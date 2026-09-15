"""Operator panel for the content stack (standard library only).

Run it on the host, next to ``manage.sh``::

    python3 -m panel.server --root /path/to/content-manager --bind 127.0.0.1 --port 8899

The server exposes read-only stack views, validated configuration editors, and
a fixed whitelist of actions. Authentication is a single operator token that is
stored outside the repository (``data/panel/token``) and exchanged for an
HttpOnly session cookie; secrets from ``.env`` are never returned to the
browser.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import mimetypes
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from panel import __version__
from panel.actions import ActionError, ActionRunner
from panel.drafts import (
    DraftActionError,
    media_base_url,
    queue_action,
    request_instagram_refresh,
    results as draft_results,
)
from panel.editors import ConfigStore, EditError, EnvStore
from panel.platforms import PlatformStore
from panel.stack import CommandError, StackView

SESSION_COOKIE = "panel_session"
SESSION_SALT = b"panel-session-v1"
MAX_BODY_BYTES = 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent / "static"

log = logging.getLogger("panel")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env_flag(value: str, default: bool = False) -> bool:
    """Read one boolean environment value the way the bots do."""
    text = str(value or "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


class PanelApp:
    """Shared state and helpers behind the HTTP handlers."""

    def __init__(
        self,
        root: Path,
        token: str,
        *,
        actions_enabled: bool = True,
        cookie_secure: bool = False,
    ):
        self.root = Path(root).resolve()
        self.token = str(token or "")
        self.actions_enabled = bool(actions_enabled)
        self.cookie_secure = bool(cookie_secure)
        self.stack = StackView(self.root)
        self.config = ConfigStore(self.root)
        self.env = EnvStore(self.root)
        self.platforms = PlatformStore(self.root, self.env)
        self.actions = ActionRunner(self.root, enabled=self.actions_enabled)
        self.started_at = time.time()

    # ----------------------------------------------------------------- auth

    def session_value(self) -> str:
        return hmac.new(self.token.encode("utf-8"), SESSION_SALT, "sha256").hexdigest()

    def token_matches(self, candidate: str) -> bool:
        return bool(self.token) and hmac.compare_digest(str(candidate or ""), self.token)

    def session_matches(self, candidate: str) -> bool:
        # A configured token is mandatory: the session value is derived from it,
        # so an empty token would make the cookie predictable.
        if not self.token:
            return False
        return bool(candidate) and hmac.compare_digest(str(candidate), self.session_value())

    # ------------------------------------------------------------------ env

    def env_value(self, key: str, default: str = "") -> str:
        try:
            lines = (self.root / ".env").read_text(encoding="utf-8").splitlines()
        except OSError:
            return default
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            found, value = stripped.split("=", 1)
            if found.strip() == key:
                return value.strip().strip('"').strip("'")
        return default

    # ---------------------------------------------------------------- views


    # -------------------------------------------------------- NotebookLM

    def notebooklm_status(self) -> dict:
        profiles = {p.strip() for p in self.env_value("COMPOSE_PROFILES").split(",") if p.strip()}
        enabled = "notebooklm" in profiles
        status = {
            "enabled": enabled,
            "session_mode": self.env_value("NOTEBOOKLM_SESSION_MODE", "persistent"),
            "worker_running": False,
            "signed_in": None,
            "error": "",
            "google_creds_set": bool(self.env_value("NOTEBOOKLM_GOOGLE_EMAIL")),
            "google_creds_email": self.env_value("NOTEBOOKLM_GOOGLE_EMAIL", "")[:3] + "..." if self.env_value("NOTEBOOKLM_GOOGLE_EMAIL") else "",
        }
        if not enabled:
            return status
        try:
            import subprocess
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", "notebooklm-worker"],
                capture_output=True, text=True, timeout=10,
            )
            status["worker_running"] = result.stdout.strip() == "true"
        except Exception as exc:
            status["error"] = f"status: {exc}"
        if status["worker_running"]:
            try:
                import urllib.request
                resp = urllib.request.urlopen("http://notebooklm-worker:8860/healthz", timeout=5)
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    status["signed_in"] = data.get("signed_in", None)
            except Exception:
                pass
        return status

    def notebooklm_set_creds(self, email: str, password: str, totp: str = "") -> dict:
        from panel.editors import EnvStore
        store = EnvStore(self.root)
        changes = 0
        for key, value in [("NOTEBOOKLM_GOOGLE_EMAIL", email),
                           ("NOTEBOOKLM_GOOGLE_PASSWORD", password),
                           ("NOTEBOOKLM_GOOGLE_TOTP_SECRET", totp)]:
            if not value:
                continue
            try:
                store.write_entry(key, value)
                changes += 1
            except Exception:
                pass
        return {"ok": changes > 0}

    def notebooklm_run_login(self) -> dict:
        import subprocess, time
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(self.root / "docker-compose.yml"),
                 "--env-file", str(self.root / ".env"),
                 "run", "--rm", "--no-deps", "notebooklm-worker",
                 "python", "-m", "app", "login-google"],
                capture_output=True, text=True, timeout=300,
                cwd=str(self.root),
            )
            ok = result.returncode == 0
            output = result.stdout.strip()[-3000:] if result.stdout.strip() else ""
            # Restart worker so it picks up the new profile
            if ok:
                try:
                    subprocess.run(
                        ["docker", "compose", "-f", str(self.root / "docker-compose.yml"),
                         "--env-file", str(self.root / ".env"),
                         "restart", "notebooklm-worker"],
                        capture_output=True, text=True, timeout=30,
                        cwd=str(self.root),
                    )
                    output += "\nWorker restarted after login."
                except Exception as exc:
                    output += f"\nWarning: worker restart failed: {exc}"
            return {
                "ok": ok,
                "returncode": result.returncode,
                "output": output,
                "error": result.stderr.strip()[-2000:] if result.stderr.strip() else "",
                "duration": round(time.monotonic() - started, 1),
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "login timed out after 300s", "duration": 300}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "duration": round(time.monotonic() - started, 1)}

    def notebooklm_import_session(self, session_data: dict) -> dict:
        import subprocess, time, json
        data_dir = self.root / "data" / "notebooklm-worker"
        data_dir.mkdir(parents=True, exist_ok=True)
        session_path = data_dir / "import-session.json"
        try:
            session_path.write_text(json.dumps(session_data, indent=2), encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": f"save: {exc}"}
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["docker", "compose", "-f", str(self.root / "docker-compose.yml"),
                 "--env-file", str(self.root / ".env"),
                 "run", "--rm", "--no-deps", "notebooklm-worker",
                 "python", "-m", "app", "import-session", "/data/import-session.json"],
                capture_output=True, text=True, timeout=60,
                cwd=str(self.root),
            )
            return {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "output": result.stdout.strip()[-3000:] if result.stdout.strip() else "",
                "error": result.stderr.strip()[-2000:] if result.stderr.strip() else "",
                "duration": round(time.monotonic() - started, 1),
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "import timed out", "duration": 60}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "duration": round(time.monotonic() - started, 1)}

    def instagram_view(self) -> dict:
        """Instagram publishing credential state, without ever returning it."""
        token = self.env_value("INSTAGRAM_ACCESS_TOKEN")
        publisher = {
            "configured": bool(token and self.env_value("INSTAGRAM_BUSINESS_ID")),
            "token_set": bool(token),
            "business_id": self.env_value("INSTAGRAM_BUSINESS_ID"),
            "api_version": self.env_value("INSTAGRAM_API_VERSION", "v26.0") or "v26.0",
            "auto_publish": _env_flag(
                self.env_value("INSTAGRAM_AUTO_PUBLISH"), True
            ),
            "public_base_url": media_base_url(
                self.root, self.env_value("INSTAGRAM_MEDIA_PUBLIC_BASE_URL")
            ),
        }
        path = self.root / "data" / "content-bot" / "instagram-token.json"
        stored = {}
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
            if isinstance(payload, dict):
                stored = payload
        publisher.update(
            {
                "refreshed_at": str(stored.get("refreshed_at") or ""),
                "expires_at": str(stored.get("expires_at") or ""),
                "refresh_source": str(stored.get("source") or ""),
                "last_error": str(stored.get("last_error") or ""),
                "token_file": str(path),
            }
        )
        return publisher

    def state_view(self) -> dict:
        path = self.root / "data" / "content-bot" / "state.json"
        payload = {
            "exists": path.is_file(),
            "path": str(path),
            "counters": {
                "day": "",
                "published_today": 0,
                "published_total": 0,
                "drafts_total": 0,
                "daily_last_run": "",
            },
            "drafts": [],
            "routine_last_run": {},
            "routines": [],
        }
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            drafts = data.get("drafts") if isinstance(data, dict) else {}
            drafts = drafts if isinstance(drafts, dict) else {}
            rows = []
            for draft_id, record in drafts.items():
                if not isinstance(record, dict):
                    continue
                rows.append(
                    {
                        "id": str(draft_id),
                        "kind": str(record.get("kind") or ""),
                        "title": str(record.get("title") or ""),
                        "status": str(record.get("status") or ""),
                        "category": str(record.get("category") or ""),
                        "created_at": str(record.get("created_at") or ""),
                        "media": str((record.get("media") or {}).get("status") or ""),
                    }
                )
            rows.sort(key=lambda item: item["created_at"], reverse=True)
            payload["drafts"] = rows[:50]
            payload["counters"] = {
                "day": str(data.get("day") or ""),
                "published_today": int(data.get("published_today") or 0),
                "published_total": len(data.get("published") or []),
                "drafts_total": len(rows),
                "daily_last_run": str(data.get("daily_last_run") or ""),
            }
            runs = data.get("routine_last_run")
            payload["routine_last_run"] = runs if isinstance(runs, dict) else {}
        payload["routines"] = self._policy_routines()
        return payload

    def _policy_routines(self) -> list[dict]:
        path = self.root / "data" / "content-manager" / "config" / "editorial-policy.yaml"
        if not path.is_file():
            return []
        try:
            import yaml

            policy = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - the editor reports YAML problems
            return []
        routines = policy.get("routines") if isinstance(policy, dict) else None
        rows = []
        for routine in routines or []:
            if not isinstance(routine, dict):
                continue
            rows.append(
                {
                    "id": str(routine.get("id") or ""),
                    "platform": str(routine.get("platform") or ""),
                    "enabled": routine.get("enabled") is not False,
                    "cadence": str(routine.get("cadence") or "daily"),
                    "time": str(routine.get("time") or ""),
                    "weekday": str(routine.get("weekday") or ""),
                    "count": routine.get("count"),
                    "media": str(routine.get("media") or "auto"),
                }
            )
        return rows

    # ---------------------------------------------------------------- links

    def public_url(self, key: str) -> str:
        """Recorded public origin of one service, without a trailing slash.

        A service published through a reverse proxy records its host name in
        the matching ``*_PUBLIC_URL`` key, and the console links through that
        host so the link keeps working from outside the LAN. Loopback values
        are the shipped placeholders and resolve to an empty string, which
        sends the caller back to the bind address instead.
        """
        value = self.env_value(key).strip().rstrip("/")
        if not value:
            return ""
        host = value.split("//", 1)[-1].split("/", 1)[0]
        if host.startswith("["):
            host = host.split("]", 1)[0].strip("[")
        else:
            host = host.split(":", 1)[0]
        if host.lower() in {"", "localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}:
            return ""
        return value

    def links(self) -> list[dict]:
        """Service links for the overview: public host first, bind address next.

        Every row uses the recorded public origin when the operator published
        the service through a reverse proxy, and falls back to the bind
        address the browser reaches on the local network otherwise.
        """
        rows: list[dict] = []

        def add(label: str, url: str, note: str) -> None:
            rows.append({"label": label, "url": url, "note": note})

        def bind_host(key: str, fallback_key: str = "") -> str:
            host = self.env_value(key) or self.env_value(fallback_key) or "127.0.0.1"
            return "127.0.0.1" if host in {"0.0.0.0", "::"} else host

        def bind_url(bind_key: str, port_key: str, default_port: str, path: str) -> str:
            port = self.env_value(port_key, default_port) or default_port
            return f"http://{bind_host(bind_key)}:{port}{path}"

        def origin(public_key: str, bind_key: str, port_key: str, default_port: str, path: str) -> str:
            public = self.public_url(public_key)
            return f"{public}{path}" if public else bind_url(bind_key, port_key, default_port, path)

        profiles = {
            part.strip()
            for part in self.env_value("COMPOSE_PROFILES").split(",")
            if part.strip()
        }

        add(
            "Operator panel",
            origin("PANEL_PUBLIC_URL", "PANEL_BIND_IP", "PANEL_PORT", "8899", "/"),
            "This console; the operator token is required to sign in.",
        )
        add(
            "Smart Router dashboard",
            origin(
                "SMART_ROUTER_PUBLIC_URL",
                "SMART_ROUTER_BIND_IP",
                "SMART_ROUTER_PORT",
                "8787",
                "/dashboard",
            ),
            "Flight deck: routing policies, telemetry, and traces.",
        )
        if "omniroute" in profiles and "9router" not in profiles:
            add(
                "OmniRoute dashboard",
                origin(
                    "OMNIROUTE_PUBLIC_BASE_URL",
                    "OMNIROUTE_BIND_IP",
                    "OMNIROUTE_PORT",
                    "20128",
                    "/",
                ),
                "Provider keys and upstream models.",
            )
        else:
            add(
                "9router dashboard",
                origin(
                    "NINEROUTER_PUBLIC_BASE_URL",
                    "NINEROUTER_BIND_IP",
                    "NINEROUTER_PORT",
                    "20128",
                    "/",
                ),
                "Provider keys and upstream models.",
            )
        add(
            "Media Studio API",
            origin(
                "MEDIA_STUDIO_PUBLIC_URL",
                "MEDIA_STUDIO_BIND_IP",
                "MEDIA_STUDIO_PORT",
                "8850",
                "/openapi.json",
            ),
            "Job API description (JSON).",
        )
        if "n8n" in profiles:
            add(
                "n8n editor",
                origin("N8N_PUBLIC_URL", "N8N_BIND_IP", "N8N_PORT", "5678", "/"),
                "Workflow editor and MCP endpoints.",
            )
        media_base = media_base_url(
            self.root, self.env_value("INSTAGRAM_MEDIA_PUBLIC_BASE_URL")
        )
        if media_base:
            add(
                "Instagram media host",
                media_base,
                "Public address the Meta Graph API downloads media from.",
            )
        rustfs_bind = self.env_value("RUSTFS_BIND_IP")
        if rustfs_bind:
            # S3_PUBLIC_CONSOLE_URL records the console path as well, so the
            # recorded value is the link and only the bind fallback needs the
            # default "/rustfs/console/" path.
            console_public = self.public_url("S3_PUBLIC_CONSOLE_URL")
            console_port = self.env_value("RUSTFS_CONSOLE_PORT", "9001") or "9001"
            console_url = console_public or (
                "http://"
                + bind_host("RUSTFS_CONSOLE_BIND_IP", "RUSTFS_BIND_IP")
                + f":{console_port}/rustfs/console/"
            )
            if not console_url.endswith("/"):
                console_url = f"{console_url}/"
            add(
                "RustFS console",
                console_url,
                "Object-storage console; the S3 API listens on port 9000.",
            )
        return rows

    def media_jobs(self) -> dict:
        # The panel shares the stack network, so the request has to use the
        # service name and the container port: the published host bind in
        # MEDIA_STUDIO_BIND_IP is what the operator's browser reaches, not what
        # this process can dial. MEDIA_STUDIO_INTERNAL_URL overrides the guess
        # for deployments where the service is named or addressed differently.
        port = self.env_value("MEDIA_STUDIO_PORT", "8850")
        base = (
            self.env_value("MEDIA_STUDIO_INTERNAL_URL") or f"http://media-studio:{port}"
        ).rstrip("/")
        token = self.env_value("MEDIA_STUDIO_API_TOKEN", "")
        request = urllib.request.Request(
            f"{base}/jobs",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, socket.timeout) as error:
            return {"ok": False, "error": str(error), "jobs": []}
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        rows = []
        for job in (jobs or [])[:30]:
            if not isinstance(job, dict):
                continue
            rows.append(
                {
                    "id": str(job.get("id") or ""),
                    "driver": str(job.get("driver") or ""),
                    "status": str(job.get("status") or ""),
                    "created_at": str(job.get("created_at") or ""),
                    "error": str(job.get("error") or "")[:160],
                    "artifacts": [
                        str(item.get("name"))
                        for item in (job.get("artifacts") or [])
                        if isinstance(item, dict) and item.get("name")
                    ],
                }
            )
        return {"ok": True, "error": "", "jobs": rows}


class PanelHandler(BaseHTTPRequestHandler):
    server_version = f"content-panel/{__version__}"
    app: PanelApp

    # ------------------------------------------------------------ utilities

    def log_message(self, fmt: str, *args) -> None:  # keep container logs tidy
        log.info("%s - %s", self.address_string(), fmt % args)

    def _cookie_session(self) -> str:
        header = self.headers.get("Cookie") or ""
        if not header:
            return ""
        try:
            cookie = SimpleCookie()
            cookie.load(header)
        except Exception:  # noqa: BLE001
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _authenticated(self) -> bool:
        return self.app.session_matches(self._cookie_session())

    def _send_json(self, status: int, payload: dict, *, cookie: str = "") -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise EditError("request body is too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError as error:
            raise EditError(f"invalid JSON body: {error}") from error
        if not isinstance(payload, dict):
            raise EditError("invalid JSON body: expected an object")
        return payload

    def _require_csrf(self) -> None:
        if self.headers.get("X-Panel-Csrf") != "1":
            raise PermissionError("missing X-Panel-Csrf header")

    def _session_cookie(self, value: str) -> str:
        parts = [f"{SESSION_COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if value == "":
            parts.append("Max-Age=0")
        if self.app.cookie_secure:
            parts.append("Secure")
        return "; ".join(parts)

    # ------------------------------------------------------------- dispatch

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        try:
            if path == "/healthz":
                self._send_json(HTTPStatus.OK, {"ok": True, "version": __version__})
                return
            if path.startswith("/static/"):
                self._serve_static(path)
                return
            if path == "/" or path == "/index.html":
                self._serve_index()
                return
            if method == "POST" and path == "/api/login":
                self._login()
                return
            if method == "POST" and path == "/api/logout":
                self._send_json(HTTPStatus.OK, {"ok": True}, cookie=self._session_cookie(""))
                return
            if not path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self._authenticated():
                self._error(HTTPStatus.UNAUTHORIZED, "authentication required")
                return
            self._api(method, path, query)
        except PermissionError as error:
            self._error(HTTPStatus.FORBIDDEN, str(error))
        except (EditError, DraftActionError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except ActionError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except CommandError as error:
            self._error(HTTPStatus.BAD_GATEWAY, str(error))
        except BrokenPipeError:
            return
        except Exception:  # noqa: BLE001
            log.exception("panel request failed: %s %s", method, path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def _api(self, method: str, path: str, query: str) -> None:
        parts = [part for part in path.split("/") if part][1:]  # drop "api"
        if method == "GET" and parts == ["session"]:
            self._send_json(
                HTTPStatus.OK,
                {
                    "authenticated": True,
                    "actions_enabled": self.app.actions_enabled,
                    "root": str(self.app.root),
                    "version": __version__,
                    "started_at": self.app.started_at,
                },
            )
            return
        if method == "GET" and parts == ["status"]:
            self._send_json(HTTPStatus.OK, self._status_payload())
            return
        if method == "GET" and parts == ["state"]:
            self._send_json(HTTPStatus.OK, self.app.state_view())
            return
        if method == "GET" and parts == ["links"]:
            self._send_json(HTTPStatus.OK, {"links": self.app.links()})
            return
        if method == "GET" and parts == ["media", "jobs"]:
            self._send_json(HTTPStatus.OK, self.app.media_jobs())
            return
        if method == "GET" and parts == ["storage"]:
            self._send_json(HTTPStatus.OK, self.app.stack.storage())
            return
        if method == "GET" and parts == ["backups"]:
            self._send_json(HTTPStatus.OK, self.app.stack.backups())
            return
        if method == "GET" and parts == ["actions"]:
            self._send_json(
                HTTPStatus.OK,
                {
                    "enabled": self.app.actions_enabled,
                    "actions": self.app.actions.listing(),
                },
            )
            return
        if method == "GET" and parts == ["notebooklm"]:
            self._send_json(HTTPStatus.OK, self.app.notebooklm_status())
            return
        if method == "POST" and parts == ["notebooklm", "creds"]:
            self._require_csrf()
            body = self._read_body()
            result = self.app.notebooklm_set_creds(
                email=str(body.get("email", "")),
                password=str(body.get("password", "")),
                totp=str(body.get("totp", "")),
            )
            self._send_json(HTTPStatus.OK, result)
            return
        if method == "POST" and parts == ["notebooklm", "login"]:
            self._require_csrf()
            result = self.app.notebooklm_run_login()
            self._send_json(HTTPStatus.OK, result)
            return
        if method == "POST" and parts == ["notebooklm", "import-session"]:
            self._require_csrf()
            body = self._read_body()
            session_data = body.get("session", {})
            result = self.app.notebooklm_import_session(session_data)
            self._send_json(HTTPStatus.OK, result)
            return
        if method == "GET" and parts == ["config"]:
            self._send_json(HTTPStatus.OK, {"files": self.app.config.listing()})
            return
        if len(parts) == 2 and parts[0] == "config" and method == "GET":
            self._send_json(HTTPStatus.OK, self.app.config.read(parts[1]))
            return
        if len(parts) == 2 and parts[0] == "config" and method == "PUT":
            self._require_csrf()
            body = self._read_body()
            result = self.app.config.write(parts[1], body.get("text", ""))
            self._send_json(HTTPStatus.OK, result)
            return
        if len(parts) == 3 and parts[0] == "config" and parts[2] == "backups" and method == "GET":
            self._send_json(HTTPStatus.OK, {"backups": self.app.config.backups(parts[1])})
            return
        if len(parts) == 3 and parts[0] == "config" and parts[2] == "seed" and method == "POST":
            self._require_csrf()
            self._read_body()
            self._send_json(HTTPStatus.OK, self.app.config.seed(parts[1]))
            return
        if len(parts) == 3 and parts[0] == "config" and parts[2] == "restore" and method == "POST":
            self._require_csrf()
            body = self._read_body()
            self._send_json(
                HTTPStatus.OK,
                self.app.config.restore(parts[1], str(body.get("backup") or "")),
            )
            return
        if method == "GET" and parts == ["drafts"]:
            payload = self.app.state_view()
            payload["results"] = draft_results(self.app.root)
            self._send_json(HTTPStatus.OK, payload)
            return
        if len(parts) == 3 and parts[0] == "drafts" and parts[2] == "action" and method == "POST":
            self._require_csrf()
            body = self._read_body()
            request = queue_action(self.app.root, parts[1], body.get("action", ""))
            self._send_json(HTTPStatus.OK, {"queued": True, "request": request})
            return
        if method == "GET" and parts == ["instagram"]:
            self._send_json(HTTPStatus.OK, self.app.instagram_view())
            return
        if parts == ["instagram", "refresh"] and method == "POST":
            self._require_csrf()
            self._read_body()
            request_instagram_refresh(self.app.root)
            self._send_json(HTTPStatus.OK, {"queued": True})
            return
        if method == "GET" and parts == ["platforms"]:
            self._send_json(HTTPStatus.OK, self.app.platforms.view())
            return
        if len(parts) == 2 and parts[0] == "platforms" and method == "PUT":
            self._require_csrf()
            body = self._read_body()
            result = self.app.platforms.update(parts[1], body.get("values") or {})
            result["restart_hint"] = (
                f"Saved to .env. Run Apply changes (docker compose up -d) so "
                f"{result.get('service') or 'the containers'} reads the new values."
            )
            self._send_json(HTTPStatus.OK, result)
            return
        if len(parts) == 3 and parts[0] == "platforms" and parts[2] == "test" and method == "POST":
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.platforms.test(parts[1], body.get("values") or {}))
            return
        if method == "GET" and parts == ["env"]:
            self._send_json(HTTPStatus.OK, {"entries": self.app.env.entries()})
            return
        if len(parts) == 2 and parts[0] == "env" and method == "PUT":
            self._require_csrf()
            body = self._read_body()
            result = self.app.env.set(body.get("key", ""), body.get("value", ""))
            result["restart_hint"] = (
                "Run the Apply changes action (docker compose up -d) so containers "
                "pick up the new environment."
            )
            self._send_json(HTTPStatus.OK, result)
            return
        if len(parts) == 2 and parts[0] == "logs" and method == "GET":
            lines = 200
            for chunk in query.split("&"):
                if chunk.startswith("lines="):
                    digits = re.sub(r"\D", "", chunk.split("=", 1)[1])
                    lines = max(10, min(int(digits or "200"), 2000))
            self._send_json(
                HTTPStatus.OK,
                {"service": parts[1], "lines": self.app.stack.logs(parts[1], lines=lines)},
            )
            return
        if len(parts) == 2 and parts[0] == "actions" and method == "POST":
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.actions.run_with(parts[1], body))
            return
        self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def _status_payload(self) -> dict:
        payload = self.app.stack.snapshot()
        payload["generated_at"] = _utc_now()
        payload["version"] = __version__
        payload["actions_enabled"] = self.app.actions_enabled
        payload["state"] = self.app.state_view()["counters"]
        return payload

    def _login(self) -> None:
        body = self._read_body()
        candidate = str(body.get("token") or "")
        if not self.app.token:
            self._error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "no panel token configured; run ./manage.sh panel token to create one",
            )
            return
        if not self.app.token_matches(candidate):
            self._error(HTTPStatus.UNAUTHORIZED, "invalid token")
            return
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            cookie=self._session_cookie(self.app.session_value()),
        )

    # -------------------------------------------------------------- static

    def _serve_index(self) -> None:
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "static assets are missing")
            return
        body = index.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; img-src 'self' data:; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        name = Path(path).name
        target = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def build_app(root: Path, token: str, *, actions_enabled: bool, cookie_secure: bool) -> PanelApp:
    return PanelApp(
        root, token, actions_enabled=actions_enabled, cookie_secure=cookie_secure
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Content stack operator panel")
    parser.add_argument("--root", default=os.environ.get("PANEL_ROOT", os.getcwd()))
    parser.add_argument("--bind", default=os.environ.get("PANEL_BIND_IP", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PANEL_PORT", "8899")))
    parser.add_argument("--token-file", default=os.environ.get("PANEL_TOKEN_FILE", ""))
    parser.add_argument("--token", default=os.environ.get("PANEL_TOKEN", ""))
    parser.add_argument(
        "--no-actions",
        action="store_true",
        default=str(os.environ.get("PANEL_ACTIONS_ENABLED", "true")).lower()
        in {"0", "false", "no", "off"},
    )
    parser.add_argument(
        "--cookie-secure",
        action="store_true",
        default=str(os.environ.get("PANEL_COOKIE_SECURE", "false")).lower()
        in {"1", "true", "yes", "on"},
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    token = args.token
    if not token and args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            print(f"panel: cannot read token file: {error}", file=sys.stderr)
            return 2
    if not token:
        print(
            "panel: no token provided; pass --token-file or run ./manage.sh panel token",
            file=sys.stderr,
        )
        return 2

    app = build_app(
        Path(args.root),
        token,
        actions_enabled=not args.no_actions,
        cookie_secure=args.cookie_secure,
    )
    server = ThreadingHTTPServer((args.bind, args.port), PanelHandler)
    server.app = app  # type: ignore[attr-defined]
    PanelHandler.app = app
    log.info(
        "panel listening on http://%s:%s (root=%s, actions=%s)",
        args.bind,
        args.port,
        app.root,
        "enabled" if app.actions_enabled else "disabled",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("panel stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
