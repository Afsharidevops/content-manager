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
from panel.storyboards import StoryboardError, StoryboardMissing, StoryboardStore

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


def quote_id(value: str) -> str:
    """Percent-encode one path segment for an upstream service URL."""
    from urllib.parse import quote

    return quote(str(value or ""), safe="")


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
        self.storyboards = StoryboardStore(self.root)
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
            "google_creds_email": (self.env_value("NOTEBOOKLM_GOOGLE_EMAIL", "") or "").split("@")[0] + "@..." if self.env_value("NOTEBOOKLM_GOOGLE_EMAIL") else "",
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

    def notebooklm_profiles(self) -> dict:
        """Fetch profiles + duration targets from the NotebookLM worker."""
        try:
            import urllib.request
            from panel.editors import EnvStore
            env = EnvStore(self.root)
            token = env.value("NOTEBOOKLM_API_TOKEN", "")
            req = urllib.request.Request("http://notebooklm-worker:8860/profiles")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            resp = urllib.request.urlopen(req, timeout=10)
            if resp.status == 200:
                return json.loads(resp.read().decode())
            return {"ok": False, "error": f"worker returned HTTP {resp.status}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def notebooklm_save_profiles(self, body: dict) -> dict:
        """Save profiles + duration targets to the NotebookLM worker."""
        import json as _j
        try:
            import urllib.request
            from panel.editors import EnvStore
            env = EnvStore(self.root)
            token = env.value("NOTEBOOKLM_API_TOKEN", "")
            payload = {}
            if "profiles" in body and isinstance(body["profiles"], dict):
                payload["profiles"] = body["profiles"]
            if "duration_targets" in body and isinstance(body["duration_targets"], dict):
                payload["duration_targets"] = body["duration_targets"]
            data = _j.dumps(payload).encode()
            req = urllib.request.Request(
                "http://notebooklm-worker:8860/profiles",
                data=data, method="PUT",
                headers={"Content-Type": "application/json"},
            )
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            resp = urllib.request.urlopen(req, timeout=15)
            if resp.status == 200:
                return _j.loads(resp.read().decode())
            return {"ok": False, "error": f"worker returned HTTP {resp.status}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def notebooklm_set_creds(self, email: str, password: str, totp: str = "") -> dict:
        import logging
        log = logging.getLogger("panel.notebooklm")
        from panel.editors import EnvStore
        store = EnvStore(self.root)
        changes = 0
        pairs = [("NOTEBOOKLM_GOOGLE_EMAIL", email),
                 ("NOTEBOOKLM_GOOGLE_PASSWORD", password),
                 ("NOTEBOOKLM_GOOGLE_TOTP_SECRET", totp)]
        for key, value in pairs:
            if not value:
                continue
            try:
                store.set(key, value)
                changes += 1
                log.info("Saved %s to .env (value present=%s)", key, bool(value))
            except Exception as exc:
                log.warning("Failed to save %s: %s", key, exc)
        # Verify by re-reading
        saved_email = self.env_value("NOTEBOOKLM_GOOGLE_EMAIL", "")
        saved_pass = self.env_value("NOTEBOOKLM_GOOGLE_PASSWORD", "")
        log.info(
            "Credential verification: email_present=%s password_present=%s",
            bool(saved_email), bool(saved_pass),
        )
        return {"ok": bool(saved_email) and bool(saved_pass), "email_set": bool(saved_email),
                "password_set": bool(saved_pass), "totp_set": bool(self.env_value("NOTEBOOKLM_GOOGLE_TOTP_SECRET", ""))}

    def notebooklm_run_login(self) -> dict:
        import subprocess, time, logging
        log = logging.getLogger("panel.notebooklm")
        started = time.monotonic()
        # Read credentials from .env so they are passed as -e flags
        email = self.env_value("NOTEBOOKLM_GOOGLE_EMAIL", "")
        password = self.env_value("NOTEBOOKLM_GOOGLE_PASSWORD", "")
        totp = self.env_value("NOTEBOOKLM_GOOGLE_TOTP_SECRET", "")
        log.info(
            "Running login-google: email_present=%s password_present=%s totp_present=%s",
            bool(email), bool(password), bool(totp),
        )
        try:
            cmd = [
                "docker", "compose", "-f", str(self.root / "docker-compose.yml"),
                "--env-file", str(self.root / ".env"),
                "run", "--rm", "--no-deps",
            ]
            if email:
                cmd += ["-e", f"NOTEBOOKLM_GOOGLE_EMAIL={email}"]
            if password:
                cmd += ["-e", f"NOTEBOOKLM_GOOGLE_PASSWORD={password}"]
            if totp:
                cmd += ["-e", f"NOTEBOOKLM_GOOGLE_TOTP_SECRET={totp}"]
            cmd += ["notebooklm-worker", "python", "-m", "app", "login-google"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(self.root))
            ok = result.returncode == 0
            output = result.stdout.strip()[-3000:] if result.stdout.strip() else ""
            # Restart worker after login so it picks up the new profile
            if ok:
                try:
                    subprocess.run(
                        ["docker", "compose", "-f", str(self.root / "docker-compose.yml"),
                         "--env-file", str(self.root / ".env"),
                         "restart", "notebooklm-worker"],
                        capture_output=True, text=True, timeout=30, cwd=str(self.root),
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

    def notebooklm_start_vnc_login(self, client_host: str = "") -> dict:
        import subprocess, time, logging, socket
        log = logging.getLogger("panel.notebooklm")
        # Detect host IP for the VNC URL
        vnc_host = self.env_value("NOTEBOOKLM_PUBLIC_URL", "")
        if not vnc_host and client_host:
            # Use the Host header from the user's HTTP request (most reliable)
            vnc_host = client_host.split(":")[0].strip()
        if not vnc_host:
            bind_ip = self.env_value("PANEL_BIND_IP", "0.0.0.0")
            if bind_ip in ("0.0.0.0", "::", "127.0.0.1", ""):
                try:
                    _tmp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    _tmp.settimeout(3)
                    _tmp.connect(("8.8.8.8", 80))
                    bind_ip = _tmp.getsockname()[0]
                    _tmp.close()
                except Exception:
                    bind_ip = socket.gethostbyname(socket.gethostname())
            vnc_host = bind_ip
        vnc_host = vnc_host.rstrip("/").replace("http://", "").replace("https://", "").split(":")[0].split("/")[0]
        # Stop main worker
        try:
            subprocess.run(
                ["docker", "compose", "-f", str(self.root / "docker-compose.yml"),
                 "--env-file", str(self.root / ".env"),
                 "stop", "notebooklm-worker"],
                capture_output=True, text=True, timeout=30, cwd=str(self.root),
            )
        except Exception as exc:
            log.warning("stop worker: %s", exc)
        # Start login container
        try:
            # Use direct docker run for proper port exposure and entrypoint override
            image = self.env_value("NOTEBOOKLM_WORKER_IMAGE_REPOSITORY", "afsharidevops/notebooklm-worker")
            tag = self.env_value("NOTEBOOKLM_WORKER_IMAGE_TAG", "0.1.0")
            data_dir = str(self.root / "data" / "notebooklm-worker")
            # Inline VNC command (verified working)
            import shlex
            vnc_cmd = (
                "CHROME=$(python3 -c "
                "'from playwright.sync_api import sync_playwright; "
                "p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()'"
                "); "
                "rm -f /data/.login-done; "
                "mkdir -p /data/notebooklm-browser-profile; chmod 777 /data/notebooklm-browser-profile; "
                "rm -f /data/notebooklm-browser-profile/Singleton*; "
                "rm -rf /data/notebooklm-browser-profile/.com.google.Chrome*; "
                "find /data/notebooklm-browser-profile -name 'LOCK' -delete 2>/dev/null || true; "
                "Xvfb :99 -screen 0 1280x1024x24 & "
                "sleep 2; "
                "fluxbox -display :99 2>/dev/null & "
                "sleep 1; "
                "x11vnc -display :99 -forever -nopw -quiet -rfbport 5900 & "
                "sleep 2; "
                "DISPLAY=:99 $CHROME --no-sandbox "
                "--disable-blink-features=AutomationControlled "
                "--disable-dev-shm-usage "
                "--window-size=1280,1024 " 
                "--window-position=0,0 "
                "--user-data-dir=/data/notebooklm-browser-profile "
                "--disable-gpu "
                "--no-first-run --no-default-browser-check "
                "--disable-component-update "
                "https://notebooklm.google.com/ & "
                "sleep 4; "
                "websockify --web /opt/novnc 8861 localhost:5900 & "
                "sleep 2; "
                "echo READY http://0.0.0.0:8861/vnc.html; "
                "while [ ! -f /data/.login-done ]; do sleep 2; done"
            )
            subprocess.run(
                ["docker", "run", "-d", "--rm",
                 "--name", "notebooklm-vnc-login",
                 "-p", "8861:8861",
                 "-v", f"{data_dir}:/data",
                 "--shm-size", "1gb",
                 f"{image}:{tag}",
                 "bash", "-c", vnc_cmd],
                capture_output=True, text=True, timeout=30, cwd=str(self.root),
            )
            time.sleep(8)
            vnc_url = f"http://{vnc_host}:8861/vnc.html"
            return {
                "ok": True,
                "vnc_url": vnc_url,
                "note": "Open this URL in a new tab, click Connect, then sign in to Google. "
                        "If the IP is wrong, set NOTEBOOKLM_PUBLIC_URL in .env.",
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def notebooklm_stop_vnc_login(self) -> dict:
        import subprocess, time, logging
        log = logging.getLogger("panel.notebooklm")
        data_dir = self.root / "data" / "notebooklm-worker"
        # Signal the login container to stop
        try:
            (data_dir / ".login-done").write_text("done")
            time.sleep(3)
        except Exception:
            pass
        # Force remove if still running
        try:
            subprocess.run(
                ["docker", "rm", "-f", "notebooklm-vnc-login"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass
        # Restart the main worker
        try:
            subprocess.run(
                ["docker", "compose", "-f", str(self.root / "docker-compose.yml"),
                 "--env-file", str(self.root / ".env"),
                 "start", "notebooklm-worker"],
                capture_output=True, text=True, timeout=30, cwd=str(self.root),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "note": "Worker restarted with the saved session."}

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

    def _media_studio_base(self) -> str:
        # The panel shares the stack network, so the request has to use the
        # service name and the container port: the published host bind in
        # MEDIA_STUDIO_BIND_IP is what the operator's browser reaches, not what
        # this process can dial. MEDIA_STUDIO_INTERNAL_URL overrides the guess
        # for deployments where the service is named or addressed differently.
        port = self.env_value("MEDIA_STUDIO_PORT", "8850")
        return (
            self.env_value("MEDIA_STUDIO_INTERNAL_URL") or f"http://media-studio:{port}"
        ).rstrip("/")

    def _media_studio_token(self) -> str:
        return self.env_value("MEDIA_STUDIO_API_TOKEN", "")

    def media_studio_request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout: int = 30,
        binary: bool = False,
        tolerate_error: bool = False,
    ) -> dict | bytes:
        """Call the Media Studio API from inside the stack network.

        With ``tolerate_error`` a rejected request returns the service's own
        error document instead of raising, which is what the timeline
        validator needs: it reports the offending field to the operator.
        """
        base = self._media_studio_base()
        token = self._media_studio_token()
        body = None
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            f"{base}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            try:
                detail = error.read().decode("utf-8", "replace")
            except OSError:
                detail = ""
            if tolerate_error:
                try:
                    document = json.loads(detail)
                except ValueError:
                    document = None
                if isinstance(document, dict):
                    return document
            raise CommandError(
                f"Media Studio rejected the request ({error.code}): {detail[:400]}"
            ) from error
        except (urllib.error.URLError, socket.timeout) as error:
            raise CommandError(f"Media Studio is unreachable: {error}") from error
        if binary:
            return raw
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as error:
            raise CommandError(f"Media Studio returned invalid JSON: {error}") from error

    def _router_base(self) -> str:
        # Same addressing rule as Media Studio: the browser reaches the
        # published bind, this process dials the service name and the
        # container port, not the host port from SMART_ROUTER_PORT.
        return (
            self.env_value("SMART_ROUTER_INTERNAL_URL") or "http://smart-router:8080"
        ).rstrip("/")

    def router_console_url(self) -> str:
        """Public address of the router's own console for the operator's browser."""
        explicit = self.env_value("SMART_ROUTER_CONSOLE_URL")
        if explicit:
            return explicit.rstrip("/")
        host = self.env_value("SMART_ROUTER_BIND_IP", "127.0.0.1")
        if host in {"0.0.0.0", "::", "::0"}:
            host = "127.0.0.1"
        port = self.env_value("SMART_ROUTER_PORT", "8787")
        return f"http://{host}:{port}"

    def _router_key(self) -> str:
        return self.env_value("SMART_ROUTER_ADMIN_API_KEY") or self.env_value(
            "SMART_ROUTER_CLIENT_API_KEY"
        )

    def router_request(
        self, method: str, path: str, payload: dict | None = None, *, timeout: int = 300
    ) -> dict:
        """Call the Hermes Smart Router content API."""
        base = self._router_base()
        key = self._router_key()
        if not key:
            raise CommandError(
                "SMART_ROUTER_ADMIN_API_KEY is not set, so the panel cannot call the router."
            )
        body = None
        headers = {"Authorization": f"Bearer {key}"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            f"{base}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:400]
            except OSError:
                detail = ""
            raise CommandError(
                f"the router rejected the request ({error.code}): {detail}"
            ) from error
        except (urllib.error.URLError, socket.timeout) as error:
            raise CommandError(f"the router is unreachable: {error}") from error
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError as error:
            raise CommandError(f"the router returned invalid JSON: {error}") from error

    @staticmethod
    def _render_job_row(job: dict) -> dict:
        return {
            "id": str(job.get("id") or ""),
            "driver": str(job.get("driver") or ""),
            "prompt": str(job.get("prompt") or "")[:200],
            "status": str(job.get("status") or ""),
            "created_at": str(job.get("created_at") or ""),
            "started_at": str(job.get("started_at") or ""),
            "finished_at": str(job.get("finished_at") or ""),
            "error": str(job.get("error") or "")[:400],
            "params": job.get("params") if isinstance(job.get("params"), dict) else {},
            "artifacts": [
                {
                    "name": str(item.get("name") or ""),
                    "kind": str(item.get("kind") or ""),
                    "size": int(item.get("size") or 0),
                }
                for item in (job.get("artifacts") or [])
                if isinstance(item, dict) and item.get("name")
            ],
        }

    def media_jobs(self) -> dict:
        """Recent Media Studio jobs for the overview card."""
        try:
            payload = self.media_studio_request("GET", "/jobs", timeout=6)
        except (CommandError, urllib.error.URLError, ValueError, socket.timeout, OSError) as error:
            return {"ok": False, "error": str(error), "jobs": []}
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        rows = [
            self._render_job_row(job) for job in (jobs or []) if isinstance(job, dict)
        ][:30]
        return {"ok": True, "error": "", "jobs": rows}

    def media_studio_drivers(self) -> tuple[list[str], str]:
        """Drivers the worker loaded, asked from the worker itself.

        ``.env`` is only the input the compose service is given; the worker
        can be running a different list (an older container, an override, a
        compose default), and the console must report what will actually run
        a job. The environment file is the fallback for an unreachable worker.
        """
        fallback = [
            part.strip()
            for part in self.env_value("MEDIA_STUDIO_DRIVERS", "").split(",")
            if part.strip()
        ]
        try:
            session = self.media_studio_request("GET", "/session/info", timeout=6)
        except (CommandError, urllib.error.URLError, ValueError, socket.timeout, OSError):
            return fallback, "env"
        if not isinstance(session, dict):
            return fallback, "env"
        loaded = [
            str(entry.get("name") or "")
            for entry in (session.get("drivers") or [])
            if isinstance(entry, dict) and entry.get("name")
        ]
        if not loaded:
            return fallback, "env"
        return loaded, "worker"

    def video_studio(self) -> dict:
        """Return the render jobs and the configuration the view needs."""
        drivers, drivers_source = self.media_studio_drivers()
        router_key = bool(self._router_key())
        try:
            payload = self.media_studio_request("GET", "/jobs", timeout=8)
        except (CommandError, urllib.error.URLError, ValueError, socket.timeout, OSError) as error:
            return {
                "ok": False,
                "error": str(error),
                "jobs": [],
                "timeline_driver": "timeline-video" in drivers,
                "drivers": drivers,
                "drivers_source": drivers_source,
                "router_ready": router_key,
            }
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        rows = [
            self._render_job_row(job)
            for job in (jobs or [])
            if isinstance(job, dict)
        ][:40]
        return {
            "ok": True,
            "error": "",
            "jobs": rows,
            "timeline_driver": "timeline-video" in drivers,
            "drivers": drivers,
            "drivers_source": drivers_source,
            "router_ready": router_key,
        }

    def video_plan(self, payload: dict) -> dict:
        """Ask the Storyboard and Video Director agents for a render plan."""
        topic = str(payload.get("topic") or "").strip()
        script = str(payload.get("script") or "").strip()
        if not topic and not script:
            raise CommandError("a topic or a script is required to plan a video.")
        body = {
            "topic": topic,
            "script": script,
            "aspect_ratio": str(payload.get("aspect_ratio") or "9:16").strip(),
            "duration": payload.get("duration") or 0,
            "language": str(payload.get("language") or "").strip(),
            "style": str(payload.get("style") or "").strip(),
            "brand": str(payload.get("brand") or "").strip(),
        }
        result = self.router_request("POST", "/v1/content/video-plan", body)
        timeline = result.get("timeline") if isinstance(result, dict) else None
        if isinstance(timeline, dict):
            # The operator-configured brand label applies unless the request
            # already named one, so the render carries the same signature as
            # the rest of the stack.
            brand = body["brand"] or self.env_value("MEDIA_STUDIO_BRAND_LABEL", "")
            meta = timeline.setdefault("meta", {})
            brand_block = meta.setdefault("brand", {})
            if brand and not str(brand_block.get("label") or "").strip():
                brand_block["label"] = brand
        return result

    def video_render(self, payload: dict) -> dict:
        """Submit an edited timeline to the timeline-video driver."""
        timeline = payload.get("timeline")
        if isinstance(timeline, str):
            try:
                timeline = json.loads(timeline)
            except ValueError as error:
                raise CommandError(f"the timeline is not valid JSON: {error}") from error
        if not isinstance(timeline, dict):
            raise CommandError("a timeline document is required to render.")
        scenes = timeline.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise CommandError("the timeline has no scenes.")
        job = self.media_studio_request(
            "POST",
            "/jobs",
            {
                "driver": "timeline-video",
                "prompt": str(payload.get("title") or "timeline render"),
                "params": {"timeline": timeline, "brand": payload.get("brand", "")},
            },
            timeout=30,
        )
        record = job.get("job") if isinstance(job, dict) else None
        return {"job": self._render_job_row(record or {})}

    def video_job(self, job_id: str) -> dict:
        job = self.media_studio_request("GET", f"/jobs/{quote_id(job_id)}", timeout=15)
        record = job.get("job") if isinstance(job, dict) else None
        row = self._render_job_row(record or {})
        if isinstance(record, dict):
            row["log_tail"] = str(record.get("log_tail") or "")[-8000:]
        return {"job": row}

    def video_cancel(self, job_id: str) -> dict:
        self.media_studio_request("DELETE", f"/jobs/{quote_id(job_id)}", timeout=15)
        return {"ok": True}

    def video_timeline_validate(self, payload: dict) -> dict:
        """Check a timeline with the renderer's own normalizer, without rendering."""
        timeline = payload.get("timeline")
        if isinstance(timeline, str):
            try:
                timeline = json.loads(timeline)
            except ValueError as error:
                raise CommandError(f"the timeline is not valid JSON: {error}") from error
        if timeline is None:
            raise CommandError("a timeline document is required to validate.")
        result = self.media_studio_request(
            "POST",
            "/timeline/validate",
            {"timeline": timeline},
            timeout=60,
            tolerate_error=True,
        )
        return result if isinstance(result, dict) else {"ok": False, "error": "unexpected reply"}

    def video_retry(self, job_id: str) -> dict:
        """Submit the same driver, prompt and params again as a new job."""
        current = self.media_studio_request("GET", f"/jobs/{quote_id(job_id)}", timeout=15)
        record = current.get("job") if isinstance(current, dict) else None
        if not isinstance(record, dict) or not record.get("driver"):
            raise CommandError(f"Media Studio does not know job {job_id}.")
        # The original params carry the timeline, so a retry reproduces the
        # same render instead of asking the operator to paste it again.
        job = self.media_studio_request(
            "POST",
            "/jobs",
            {
                "driver": str(record.get("driver") or ""),
                "prompt": str(record.get("prompt") or "retry"),
                "params": record.get("params") if isinstance(record.get("params"), dict) else {},
            },
            timeout=30,
        )
        fresh = job.get("job") if isinstance(job, dict) else None
        return {"job": self._render_job_row(fresh or {})}

    # ---------------------------------------------------- storyboard drafts

    def storyboard_list(self) -> dict:
        """The scene-editor draft list, newest first."""
        return {"drafts": self.storyboards.list()}

    def storyboard_create(self, payload: dict) -> dict:
        """Plan one draft with the agents and store it for scene editing."""
        plan = self.video_plan(payload)
        storyboard = plan.get("storyboard") if isinstance(plan, dict) else None
        timeline = plan.get("timeline") if isinstance(plan, dict) else None
        if not isinstance(timeline, dict) or not timeline.get("scenes"):
            raise CommandError("the agents returned no timeline scenes to edit.")
        draft = self.storyboards.create(
            brief={
                "topic": payload.get("topic", ""),
                "script": payload.get("script", ""),
                "language": payload.get("language", ""),
                "style": payload.get("style", ""),
                "duration": payload.get("duration") or 0,
            },
            storyboard=storyboard if isinstance(storyboard, dict) else {},
            timeline=timeline,
        )
        return {"draft": draft}

    def storyboard_read(self, draft_id: str) -> dict:
        """One draft, with its render job refreshed while it is running."""
        draft = self.storyboards.read(draft_id)
        job = draft.get("job") if isinstance(draft.get("job"), dict) else {}
        job_id = str(job.get("id") or "")
        if job_id and str(job.get("status") or "") in {"queued", "running"}:
            record = None
            try:
                payload = self.media_studio_request("GET", f"/jobs/{quote_id(job_id)}", timeout=10)
                record = payload.get("job") if isinstance(payload, dict) else None
            except (CommandError, urllib.error.URLError, ValueError, socket.timeout, OSError):
                record = None
            if isinstance(record, dict):
                draft = self.storyboards.record_job(draft_id, record)
        return {"draft": draft}

    def storyboard_update(self, draft_id: str, payload: dict) -> dict:
        return {"draft": self.storyboards.update(draft_id, payload)}

    def storyboard_delete(self, draft_id: str) -> dict:
        self.storyboards.delete(draft_id)
        return {"ok": True}

    def storyboard_verdict(self, draft_id: str, status: str, note: str = "") -> dict:
        """Record the operator's approve/reject decision on a draft."""
        return {"draft": self.storyboards.set_status(draft_id, status, note)}

    def storyboard_scene_regenerate(self, draft_id: str, index: int, payload: dict) -> dict:
        """Rewrite one scene with the Storyboard Agent, keeping the others."""
        draft = self.storyboards.read(draft_id)
        timeline = draft.get("timeline") if isinstance(draft.get("timeline"), dict) else {}
        scenes = timeline.get("scenes") if isinstance(timeline.get("scenes"), list) else []
        if not scenes:
            raise StoryboardError("this draft has no scenes to rewrite.")
        if index < 1 or index > len(scenes):
            raise StoryboardError(f"scene {index} is outside this draft (1-{len(scenes)}).")
        brief = draft.get("brief") if isinstance(draft.get("brief"), dict) else {}
        result = self.router_request(
            "POST",
            "/v1/content/scene",
            {
                "topic": brief.get("topic") or draft.get("title") or "",
                "script": brief.get("script") or "",
                "storyboard": draft.get("storyboard") or {},
                "index": index,
                "instruction": str(payload.get("instruction") or "").strip(),
                "language": brief.get("language") or "",
            },
            timeout=90,
        )
        scene = result.get("scene") if isinstance(result, dict) else None
        if not isinstance(scene, dict):
            raise CommandError("the Storyboard Agent returned no scene to apply.")
        draft = self.storyboards.replace_scene(draft_id, index, scene)
        return {"draft": draft, "scene": draft["timeline"]["scenes"][index - 1]}

    def storyboard_render(self, draft_id: str, payload: dict) -> dict:
        """Queue the draft's timeline and follow the job on the draft."""
        draft = self.storyboards.read(draft_id)
        status = str(draft.get("status") or "")
        if status == "rejected":
            raise StoryboardError("a rejected draft cannot be rendered.")
        if status == "rendering":
            raise StoryboardError("this draft is already rendering.")
        timeline = draft.get("timeline") if isinstance(draft.get("timeline"), dict) else {}
        if not timeline.get("scenes"):
            raise StoryboardError("this draft has no scenes to render.")
        result = self.video_render(
            {
                "timeline": timeline,
                "title": draft.get("title") or "storyboard render",
                "brand": payload.get("brand", ""),
            }
        )
        job = result.get("job") if isinstance(result, dict) else None
        draft = self.storyboards.record_job(draft_id, job if isinstance(job, dict) else {})
        return {"draft": draft, "job": job if isinstance(job, dict) else {}}

    def _router_read(self, path: str, *, timeout: int = 30) -> tuple[object, str]:
        """Read one router endpoint, reporting failure instead of raising."""
        try:
            return self.router_request("GET", path, timeout=timeout), ""
        except CommandError as error:
            return None, str(error)

    def hermes_overview(self) -> dict:
        """Agents, models and routing telemetry from the Hermes control plane."""
        info, info_error = self._router_read("/router/info", timeout=15)
        summary, summary_error = self._router_read("/control/api/summary?hours=24")
        agents, agents_error = self._router_read("/control/api/agents")
        content_agents, content_error = self._router_read("/v1/content/agents", timeout=15)
        models, models_error = self._router_read("/v1/models", timeout=20)
        return {
            "ok": not (info_error and summary_error and agents_error),
            "console_url": self.router_console_url(),
            "dashboard_url": f"{self.router_console_url()}/dashboard",
            "operations_url": f"{self.router_console_url()}/control/",
            "info": info if isinstance(info, dict) else {},
            "info_error": info_error,
            "summary": summary if isinstance(summary, dict) else {},
            "summary_error": summary_error,
            "agents": agents if isinstance(agents, list) else [],
            "agents_error": agents_error,
            "content_agents": content_agents if isinstance(content_agents, dict) else {},
            "content_error": content_error,
            "models": (models or {}).get("data", []) if isinstance(models, dict) else [],
            "models_error": models_error,
        }

    def orchestration_runs(self, limit: int = 50) -> dict:
        """Recent orchestrator runs with their step and approval counters."""
        runs, error = self._router_read(f"/control/api/orchestrations?limit={int(limit)}")
        return {
            "ok": not error,
            "error": error,
            "runs": runs if isinstance(runs, list) else [],
        }

    def orchestration_run(self, run_id: str) -> dict:
        """One orchestration run with its steps, approvals and reviewer results."""
        snapshot, error = self._router_read(f"/control/api/orchestrations/{quote_id(run_id)}")
        if error:
            raise CommandError(error)
        return {"ok": True, "run": snapshot if isinstance(snapshot, dict) else {}}

    def knowledge_overview(self) -> dict:
        """Knowledge bases with their chunk counts."""
        bases, error = self._router_read("/control/api/knowledge")
        return {
            "ok": not error,
            "error": error,
            "bases": bases if isinstance(bases, list) else [],
        }

    def knowledge_ingest(self, payload: dict) -> dict:
        """Add one document to a knowledge base and report the chunk count."""
        try:
            kb_id = int(payload.get("kb_id"))
        except (TypeError, ValueError):
            raise CommandError("a knowledge base id is required to index a document.") from None
        content = str(payload.get("content") or "").strip()
        if not content:
            raise CommandError("the document content is empty.")
        result = self.router_request(
            "POST",
            f"/control/api/knowledge/{kb_id}/documents",
            {
                "source": str(payload.get("source") or "panel"),
                "title": str(payload.get("title") or "")[:300],
                "content": content,
            },
            timeout=300,
        )
        chunks = result.get("chunks") if isinstance(result, dict) else None
        return {"ok": True, "chunks": chunks if chunks is not None else 0}

    def knowledge_source(self, payload: dict) -> dict:
        """Fetch a URL or PDF file and ingest its content into a knowledge base."""
        from panel.ingest_utils import fetch_url, _extract_pdf
        kb_id = int(payload.get("kb_id") or 0)
        if not kb_id:
            raise CommandError("a knowledge base id is required.")
        url = str(payload.get("url") or "").strip()
        file_path = str(payload.get("file_path") or "").strip()
        if url:
            if not url.startswith(("http://", "https://")):
                raise CommandError("invalid URL.")
            try:
                title, text = fetch_url(url, timeout=int(payload.get("timeout", 30)))
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            if not text.strip():
                raise CommandError("the URL returned no readable text.")
            return self.knowledge_ingest({
                "kb_id": kb_id,
                "source": url,
                "title": str(payload.get("title") or title)[:300],
                "content": text,
            })
        if file_path:
            if not os.path.isfile(file_path):
                raise CommandError(f"file not found: {file_path}")
            try:
                title, text = _extract_pdf(file_path, file_path)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            if not text.strip():
                raise CommandError("PDF extraction produced empty text.")
            return self.knowledge_ingest({
                "kb_id": kb_id,
                "source": file_path,
                "title": str(payload.get("title") or os.path.basename(file_path))[:300],
                "content": text,
            })
        raise CommandError("provide a url or file_path.")

    def knowledge_search(self, payload: dict) -> dict:
        """Retrieval test: run one query against the selected knowledge bases."""
        query = str(payload.get("query") or "").strip()
        if not query:
            raise CommandError("a query is required to test retrieval.")
        kb_ids = []
        for value in payload.get("kb_ids") or []:
            try:
                kb_ids.append(int(value))
            except (TypeError, ValueError):
                raise CommandError(f"knowledge base id {value!r} is not a number.") from None
        try:
            limit = int(payload.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        result = self.router_request(
            "POST",
            "/control/api/knowledge/search",
            {"kb_ids": kb_ids, "query": query, "limit": max(1, min(20, limit))},
            timeout=120,
        )
        return {"ok": True, "results": result if isinstance(result, list) else []}

    def video_artifact(self, job_id: str, name: str) -> tuple[bytes, str]:
        raw = self.media_studio_request(
            "GET",
            f"/artifacts/{quote_id(job_id)}/{quote_id(name)}",
            timeout=120,
            binary=True,
        )
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return raw if isinstance(raw, bytes) else b"", content_type


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

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

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
        except StoryboardMissing as error:
            self._error(HTTPStatus.NOT_FOUND, str(error))
        except StoryboardError as error:
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
        if method == "GET" and parts == ["video", "studio"]:
            self._send_json(HTTPStatus.OK, self.app.video_studio())
            return
        if method == "POST" and parts == ["video", "plan"]:
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.video_plan(body))
            return
        if method == "POST" and parts == ["video", "render"]:
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.video_render(body))
            return
        if method == "GET" and len(parts) == 3 and parts[:2] == ["video", "jobs"]:
            self._send_json(HTTPStatus.OK, self.app.video_job(parts[2]))
            return
        if method == "DELETE" and len(parts) == 3 and parts[:2] == ["video", "jobs"]:
            self._require_csrf()
            self._send_json(HTTPStatus.OK, self.app.video_cancel(parts[2]))
            return
        if (
            method == "GET"
            and len(parts) == 4
            and parts[0] == "video"
            and parts[1] == "artifacts"
        ):
            self._serve_video_artifact(parts[2], parts[3])
            return
        if method == "POST" and parts == ["video", "timeline", "validate"]:
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.video_timeline_validate(body))
            return
        if method == "GET" and parts == ["video", "storyboards"]:
            self._send_json(HTTPStatus.OK, self.app.storyboard_list())
            return
        if method == "POST" and parts == ["video", "storyboards"]:
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.storyboard_create(body))
            return
        if len(parts) == 3 and parts[:2] == ["video", "storyboards"]:
            if method == "GET":
                self._send_json(HTTPStatus.OK, self.app.storyboard_read(parts[2]))
                return
            if method == "PUT":
                self._require_csrf()
                body = self._read_body()
                self._send_json(HTTPStatus.OK, self.app.storyboard_update(parts[2], body))
                return
            if method == "DELETE":
                self._require_csrf()
                self._send_json(HTTPStatus.OK, self.app.storyboard_delete(parts[2]))
                return
        if len(parts) == 4 and parts[:2] == ["video", "storyboards"] and parts[3] in {"approve", "reject"}:
            if method == "POST":
                self._require_csrf()
                body = self._read_body()
                status = "approved" if parts[3] == "approve" else "rejected"
                self._send_json(
                    HTTPStatus.OK,
                    self.app.storyboard_verdict(parts[2], status, str(body.get("note", ""))),
                )
                return
        if method == "POST" and len(parts) == 4 and parts[:2] == ["video", "storyboards"] and parts[3] == "render":
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.storyboard_render(parts[2], body))
            return
        if (
            method == "POST"
            and len(parts) == 6
            and parts[:2] == ["video", "storyboards"]
            and parts[3] == "scenes"
            and parts[5] == "regenerate"
        ):
            self._require_csrf()
            body = self._read_body()
            if not parts[4].isdigit():
                raise StoryboardError(f"{parts[4]} is not a scene number.")
            self._send_json(
                HTTPStatus.OK,
                self.app.storyboard_scene_regenerate(parts[2], int(parts[4]), body),
            )
            return
        if (
            method == "POST"
            and len(parts) == 4
            and parts[0] == "video"
            and parts[1] == "jobs"
            and parts[3] == "retry"
        ):
            self._require_csrf()
            self._send_json(HTTPStatus.OK, self.app.video_retry(parts[2]))
            return
        if method == "GET" and parts == ["hermes", "overview"]:
            self._send_json(HTTPStatus.OK, self.app.hermes_overview())
            return
        if method == "GET" and parts == ["orchestration", "runs"]:
            limit = 50
            for pair in query.split("&"):
                key, _, value = pair.partition("=")
                if key == "limit" and value.isdigit():
                    limit = max(1, min(200, int(value)))
            self._send_json(HTTPStatus.OK, self.app.orchestration_runs(limit))
            return
        if method == "GET" and len(parts) == 3 and parts[:2] == ["orchestration", "runs"]:
            self._send_json(HTTPStatus.OK, self.app.orchestration_run(parts[2]))
            return
        if method == "GET" and parts == ["knowledge"]:
            self._send_json(HTTPStatus.OK, self.app.knowledge_overview())
            return
        if method == "POST" and parts == ["knowledge", "documents"]:
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.knowledge_ingest(body))
            return
        if method == "POST" and parts == ["knowledge", "sources"]:
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.knowledge_source(body))
            return
        if method == "POST" and parts == ["knowledge", "search"]:
            self._require_csrf()
            body = self._read_body()
            self._send_json(HTTPStatus.OK, self.app.knowledge_search(body))
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
        if method == "POST" and parts == ["notebooklm", "start-vnc-login"]:
            self._require_csrf()
            result = self.app.notebooklm_start_vnc_login(client_host=self.headers.get("Host", ""))
            self._send_json(HTTPStatus.OK, result)
            return
        if method == "POST" and parts == ["notebooklm", "stop-vnc-login"]:
            self._require_csrf()
            result = self.app.notebooklm_stop_vnc_login()
            self._send_json(HTTPStatus.OK, result)
            return
        if method == "POST" and parts == ["notebooklm", "import-session"]:
            self._require_csrf()
            body = self._read_body()
            session_data = body.get("session", {})
            result = self.app.notebooklm_import_session(session_data)
            self._send_json(HTTPStatus.OK, result)
            return
        if method == "GET" and parts == ["notebooklm", "profiles"]:
            result = self.app.notebooklm_profiles()
            self._send_json(HTTPStatus.OK, result)
            return
        if method == "POST" and parts == ["notebooklm", "profiles"]:
            self._require_csrf()
            body = self._read_body()
            result = self.app.notebooklm_save_profiles(body)
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
                    lines = max(10, min(int(digits or "200"), 5000))
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

    def _serve_video_artifact(self, job_id: str, name: str) -> None:
        """Stream one rendered artifact through the panel session.

        Media Studio binds to loopback and expects its own token; the panel
        already owns the operator session, so downloads go through here
        instead of exposing the worker port.
        """
        body, content_type = self.app.video_artifact(job_id, name)
        if not body:
            self._error(HTTPStatus.NOT_FOUND, "artifact not found")
            return
        disposition = "attachment" if content_type.startswith("video/") else "inline"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'{disposition}; filename="{Path(name).name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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
