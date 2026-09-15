"""Minimal JSON HTTP API for the NotebookLM worker.

Endpoints:
  GET    /healthz
  GET    /session/info
  POST   /session/probe           {"wait_seconds": 20}
  GET    /profiles
  POST   /jobs                    {"topic": "...", "sources": [...], "profile": "..."}
  GET    /jobs
  GET    /jobs/<id>
  DELETE /jobs/<id>               cancel a job that has not started
  POST   /uploads                 raw file bytes in, upload id out
  GET    /artifacts/<id>/<name>
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import shutil
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import __version__, browser, prompts
from app.models import JobStore, NotebookLMJob, normalize_sources
from app import sources as sources_mod

LOGGER = logging.getLogger("notebooklm.server")
MAX_BODY = 256 * 1024
MAX_UPLOAD = 64 * 1024 * 1024
LOG_TAIL_BYTES = 4000


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _json_response(handler, status, {"error": message})


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0 or length > MAX_BODY:
        raise ValueError("Request body must be JSON between 1 and 256 KiB.")
    raw = handler.rfile.read(length)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Request body is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Request body must be a JSON object.")
    return parsed


def _log_tail(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - LOG_TAIL_BYTES))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


class NotebookLMHandler(BaseHTTPRequestHandler):
    server_version = "NotebookLM/0.1"
    settings = None
    store: JobStore | None = None
    runner = None

    def log_message(self, fmt: str, *args) -> None:  # silence default stderr spam
        LOGGER.debug("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------ plumbing

    def _authorized(self) -> bool:
        token = getattr(self.settings, "api_token", "") or ""
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        return hmac.compare_digest(header.encode(), expected.encode())

    def _check_signed_in(self) -> bool | None:
        """Best-effort: None = unknown, True/False = session valid or not.
        A full check requires browser overhead, so this stays heuristics."""
        if str(getattr(self.settings, "session_mode", "")).lower() != "persistent":
            return None
        import os
        profile_dir = (
            getattr(self.settings, "browser_profile", None)
            or os.path.join(getattr(self.settings, "data_dir", "/data"), "notebooklm-browser-profile")
        )
        if not os.path.isdir(profile_dir):
            return None
        # Local Storage leveldb exists -> a profile was used
        ls_dir = os.path.join(profile_dir, "Default", "Local Storage", "leveldb")
        return os.path.isdir(ls_dir) or None

    def do_GET(self) -> None:  # noqa: N802 - the name is fixed by http.server
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/healthz":
            _json_response(
                self,
                200,
                {
                    "status": "ok",
                    "version": __version__,
                    "counts": self.store.counts(),
                    "enabled": bool(self.settings.enabled),
                    "session_mode": self.settings.session_mode,
                    "signed_in": self._check_signed_in(),
                },
            )
            return
        if not self._authorized():
            _error(self, 401, "Missing or invalid API token.")
            return
        if path == "/session/info":
            info = browser.session_info(self.settings)
            info["signed_in"] = "unknown"
            _json_response(self, 200, info)
            return
        if path == "/profiles":
            payload = {
                name: profile.to_dict() for name, profile in sorted(prompts.PROFILES.items())
            }
            _json_response(
                self,
                200,
                {
                    "default": self.settings.default_profile,
                    "duration_targets": dict(prompts.DURATION_TARGETS),
                    "profiles": payload,
                },
            )
            return
        if path == "/jobs":
            jobs = [job.to_dict() for job in self.store.list_jobs()]
            _json_response(self, 200, {"jobs": jobs})
            return
        if path.startswith("/jobs/"):
            job_id = path.split("/", 2)[2]
            job = self.store.get(job_id)
            if job is None:
                _error(self, 404, "Job not found.")
                return
            payload = job.to_dict()
            payload["log_tail"] = _log_tail(job.log_path)
            _json_response(self, 200, {"job": payload})
            return
        if path.startswith("/artifacts/"):
            self._artifact(path, query)
            return
        _error(self, 404, "Unknown path.")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._authorized():
            _error(self, 401, "Missing or invalid API token.")
            return
        if path == "/jobs":
            self._create_job()
            return
        if path == "/session/probe":
            self._probe(query=urllib.parse.parse_qs(parsed.query))
            return
        if path == "/uploads":
            self._upload(query=urllib.parse.parse_qs(parsed.query))
            return
        _error(self, 404, "Unknown path.")

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._authorized():
            _error(self, 401, "Missing or invalid API token.")
            return
        if path.startswith("/jobs/"):
            job_id = path.split("/", 2)[2]
            if self.store.cancel_created(job_id):
                _json_response(self, 200, {"canceled": job_id})
                return
            _error(self, 409, "Only a job that has not started can be canceled.")
            return
        _error(self, 404, "Unknown path.")

    # ------------------------------------------------------------- actions

    def _create_job(self) -> None:
        if not self.settings.enabled:
            _error(self, 409, "The NotebookLM worker is disabled.")
            return
        try:
            payload = _read_json(self)
        except ValueError as error:
            _error(self, 400, str(error))
            return
        topic = str(payload.get("topic") or "").strip()
        if not topic:
            _error(self, 400, "A topic is required.")
            return
        profile = prompts.get_profile(
            str(payload.get("profile") or ""), default=self.settings.default_profile
        )
        duration = prompts.duration_target(
            profile, str(payload.get("duration_profile") or "")
        )
        job = NotebookLMJob(
            content_id=str(payload.get("content_id") or ""),
            topic=topic,
            sources=normalize_sources(payload.get("sources")),
            profile=profile.name,
            language=profile.language,
            duration=duration,
            voice_gender=profile.voice_gender,
            style=profile.style,
            tone=profile.tone,
            audience=profile.audience,
        )
        self.store.create(job)
        LOGGER.info("Job %s queued: profile=%s", job.id, job.profile)
        _json_response(self, 201, {"job": job.to_dict()})

    def _probe(self, *, query: dict) -> None:
        try:
            payload = _read_json(self)
        except ValueError:
            payload = {}
        wait_seconds = int(
            payload.get("wait_seconds")
            or (query.get("wait_seconds") or ["15"])[0]
            or 15
        )
        probe_id = uuid.uuid4().hex[:8]
        out_dir = os.path.join(self.settings.data_dir, "probes")
        try:
            with browser.open_page(self.settings) as page:
                browser.configure_page(page, self.settings)
                page.goto(self.settings.home_url, wait_until="domcontentloaded")
                page.wait_for_timeout(max(1, wait_seconds) * 1000)
                files = browser.snapshot_page(page, out_dir, prefix=probe_id)
                signed = browser.signed_in(page)
                url = str(page.url or "")
        except Exception as error:  # noqa: BLE001 - the probe reports, it does not raise
            _json_response(
                self,
                200,
                {
                    "probe": probe_id,
                    "signed_in": False,
                    "error": f"{type(error).__name__}: {error}",
                },
            )
            return
        _json_response(
            self,
            200,
            {
                "probe": probe_id,
                "signed_in": bool(signed),
                "url": url,
                "files": files,
                "hint": (
                    "Reuse the element names in files with "
                    "NOTEBOOKLM_SELECTORS_FILE when the UI changed."
                ),
            },
        )

    def _upload(self, *, query: dict) -> None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            _error(self, 400, "Upload body must be between 1 byte and 64 MiB.")
            return
        raw = self.rfile.read(length)
        filename = (
            self.headers.get("X-Filename")
            or (query.get("name") or [""])[0]
            or "material.txt"
        )
        uploads_dir = os.path.join(self.settings.data_dir, "uploads")
        upload_id = sources_mod.store_upload(uploads_dir, filename, raw)
        LOGGER.info("Stored upload %s (%s, %d bytes)", upload_id, filename, len(raw))
        _json_response(self, 201, {"id": upload_id, "name": os.path.basename(filename)})

    def _artifact(self, path: str, query: dict) -> None:
        parts = path.split("/", 3)
        if len(parts) < 4:
            _error(self, 400, "An artifact needs a job id and a file name.")
            return
        job_id, name = parts[2], urllib.parse.unquote(parts[3])
        job = self.store.get(job_id)
        if job is None:
            _error(self, 404, "Job not found.")
            return
        videos_dir = os.path.join(self.settings.data_dir, "videos")
        allowed = {os.path.basename(job.video_path or "")} - {""}
        allowed.add(f"{job_id}.mp4")
        if os.path.basename(name) not in allowed:
            _error(self, 404, "Artifact not found.")
            return
        target = os.path.join(videos_dir, os.path.basename(name))
        if not os.path.isfile(target):
            _error(self, 404, "Artifact not found.")
            return
        size = os.path.getsize(target)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(target, "rb") as handle:
            shutil.copyfileobj(handle, self.wfile)


def serve(settings, store: JobStore, runner, *, handler=NotebookLMHandler) -> ThreadingHTTPServer:
    """Build the HTTP server (not started) for the given settings."""

    class Bound(handler):
        pass

    Bound.settings = settings
    Bound.store = store
    Bound.runner = runner
    return ThreadingHTTPServer((settings.bind_ip, settings.port), Bound)
