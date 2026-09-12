"""Operator actions for live Content Bot drafts.

The panel never edits the bot's ``state.json`` directly: the bot keeps the
authoritative draft state in memory and rewrites the file, so an external edit
would be overwritten. Instead the panel appends a validated request to a queue
inside the bot data directory, and the bot drains that queue from its main loop
and reports the outcome in ``panel-actions.results.json``.

Both processes run as the same stack user and share the data directory, so a
small ``flock`` around the queue file keeps concurrent drains safe.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REQUEST_FILE = "panel-actions.requests.jsonl"
RESULT_FILE = "panel-actions.results.json"
LOCK_FILE = "panel-actions.lock"
RESULT_LIMIT = 40
DRAFT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Actions the bot applies to a live draft.
ACTIONS = {
    "discard": "Remove the draft from the queue without publishing.",
    "text-only": "Answer the media question with Text only.",
    "image": "Answer the media question with an AI image.",
    "publish": "Publish the approved draft to Telegram.",
    "publish_both": "Publish the approved draft to Telegram and Instagram.",
    "publish_ig": "Publish the approved draft to Instagram.",
}


class DraftActionError(ValueError):
    """Raised when a console draft action is unknown or malformed."""


def data_dir(root: Path) -> Path:
    return Path(root) / "data" / "content-bot"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_atomic(path: Path, payload: dict) -> None:
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
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
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


class _QueueLock:
    """Exclusive lock shared with the bot around the request queue file."""

    def __init__(self, root: Path):
        self.path = data_dir(root) / LOCK_FILE
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
        return False


def validate_action(draft_id: str, action: str) -> tuple[str, str]:
    cleaned_id = str(draft_id or "").strip()
    if not DRAFT_ID_RE.match(cleaned_id):
        raise DraftActionError("invalid draft id")
    cleaned_action = str(action or "").strip()
    if cleaned_action not in ACTIONS:
        raise DraftActionError(f"unknown draft action: {action}")
    return cleaned_id, cleaned_action


def queue_action(root: Path, draft_id: str, action: str) -> dict:
    """Append one validated request for the bot to apply."""
    cleaned_id, cleaned_action = validate_action(draft_id, action)
    request = {
        "id": os.urandom(8).hex(),
        "draft_id": cleaned_id,
        "action": cleaned_action,
        "requested_at": _now(),
    }
    path = data_dir(root) / REQUEST_FILE
    with _QueueLock(root):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(request, ensure_ascii=False) + "\n")
            handle.flush()
            os.fchmod(handle.fileno(), 0o666)
    return request


def pending(root: Path) -> list[dict]:
    """Requests that have not been applied yet."""
    path = data_dir(root) / REQUEST_FILE
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("draft_id"):
            rows.append(row)
    return rows


TUNNEL_LOG_RELATIVE = Path("tunnel") / "trycloudflared.log"
MEDIA_BASE_URL_FILE = "media-base-url.txt"
QUICK_TUNNEL_HOST = "trycloudflare.com"
QUICK_TUNNEL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
TUNNEL_LOG_TAIL = 64_000


def _first_https_line(text: str) -> str:
    for line in text.splitlines():
        candidate = line.split("#", 1)[0].strip().rstrip("/")
        if candidate.startswith("https://") and len(candidate) > len("https://"):
            return candidate
    return ""


def media_base_url(root: Path, configured: str = "") -> str:
    """Public media base URL, resolved the same way the Content Bot does.

    A pinned ``media-base-url.txt`` wins, then a non-quick-tunnel configured
    value, then the most recent hostname from the bundled tunnel log, so the
    console never shows a stale quick tunnel address.
    """
    try:
        pinned = _first_https_line((data_dir(root) / MEDIA_BASE_URL_FILE).read_text(
            encoding="utf-8"
        ))
    except OSError:
        pinned = ""
    if pinned:
        return pinned
    stable = configured.strip().rstrip("/")
    if stable.startswith("https://") and QUICK_TUNNEL_HOST not in stable:
        return stable
    path = data_dir(root) / TUNNEL_LOG_RELATIVE
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TUNNEL_LOG_TAIL))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    matches = QUICK_TUNNEL_RE.findall(tail)
    return matches[-1] if matches else ""


def request_instagram_refresh(root: Path) -> Path:
    """Ask the bot to extend the Instagram token on its next loop."""
    path = data_dir(root) / "instagram-refresh.request"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_now() + "\n", encoding="utf-8")
    os.chmod(path, 0o666)
    return path


def results(root: Path, limit: int = 20) -> list[dict]:
    """Recent bot results, newest first."""
    payload = _read_json(data_dir(root) / RESULT_FILE, {})
    rows = payload.get("results") if isinstance(payload, dict) else None
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    return list(reversed(rows))[: max(1, int(limit))]
