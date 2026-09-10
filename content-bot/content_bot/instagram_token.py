"""Long-lived Instagram access token maintenance.

Instagram Login issues a token that stays valid for 60 days and can be
extended for another 60 days from any still-valid copy. The refresh is a plain
GET on the Graph host, so the bot can do it on its own and only has to tell the
operator when a manual renewal is actually required.

The refreshed token and its expiry live in ``<data_dir>/instagram-token.json``
so a container restart keeps publishing. The operator console reads the same
file for the expiry card and never returns the token itself.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from content_bot.http import HttpError, request_json

log = logging.getLogger("content_bot.instagram_token")

TOKEN_FILE = "instagram-token.json"
REQUEST_FILE = "instagram-refresh.request"
VALID_DAYS = 60
REFRESH_AFTER_DAYS = 7
WARN_DAYS = 10
INSTAGRAM_HOST = "graph.instagram.com"


class InstagramTokenError(RuntimeError):
    """Raised when the long-lived token could not be extended."""


def token_path(data_dir) -> Path:
    return Path(str(data_dir or "/data")) / TOKEN_FILE


def request_path(data_dir) -> Path:
    return Path(str(data_dir or "/data")) / REQUEST_FILE


def take_request(data_dir) -> bool:
    """Consume one panel refresh request; True only when it was pending."""
    try:
        request_path(data_dir).unlink()
    except FileNotFoundError:
        return False
    except OSError as error:
        log.warning("refresh request could not be cleared: %s", error)
        return False
    return True


def load(data_dir) -> dict:
    """The stored token record, or an empty dict when nothing is stored yet."""
    path = token_path(data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save(data_dir, record: dict) -> None:
    """Write the token record with owner-only permissions."""
    path = token_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def parse_time(value, default=None):
    """Parse one stored ISO timestamp; returns ``default`` when unusable."""
    text = str(value or "").strip()
    if not text:
        return default
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def days_left(record: dict, *, now=None) -> int | None:
    """Whole days until the stored token expires, or ``None`` when unknown."""
    expires = parse_time((record or {}).get("expires_at"))
    if expires is None:
        return None
    current = now or datetime.now(timezone.utc)
    return int((expires - current).total_seconds() // 86400)


def is_expired(record: dict, *, now=None, margin_days: int = 1) -> bool:
    """Whether the stored copy is unusable, or close enough to be unsafe."""
    remaining = days_left(record, now=now)
    if remaining is None:
        return False
    return remaining < max(0, int(margin_days))


def refresh_due(record: dict, *, now=None, after_days: int = REFRESH_AFTER_DAYS) -> bool:
    """Whether the stored copy should be extended again."""
    refreshed = parse_time((record or {}).get("refreshed_at"))
    if refreshed is None or not str((record or {}).get("access_token") or "").strip():
        return True
    current = now or datetime.now(timezone.utc)
    return (current - refreshed) >= timedelta(days=max(1, int(after_days)))


def refresh_url(settings, token: str) -> tuple[str, str]:
    """Build the extension URL and report which login variant it belongs to."""
    base = str(getattr(settings, "instagram_api_base", "") or "").strip().rstrip("/")
    app_id = str(getattr(settings, "instagram_app_id", "") or "").strip()
    app_secret = str(getattr(settings, "instagram_app_secret", "") or "").strip()
    encoded = quote(str(token or "").strip(), safe="")
    if INSTAGRAM_HOST in base or not (app_id and app_secret):
        host = base or f"https://{INSTAGRAM_HOST}"
        return (
            f"{host}/refresh_access_token?grant_type=ig_refresh_token&access_token={encoded}",
            "instagram-login",
        )
    version = str(getattr(settings, "instagram_api_version", "") or "").strip()
    prefix = f"{base}/{version}" if version else base
    return (
        f"{prefix}/oauth/access_token?grant_type=fb_exchange_token"
        f"&client_id={quote(app_id, safe='')}"
        f"&client_secret={quote(app_secret, safe='')}"
        f"&fb_exchange_token={encoded}",
        "facebook-login",
    )


def _error_detail(error: Exception) -> str:
    body = getattr(error, "body", b"")
    if isinstance(body, (bytes, bytearray)) and body:
        text = body.decode("utf-8", "replace").strip()
        if text:
            try:
                payload = json.loads(text)
                detail = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(detail, dict) and detail.get("message"):
                    return str(detail["message"])[:300]
                if isinstance(payload, dict) and payload.get("error_description"):
                    return str(payload["error_description"])[:300]
            except ValueError:
                pass
            return text[:300]
    return str(error)[:300]


def refresh(settings, token: str, *, request=None, now=None, timeout: int = 30) -> dict:
    """Extend one long-lived token and return the stored record.

    Raises ``InstagramTokenError`` when the host rejects the request; the
    caller decides whether to notify the operator or keep the previous copy.
    """
    current = str(token or "").strip()
    if not current:
        raise InstagramTokenError("no Instagram access token is configured")
    url, source = refresh_url(settings, current)
    request_fn = request or request_json
    try:
        payload = request_fn(url, timeout=int(timeout or 30))
    except HttpError as error:
        raise InstagramTokenError(f"HTTP {error.status}: {_error_detail(error)}") from error
    except (ConnectionError, OSError, TimeoutError) as error:
        raise InstagramTokenError(f"connection error: {error}") from error
    except ValueError as error:
        raise InstagramTokenError(f"unreadable response: {error}") from error
    if not isinstance(payload, dict):
        raise InstagramTokenError("the token host returned an unexpected payload")
    fresh = str(payload.get("access_token") or "").strip()
    if not fresh:
        message = payload.get("error_message") or payload.get("message") or ""
        raise InstagramTokenError(
            str(message)[:300] or "the token host returned no access_token"
        )
    try:
        expires_in = int(payload.get("expires_in") or VALID_DAYS * 86400)
    except (TypeError, ValueError):
        expires_in = VALID_DAYS * 86400
    expires_in = max(3600, expires_in)
    moment = now or datetime.now(timezone.utc)
    record = {
        "access_token": fresh,
        "token_type": str(payload.get("token_type") or "bearer"),
        "expires_in": expires_in,
        "refreshed_at": moment.isoformat(timespec="seconds"),
        "expires_at": (moment + timedelta(seconds=expires_in)).isoformat(timespec="seconds"),
        "source": source,
        "last_error": "",
        "checked_at": moment.isoformat(timespec="seconds"),
    }
    return record


def record_failure(data_dir, record: dict, message: str, *, now=None) -> dict:
    """Store a refresh failure next to the token the bot keeps using."""
    moment = now or datetime.now(timezone.utc)
    updated = dict(record or {})
    updated["last_error"] = str(message)[:300]
    updated["checked_at"] = moment.isoformat(timespec="seconds")
    updated.setdefault("notified_at", "")
    updated.setdefault("notified_error", "")
    if not updated.get("source"):
        updated["source"] = "env"
    if not updated.get("refreshed_at"):
        updated["refreshed_at"] = ""
    save(data_dir, updated)
    return updated


def expiry_note(record: dict, *, now=None) -> str:
    """One operator-facing sentence about the stored token state."""
    remaining = days_left(record, now=now)
    if remaining is None:
        return "Instagram token: no refresh record yet."
    if remaining < 0:
        return "Instagram token: expired; paste a fresh token in INSTAGRAM_ACCESS_TOKEN."
    warn = " Renew it soon to avoid a gap in publishing." if remaining <= WARN_DAYS else ""
    return f"Instagram token: {remaining} day(s) left.{warn}"


def hours_since(value, *, now=None) -> float | None:
    """Hours elapsed since one stored ISO timestamp, or ``None``."""
    moment = parse_time(value)
    if moment is None:
        return None
    current = now or datetime.now(timezone.utc)
    return (current - moment).total_seconds() / 3600.0


def should_notify(record: dict, message: str, *, now=None, cooldown_hours: float = 6.0) -> bool:
    """Whether one refresh failure is worth another operator notice."""
    if str((record or {}).get("notified_error") or "") != str(message):
        return True
    elapsed = hours_since((record or {}).get("notified_at"), now=now)
    if elapsed is None:
        return True
    return elapsed >= max(0.5, float(cooldown_hours))
