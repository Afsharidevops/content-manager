"""Minimal JSON HTTP API for submitting and inspecting media jobs.

Endpoints:
  GET  /healthz
  GET  /session/info
  POST /session/probe            {"driver": "flow-video", "wait_seconds": 25}
  POST /jobs                     {"driver": "...", "prompt": "...", "params": {}}
  GET  /jobs
  GET  /jobs/<id>
  DELETE /jobs/<id>              cancel while queued
  GET  /artifacts/<id>/<name>
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from media_studio.drivers import DRIVERS, PROBE
from media_studio.runner import JobQueue
from media_studio.state import StateStore

LOGGER = logging.getLogger("media_studio.server")
MAX_BODY = 512 * 1024


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    if length <= 0 or length > MAX_BODY:
        raise ValueError("Request body must be JSON between 1 and 512 KiB.")
    raw = handler.rfile.read(length)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Request body is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Request body must be a JSON object.")
    return parsed


class MediaStudioHandler(BaseHTTPRequestHandler):
    server_version = "MediaStudio/0.1"
    settings = None
    state: StateStore | None = None
    queue: JobQueue | None = None

    def log_message(self, fmt: str, *args) -> None:  # silence default stderr spam
        LOGGER.debug("%s %s", self.address_string(), fmt % args)

    def _authorized(self) -> bool:
        token = getattr(self.settings, "api_token", "") or ""
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {token}"
        return hmac.compare_digest(header.encode(), expected.encode())

    def _route(self, parts: list[str]) -> None:
        method = self.command
        if method == "GET" and parts == ["healthz"]:
            counts = self.state.counts()
            _json_response(self, 200, {"ok": True, **counts})
            return
        if not self._authorized():
            _json_response(self, 401, {"error": "Unauthorized. Send Authorization: Bearer <token>."})
            return
        if method == "GET" and parts == ["session", "info"]:
            settings = self.settings
            _json_response(
                self,
                200,
                {
                    "version": "0.1.0",
                    "session_mode": settings.session_mode,
                    "cdp_url": settings.cdp_url if settings.session_mode == "cdp" else "",
                    "drivers": [
                        {
                            "name": meta.name,
                            "label": meta.label,
                            "group": meta.group,
                            "target_url": meta.target_url,
                        }
                        for meta in DRIVERS.values()
                        if meta.name in settings.drivers
                    ],
                    "writer_configured": bool(settings.writer_base_url),
                },
            )
            return
        if method == "POST" and parts == ["session", "probe"]:
            try:
                body = _read_json(self)
            except ValueError as exc:
                _json_response(self, 400, {"error": str(exc)})
                return
            try:
                job = self.queue.submit(PROBE, "", {"driver": str(body.get("driver", "")), "wait_seconds": int(body.get("wait_seconds", 25))})
            except ValueError as exc:
                _json_response(self, 400, {"error": str(exc)})
                return
            _json_response(self, 202, {"job": job.to_dict()})
            return
        if method == "POST" and parts == ["jobs"]:
            try:
                body = _read_json(self)
                job = self.queue.submit(
                    str(body.get("driver", "")),
                    str(body.get("prompt", "")),
                    dict(body.get("params") or {}),
                )
            except ValueError as exc:
                _json_response(self, 400, {"error": str(exc)})
                return
            _json_response(self, 202, {"job": job.to_dict()})
            return
        if method == "GET" and parts == ["jobs"]:
            jobs = [job.to_dict() for job in self.state.list_jobs(100)]
            _json_response(self, 200, {"jobs": jobs})
            return
        if len(parts) >= 2 and parts[0] == "jobs" and method == "GET":
            job = self.state.get(parts[1])
            if not job:
                _json_response(self, 404, {"error": "Job not found."})
                return
            payload = job.to_dict()
            payload["log_tail"] = self._log_tail(job.log_path)
            _json_response(self, 200, {"job": payload})
            return
        if len(parts) == 2 and parts[0] == "jobs" and method == "DELETE":
            if not self.state.cancel_queued(parts[1]):
                job = self.state.get(parts[1])
                if job and job.status == "queued":
                    _json_response(self, 404, {"error": "Job not found."})
                else:
                    _json_response(self, 409, {"error": "Only queued jobs can be canceled."})
                return
            _json_response(self, 200, {"ok": True})
            return
        if len(parts) == 3 and parts[0] == "artifacts" and method == "GET":
            job = self.state.get(parts[1])
            name = os.path.basename(parts[2])
            if not job or not name or name != parts[2]:
                _json_response(self, 404, {"error": "Artifact not found."})
                return
            path = os.path.join(self.queue.artifact_root, job.id, name)
            if not os.path.isfile(path):
                _json_response(self, 404, {"error": "Artifact not found."})
                return
            self._serve_file(path, name)
            return
        _json_response(self, 404, {"error": f"Unknown route: {self.command} /{'/'.join(parts)}"})

    def _log_tail(self, log_path: str) -> str:
        if not log_path or not os.path.isfile(log_path):
            return ""
        try:
            with open(log_path, encoding="utf-8") as handle:
                lines = handle.readlines()
            return "".join(lines[-40:])
        except OSError:
            return ""

    def _serve_file(self, path: str, name: str) -> None:
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            with open(path, "rb") as handle:
                content = handle.read()
        except OSError:
            _json_response(self, 404, {"error": "Artifact not found."})
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        try:
            self._route(parts)
        except BrokenPipeError:
            pass
        except Exception:  # noqa: BLE001
            LOGGER.exception("Request failed: %s %s", method, self.path)
            try:
                _json_response(self, 500, {"error": "Internal server error."})
            except OSError:
                pass


def build_handler(settings, state: StateStore, queue: JobQueue):
    """Return a request-handler class bound to one API instance."""

    class BoundMediaStudioHandler(MediaStudioHandler):
        pass

    BoundMediaStudioHandler.settings = settings
    BoundMediaStudioHandler.state = state
    BoundMediaStudioHandler.queue = queue
    return BoundMediaStudioHandler


def serve(settings, state: StateStore, queue: JobQueue) -> None:
    server = ThreadingHTTPServer((settings.bind_ip, settings.port), build_handler(settings, state, queue))
    LOGGER.info("Media Studio API listening on %s:%s", settings.bind_ip, settings.port)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
