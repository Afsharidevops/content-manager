"""Apply the draft actions queued by the Content Manager panel.

The panel never edits ``state.json``: the bot keeps the authoritative copy in
memory and rewrites the whole file, so an external edit would be lost. Instead
the panel appends a validated request to a small queue inside the bot data
directory, and the bot drains that queue from its main loop and reports the
outcome back in ``panel-actions.results.json``.

The file names, the lock file, and the JSON shapes are duplicated from
``panel/drafts.py`` on purpose: the bot image ships without the panel package,
so the two processes only share the on-disk contract.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from content_bot import telegram as telegram_mod

log = logging.getLogger("content_bot.panel")

REQUEST_FILE = "panel-actions.requests.jsonl"
RESULT_FILE = "panel-actions.results.json"
LOCK_FILE = "panel-actions.lock"
RESULT_LIMIT = 40

TEXT_ONLY_STATUSES = {"media_ask", "awaiting_media", "media_failed"}
IMAGE_STATUSES = {"media_ask", "media_failed"}
PUBLISH_STATUSES = {"text", "text_only", "media_ready"}

_NO_MEDIA = (
    "Media Studio is not configured; set CONTENT_MEDIA_STUDIO_URL and restart "
    "the bot."
)


def data_dir(settings) -> Path:
    return Path(str(getattr(settings, "data_dir", "") or "/data"))


def requests_path(settings) -> Path:
    return data_dir(settings) / REQUEST_FILE


def results_path(settings) -> Path:
    return data_dir(settings) / RESULT_FILE


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
    """Exclusive lock shared with the panel around the request queue file."""

    def __init__(self, settings):
        self.path = data_dir(settings) / LOCK_FILE
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


def _consume(settings) -> list[dict]:
    """Take every queued request, leaving the queue empty for the panel."""
    path = requests_path(settings)
    rows: list[dict] = []
    with _QueueLock(settings):
        if not path.is_file():
            return rows
        taken = path.with_name(path.name + ".draining")
        try:
            os.replace(path, taken)
        except OSError as error:
            log.warning("panel queue could not be claimed: %s", error)
            return rows
        try:
            raw = taken.read_text(encoding="utf-8")
        except OSError:
            raw = ""
        finally:
            try:
                taken.unlink()
            except OSError:
                pass
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("draft_id") and row.get("action"):
            rows.append(row)
    return rows


def record_results(settings, rows: list[dict]) -> None:
    """Append drained results for the panel, newest last on disk."""
    if not rows:
        return
    path = results_path(settings)
    payload = _read_json(path, {})
    stored = payload.get("results") if isinstance(payload, dict) else None
    stored = [row for row in (stored or []) if isinstance(row, dict)]
    stored.extend(rows)
    _write_atomic(
        path,
        {"results": stored[-RESULT_LIMIT:], "updated_at": _now()},
    )


def _describe(record: dict) -> str:
    title = " ".join(str(record.get("title") or "").split())
    return title[:80]


def _sync_ask_message(bot, record: dict, text: str) -> None:
    """Drop the stale media keyboard from the Telegram prompt."""
    chat_id = record.get("chat_id")
    ask_id = record.get("ask_message_id")
    if chat_id is None or ask_id is None:
        return
    bot._edit_safe(chat_id, int(ask_id), text)


def _apply(bot, request: dict) -> tuple[str, str]:
    """Run one queued action; returns ``(status, detail)``."""
    draft_id = str(request.get("draft_id") or "")
    action = str(request.get("action") or "")
    record = bot.state.get_draft(draft_id)
    if record is None:
        return "missing", "The draft is no longer active."
    status = str(record.get("status") or "")

    if action == "discard":
        bot.discard_draft(record)
        return "done", "Draft discarded."

    if action == "text-only":
        if status not in TEXT_ONLY_STATUSES:
            return "skipped", f"Text only is not available while the draft is {status}."
        bot._media_callback("", "none", draft_id)
        _sync_ask_message(bot, record, "Text-only post; approve it to publish.")
        return "done", "Marked as a text-only post."

    if action == "image":
        if status not in IMAGE_STATUSES:
            return "skipped", f"An AI image is not available while the draft is {status}."
        if bot.media is None:
            return "skipped", _NO_MEDIA
        bot._media_callback("", "image", draft_id)
        current = bot.state.get_draft(draft_id) or {}
        if str(current.get("status") or "") != "media_running":
            return "error", "The image job did not start; check the bot log."
        _sync_ask_message(bot, record, "Creating the image now; this can take a few minutes.")
        return "done", "Image generation started."

    if action == "publish":
        if status not in PUBLISH_STATUSES:
            return "skipped", f"Publishing is not available while the draft is {status}."
        bot._approve(
            "",
            record,
            record.get("chat_id"),
            record.get("message_id"),
            targets=("telegram",),
        )
        if bot.state.get_draft(draft_id) is None:
            return "done", "Published to Telegram."
        return "error", "Publish did not complete; check the bot log."

    return "error", f"Unknown action: {action}"


def _notify(bot, request: dict, record: dict, status: str, detail: str) -> None:
    """Tell the operator in Telegram what the console just did."""
    chat_id = record.get("chat_id")
    if chat_id is None:
        return
    title = _describe(record)
    prefix = {
        "done": "Panel:",
        "skipped": "Panel skipped:",
        "missing": "Panel:",
        "error": "Panel error:",
    }.get(status, "Panel:")
    text = f"{prefix} {detail}"
    if title:
        text += f"\n{title}"
    try:
        bot.api.send_message(chat_id, text)
    except telegram_mod.TelegramError as error:
        log.warning("panel action notice failed: %s", error)


def drain(bot) -> int:
    """Apply every queued panel action; returns how many were handled."""
    settings = getattr(bot, "settings", None)
    if settings is None:
        return 0
    path = requests_path(settings)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return 0
    except OSError:
        return 0
    rows = _consume(settings)
    if not rows:
        return 0
    results: list[dict] = []
    for request in rows:
        draft_id = str(request.get("draft_id") or "")
        record = bot.state.get_draft(draft_id) or {}
        try:
            status, detail = _apply(bot, request)
        except telegram_mod.TelegramError as error:
            status, detail = "error", f"Telegram API error: {error}"
        except Exception as error:  # noqa: BLE001 - one bad request must not stop the loop
            log.exception("panel action %s failed", request.get("action"))
            status, detail = "error", f"{type(error).__name__}: {error}"
        results.append(
            {
                "id": str(request.get("id") or ""),
                "draft_id": draft_id,
                "action": str(request.get("action") or ""),
                "status": status,
                "ok": status == "done",
                "message": detail,
                "detail": detail,
                "title": _describe(record),
                "requested_at": str(request.get("requested_at") or ""),
                "finished_at": _now(),
            }
        )
        if record:
            _notify(bot, request, record, status, detail)
    record_results(settings, results)
    log.info("panel actions applied: %s", len(results))
    return len(results)
