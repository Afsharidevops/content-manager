"""Execution administration surface for Hermes Linux Stack v0.5.8.

This service is intentionally separate from the Smart Router process.  It can
change execution policy files and the dedicated Telegram approval-bot token,
but it never receives the approval signing key, Docker socket, or SSH private
credentials.  The Operations Center talks to it directly from the operator's
browser with a separate short-lived/admin credential.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ADMIN_KEY_PATH = Path(os.getenv("EXECUTION_ADMIN_KEY_FILE", "/run/secrets/execution-admin-key"))
FEATURES_PATH = Path(os.getenv("EXECUTION_FEATURES_FILE", "/run/config/execution-features"))
GENERATION_PATH = Path(os.getenv("EXECUTION_POLICY_GENERATION_FILE", "/run/config/execution-policy-generation"))
USERS_PATH = Path(os.getenv("EXECUTION_APPROVAL_USERS_FILE", "/run/config/execution-users"))
ALLOWED_USERS_PATH = Path(os.getenv("EXECUTION_ALLOWED_USERS_FILE", "/run/config/telegram-allowed-users"))
BOT_TOKEN_PATH = Path(os.getenv("EXECUTION_APPROVAL_BOT_TOKEN_FILE", "/run/secrets/execution-approval-bot-token"))
HERMES_BOT_TOKEN_HASH_PATH = Path(os.getenv("EXECUTION_HERMES_BOT_TOKEN_HASH_FILE", "/run/config/hermes-bot-token.sha256"))
CONTROL_SECRET_PATH = Path(os.getenv("BROKER_CONTROL_SECRET_FILE", "/run/config/execution-control"))
SSH_PROFILES_PATH = Path(os.getenv("EXECUTION_SSH_PROFILES_DIR", "/profiles"))
AUDIT_PATH = Path(os.getenv("EXECUTION_ADMIN_AUDIT_FILE", "/state/admin-audit.jsonl"))

ALLOWED_FEATURES = ("local", "ssh", "docker")
TOKEN_RE = re.compile(r"^[0-9]+:[A-Za-z0-9_-]{20,}$")
MAX_AUDIT_LINES = 1000


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _atomic_write(path: Path, value: str, mode: int = 0o660) -> None:
    """Safely rewrite an existing mounted regular file without replacing its inode.

    Compose mounts execution policy files individually.  Replacing a bind-mount
    target is unreliable and would also change ownership, so v0.5.8 writes the
    existing inode with O_NOFOLLOW and fsync.  manage.sh creates the files first.
    """
    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"execution admin target is unavailable: {path.name}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"execution admin target is not a regular file: {path.name}")
        data = value + ("" if not value or value.endswith("\n") else "\n")
        os.write(fd, data.encode("utf-8"))
        os.fsync(fd)
        try: os.fchmod(fd, mode)
        except PermissionError: pass
    finally:
        os.close(fd)


def configured() -> bool:
    return bool(_read(ADMIN_KEY_PATH))


def authorized(candidate: str) -> bool:
    expected = _read(ADMIN_KEY_PATH)
    return bool(expected and candidate and hmac.compare_digest(expected, candidate))


def allowed_origin(origin: str) -> bool:
    if not origin:
        return True  # curl/CLI clients do not send Origin.
    configured_origins = {
        item.strip().rstrip("/")
        for item in os.getenv(
            "EXECUTION_ADMIN_ALLOWED_ORIGINS",
            "http://127.0.0.1:8787,http://localhost:8787",
        ).split(",")
        if item.strip()
    }
    return origin.rstrip("/") in configured_origins


def _features() -> list[str]:
    raw = _read(FEATURES_PATH)
    return [item for item in ALLOWED_FEATURES if item in {x.strip() for x in raw.split(",") if x.strip()}]


def _users(path: Path) -> list[str]:
    raw = _read(path).replace("\n", ",")
    result: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if item and item.isdigit() and item not in result:
            result.append(item)
    return result


def _generation() -> int:
    try:
        return max(0, int(_read(GENERATION_PATH) or "0"))
    except ValueError:
        return 0


def _bump_generation() -> int:
    value = _generation() + 1
    _atomic_write(GENERATION_PATH, str(value))
    return value


def _audit(action: str, status: str = "ok", detail: dict[str, Any] | None = None) -> None:
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "status": status,
        "detail": detail or {},
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_AUDIT_LINES:
            _atomic_write(AUDIT_PATH, "\n".join(lines[-MAX_AUDIT_LINES:]), 0o600)
    except OSError:
        pass


def _probe(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=2.5) as response:
            data = json.loads(response.read().decode("utf-8"))
        checks = data.get("checks") if isinstance(data, dict) else {}
        return {"reachable": True, "status": data.get("status", "unknown"), "checks": checks or {}}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return {"reachable": False, "status": "unreachable", "error": type(exc).__name__}


def status() -> dict[str, Any]:
    token = _read(BOT_TOKEN_PATH)
    profiles: list[dict[str, str]] = []
    try:
        for directory in sorted(SSH_PROFILES_PATH.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            name = directory.name
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
                continue
            auth = "unknown"
            try:
                metadata = json.loads((directory / "profile.json").read_text(encoding="utf-8"))
                auth = str(metadata.get("auth", "publickey"))[:32]
            except (OSError, json.JSONDecodeError):
                pass
            profiles.append({"name": name, "auth": auth})
    except OSError:
        pass
    return {
        "version": "0.1.3",
        "features": _features(),
        "users": _users(USERS_PATH),
        "allowed_users": _users(ALLOWED_USERS_PATH),
        "policy_generation": _generation(),
        "bot_token_configured": bool(TOKEN_RE.fullmatch(token)),
        "admin_key_configured": configured(),
        "ssh_profiles": profiles,
        "services": {
            "approver": _probe(os.getenv("EXECUTION_APPROVER_URL", "http://execution-approver:8751")),
            "docker": _probe(os.getenv("EXECUTION_DOCKER_BROKER_URL", "http://execution-docker-broker:8750")),
            "ssh": _probe(os.getenv("EXECUTION_SSH_BROKER_URL", "http://execution-ssh-broker:8750")),
        },
        "security": {
            "signing_key_mounted": False,
            "docker_socket_mounted": False,
            "ssh_private_credentials_mounted": False,
            "bot_token_readback": False,
        },
    }


def set_features(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("features", [])
    if not isinstance(value, list):
        raise ValueError("features must be a JSON array")
    requested = []
    for item in value:
        item = "local" if str(item) == "sandbox" else str(item)
        if item not in ALLOWED_FEATURES:
            raise ValueError(f"unsupported execution feature: {item}")
        if item not in requested:
            requested.append(item)
    canonical = [item for item in ALLOWED_FEATURES if item in requested]
    _atomic_write(FEATURES_PATH, ",".join(canonical))
    generation = _bump_generation()
    _audit("features.update", detail={"features": canonical, "generation": generation})
    return {"status": "ok", "features": canonical, "policy_generation": generation,
            "note": "Feature policy changes are live for already-deployed brokers. First-time broker deployment still uses manage.sh."}


def set_users(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("users", [])
    if not isinstance(value, list):
        raise ValueError("users must be a JSON array")
    users: list[str] = []
    for item in value:
        item = str(item).strip()
        if not item.isdigit():
            raise ValueError("execution approvers must be numeric Telegram user IDs")
        if item not in users:
            users.append(item)
    allowed = set(_users(ALLOWED_USERS_PATH))
    if users and (not allowed or any(item not in allowed for item in users)):
        raise ValueError("execution approvers must already exist in TELEGRAM_ALLOWED_USERS")
    _atomic_write(USERS_PATH, ",".join(users))
    generation = _bump_generation()
    _audit("users.update", detail={"users": users, "generation": generation})
    return {"status": "ok", "users": users, "policy_generation": generation}


def replace_bot_token(payload: dict[str, Any]) -> dict[str, Any]:
    token = str(payload.get("token", "")).strip()
    if not TOKEN_RE.fullmatch(token):
        raise ValueError("Telegram bot token format is invalid")
    hermes_hash = _read(HERMES_BOT_TOKEN_HASH_PATH)
    if hermes_hash and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), hermes_hash):
        raise ValueError("the execution approver must use a different Telegram bot from Hermes")
    _atomic_write(BOT_TOKEN_PATH, token, 0o600)
    generation = _bump_generation()
    _audit("approval_bot.replace", detail={"generation": generation})
    return {"status": "ok", "bot_token_configured": True, "policy_generation": generation,
            "token": None, "note": "The token is write-only and is never returned by the API."}


def rotate_control_secret() -> dict[str, Any]:
    _atomic_write(CONTROL_SECRET_PATH, secrets.token_hex(32), 0o660)
    generation = _bump_generation()
    _audit("control_secret.rotate", detail={"generation": generation})
    return {"status": "ok", "policy_generation": generation,
            "note": "Broker control secret rotated; pending capabilities from older generations are invalid."}


def audit(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(200, int(limit)))
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    result = []
    for line in reversed(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result
