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

    def links(self) -> list[dict]:
        rows = []
        router_host = self.env_value("SMART_ROUTER_BIND_IP", "127.0.0.1")
        router_port = self.env_value("SMART_ROUTER_PORT", "8787")
        if router_host in {"0.0.0.0", "::"}:
            router_host = "127.0.0.1"
        rows.append(
            {
                "label": "Smart Router dashboard",
                "url": f"http://{router_host}:{router_port}/dashboard",
                "note": "Flight deck: routing policies, telemetry, and traces.",
            }
        )
        nine_host = self.env_value("NINEROUTER_BIND_IP", "127.0.0.1")
        nine_port = self.env_value("NINEROUTER_PORT", "20128")
        if nine_host in {"0.0.0.0", "::"}:
            nine_host = "127.0.0.1"
        rows.append(
            {
                "label": "9router dashboard",
                "url": f"http://{nine_host}:{nine_port}/",
                "note": "Provider keys and upstream models.",
            }
        )
        media_host = self.env_value("MEDIA_STUDIO_BIND_IP", "127.0.0.1")
        media_port = self.env_value("MEDIA_STUDIO_PORT", "8850")
        if media_host in {"0.0.0.0", "::"}:
            media_host = "127.0.0.1"
        rows.append(
            {
                "label": "Media Studio API",
                "url": f"http://{media_host}:{media_port}/openapi.json",
                "note": "Job API description (JSON).",
            }
        )
        rustfs_bind = self.env_value("RUSTFS_BIND_IP")
        if rustfs_bind:
            rustfs_host = "127.0.0.1" if rustfs_bind in {"0.0.0.0", "::"} else rustfs_bind
            rustfs_console_port = self.env_value("RUSTFS_CONSOLE_PORT", "9001")
            rows.append(
                {
                    "label": "RustFS console",
                    "url": f"http://{rustfs_host}:{rustfs_console_port}/rustfs/console/",
                    "note": "Object-storage console; the S3 API listens on port 9000.",
                }
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
