"""Telegram Content Bot: on-demand link posts and daily editorial proposals."""

from __future__ import annotations

import html
import logging
import os
import re
import secrets
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from content_bot import extract, fetch, state as state_mod, telegram as telegram_mod
from content_bot import instagram as instagram_mod
from content_bot import mediastudio as media_mod, rtl as rtl_mod, search as search_mod
from content_bot import instagram_token as instagram_token_mod
from content_bot import panel_actions as panel_actions_mod
from content_bot import platforms as platforms_mod
from content_bot import workflow, writer as writer_mod
from content_bot.config import BotSettings
from content_pipeline.normalize import canonicalize_url, content_hash as canonical_content_hash

URL_RE = re.compile(r"https?://[^\s<>\"']+")
MIN_ARTICLE_CHARS = 60

log = logging.getLogger("content_bot")

# Forces the bold title line to render right-to-left in Telegram even when it
# contains Latin-script product names.
_TITLE_RTL_OPEN = "\u202b"
_TITLE_RTL_CLOSE = "\u202c"

# Weekday names accepted by the per-platform routine cadence in the policy.
_WEEKDAY_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _html_escape(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _html_title(title: str) -> str:
    """Render one post title as bold, right-to-left HTML."""
    return (
        f"<b>{_TITLE_RTL_OPEN}{_html_escape(title)}"
        f"{_TITLE_RTL_CLOSE}</b>"
    )


def _rtl_body_html(body: str) -> str:
    """Escape post body for Telegram HTML with per-line RTL direction marks."""
    lines: list[str] = []
    for raw in str(body or "").splitlines():
        if not raw.strip():
            lines.append("")
            continue
        escaped = _html_escape(raw)
        if rtl_mod.needs_rtl_mark(raw):
            escaped = f"{rtl_mod.RTL_MARK}{escaped}"
        lines.append(escaped)
    return "\n".join(lines)


_MEDIA_CAPTION_MAX = 1024
_TEXT_MESSAGE_MAX = 4096

# Backoff after a transient network failure in the polling loop.
_CONNECTION_RETRY_SECONDS = 5

# The first Telegram call happens while the container starts, which can race
# the resolver or the outbound route right after a stack restart. Retry with a
# growing delay so a transient failure does not end as a crashed process.
_STARTUP_ATTEMPTS = 5
_STARTUP_BACKOFF_SECONDS = 3


def _media_caption(record: dict) -> str:
    """Best-effort single caption (max 1024 chars) for one media post."""
    return _media_caption_messages(record)[0]


def _media_caption_messages(record: dict) -> list[str]:
    """Split one media post into publishable HTML messages.

    The first message is the media caption and never exceeds Telegram's
    1024-character media caption limit. Any paragraphs that do not fit are
    returned as continuation text messages so that no post content is lost.
    When a continuation exists, the clickable source link moves to the end
    of the last message instead of the caption.
    """
    url = str(record.get("source_url") or "").strip()
    body = writer_mod.Writer._strip_source_url(str(record.get("body") or ""), url)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    title_html = _html_title(record.get("title") or "")
    link_html = f'<a href="{url}">{_html_escape(url)}</a>' if url else ""
    suffix = f"\n\n{link_html}" if link_html else ""

    def assemble(parts: list[str], *, with_link: bool) -> str:
        body_html = "\n\n".join(_rtl_body_html(part) for part in parts)
        end = suffix if with_link else ""
        return f"{title_html}\n\n{body_html}{end}"

    if paragraphs and len(assemble(paragraphs, with_link=True)) <= _MEDIA_CAPTION_MAX:
        return [assemble(paragraphs, with_link=True)]
    caption_parts: list[str] = []
    for part in paragraphs:
        if len(assemble(caption_parts + [part], with_link=False)) <= _MEDIA_CAPTION_MAX:
            caption_parts.append(part)
        else:
            break
    rest = paragraphs[len(caption_parts):]
    messages = [assemble(caption_parts, with_link=False)]
    continuation: list[str] = []
    current: list[str] = []
    current_len = 0
    for part in rest:
        piece = _rtl_body_html(part)
        if len(piece) > _TEXT_MESSAGE_MAX:
            if current:
                continuation.append("\n\n".join(current))
                current = []
                current_len = 0
            continuation.append(piece)
            continue
        added = len(piece) + (2 if current else 0)
        if current and current_len + added > _TEXT_MESSAGE_MAX:
            continuation.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(piece)
        current_len += len(piece) + (2 if len(current) > 1 else 0)
    if current:
        continuation.append("\n\n".join(current))
    continuation[0] = "…\n\n" + continuation[0]
    if suffix:
        last = continuation[-1]
        if len(last) + len(suffix) > _TEXT_MESSAGE_MAX:
            continuation.append(suffix.lstrip("\n"))
        else:
            continuation[-1] = last + suffix
    return messages + continuation


MEDIA_BASE_URL_FILE = "media-base-url.txt"
TUNNEL_LOG_NAME = "trycloudflared.log"
QUICK_TUNNEL_HOST = "trycloudflare.com"
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")
_TUNNEL_LOG_TAIL = 64_000


def _clean_base_url(value) -> str:
    """Return one usable public base URL, or an empty string."""
    text = str(value or "").strip().rstrip("/")
    if text.startswith("https://") and len(text) > len("https://"):
        return text
    return ""


def _base_url_from_file(data_dir) -> str:
    """A hostname the operator (or a deployment script) pinned explicitly."""
    try:
        raw = (Path(data_dir) / MEDIA_BASE_URL_FILE).read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in raw.splitlines():
        cleaned = _clean_base_url(line.split("#", 1)[0])
        if cleaned:
            return cleaned
    return ""


def _stable_base_url(value) -> str:
    """The configured base URL, but never a quick tunnel hostname."""
    cleaned = _clean_base_url(value)
    if cleaned and QUICK_TUNNEL_HOST not in cleaned:
        return cleaned
    return ""


def _base_url_from_tunnel_log(data_dir) -> str:
    """The most recent hostname the bundled quick tunnel reported."""
    path = Path(data_dir) / "tunnel" / TUNNEL_LOG_NAME
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TUNNEL_LOG_TAIL))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return ""
    matches = _TUNNEL_URL_RE.findall(tail)
    return matches[-1] if matches else ""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _policy_zone(policy: dict) -> ZoneInfo:
    tz_name = str((policy.get("pipeline") or {}).get("timezone") or "Asia/Tehran")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return timezone.utc


class ContentBot:
    def __init__(
        self,
        settings: BotSettings,
        *,
        api=None,
        writer=None,
        store=None,
        fetch_page=None,
        fetch_feed=None,
        media=None,
        search_topic=None,
        now_fn=_now_utc,
    ):
        self.settings = settings
        self.api = api or telegram_mod.TelegramApi(
            settings.bot_token,
            settings.telegram_api_base,
        )
        if writer is None and settings.writer_base_url:
            writer = writer_mod.Writer(
                settings.writer_base_url,
                settings.writer_api_key,
                settings.writer_model,
                max_tokens=settings.writer_max_tokens,
                reasoning_effort=settings.writer_reasoning_effort,
            )
        self.writer = writer
        if media is None and settings.media_studio_url:
            media = media_mod.MediaStudio(
                settings.media_studio_url,
                settings.media_studio_token,
            )
        self.media = media
        self.state = store or state_mod.StateStore(Path(settings.data_dir) / "state.json")
        self.fetch_page = fetch_page or fetch.fetch_page
        self.fetch_feed = fetch_feed or fetch.fetch_feed
        self.search_topic = search_topic or search_mod.search_topic
        self.now_fn = now_fn
        self.tools = workflow.load_tools(settings.policy_dir)
        self._offset = 0
        self._instagram = None
        self._instagram_record_cache: dict | None = None
        self._instagram_checked_at = None
        self._instagram_notified = False

    def _instagram_media_base_url(self) -> str:
        """Public media base URL the Graph API can download from.

        Order of precedence:

        1. ``data/content-bot/media-base-url.txt`` - an explicit, stable
           hostname written by the operator or a deployment script.
        2. ``INSTAGRAM_MEDIA_PUBLIC_BASE_URL`` when it is not a quick tunnel:
           a pinned domain must win over anything automatic.
        3. the hostname the bundled tunnel container reported last, read from
           ``data/content-bot/tunnel/trycloudflared.log``. A quick tunnel
           changes hostname on every restart, so the live log beats the stale
           value an operator may still have in ``.env``.
        """
        for candidate in (
            _base_url_from_file(self.settings.data_dir),
            _stable_base_url(self.settings.instagram_media_public_base_url),
            _base_url_from_tunnel_log(self.settings.data_dir),
        ):
            if candidate:
                return candidate
        return ""

    def _instagram_token(self) -> str:
        """The Instagram access token to publish with: refreshed one wins."""
        stored = self._instagram_record() or {}
        token = str(stored.get("access_token") or "").strip()
        if token and not instagram_token_mod.is_expired(stored, now=self.now_fn()):
            return token
        return self.settings.instagram_access_token

    def _instagram_record(self) -> dict:
        """The refresh bookkeeping saved by the token maintenance step."""
        if self._instagram_record_cache is None:
            self._instagram_record_cache = instagram_token_mod.load(
                self.settings.data_dir
            )
        return self._instagram_record_cache

    def _instagram_publisher(self):
        """Lazy Instagram Graph API publisher bound to the configured account."""
        media_base = self._instagram_media_base_url()
        if (
            self._instagram is not None
            and str(getattr(self._instagram, "media_base_url", "")) != media_base
        ):
            # The tunnel handed out a new hostname; rebuild against it.
            self._instagram = None
        if self._instagram is None and self.settings.instagram_business_id and self._instagram_token():
            self._instagram = instagram_mod.InstagramPublisher(
                self.settings.instagram_business_id,
                self._instagram_token(),
                media_base_url=media_base,
                media_root=self.settings.data_dir,
                graph_base=self.settings.instagram_api_base,
                api_version=self.settings.instagram_api_version,
                poll_timeout_seconds=self.settings.instagram_poll_timeout_seconds,
            )
        return self._instagram

    # ---------------------------------------------------- instagram token

    def _notify_owner(self, text: str) -> None:
        """Send one operator notice, ignoring Telegram failures."""
        owner = self._owner_chat_id()
        if owner is None:
            log.info("operator notice skipped: %s", text)
            return
        try:
            self.api.send_message(owner, text)
        except telegram_mod.TelegramError as error:
            log.warning("operator notice failed: %s", error)

    def instagram_status_text(self) -> str:
        """Human-readable Instagram credential state for the operator."""
        if not self.settings.instagram_business_id:
            return "Instagram is not configured: set INSTAGRAM_BUSINESS_ID."
        record = self._instagram_record() or {}
        lines = [instagram_token_mod.expiry_note(record, now=self.now_fn())]
        media_base = self._instagram_media_base_url()
        lines.append(f"Media base URL: {media_base or 'not set (publishing needs one)'}")
        if record.get("refreshed_at"):
            lines.append(f"Last refresh: {record['refreshed_at']}")
        if record.get("source"):
            lines.append(f"Login variant: {record['source']}")
        if record.get("last_error"):
            lines.append(f"Last error: {record['last_error']}")
        return "\n".join(lines)

    def maybe_refresh_instagram_token(self, *, force: bool = False) -> bool:
        """Extend the long-lived Instagram token when it is due.

        The refresh is automatic; the operator only gets a Telegram notice
        when something needs attention (failure, or a token close to expiry).
        """
        if self.settings.instagram_disable_refresh:
            return False
        if not self.settings.instagram_business_id:
            return False
        token = self._instagram_token()
        if not token:
            return False
        now = self.now_fn()
        if instagram_token_mod.take_request(self.settings.data_dir):
            force = True
        record = self._instagram_record() or {}
        if not force and not instagram_token_mod.refresh_due(record, now=now):
            return False
        checked = self._instagram_checked_at
        if not force and checked is not None and (now - checked) < timedelta(hours=1):
            return False
        self._instagram_checked_at = now
        try:
            fresh = instagram_token_mod.refresh(self.settings, token, now=now)
        except instagram_token_mod.InstagramTokenError as error:
            message = str(error)
            notify = instagram_token_mod.should_notify(record, message, now=now)
            record = instagram_token_mod.record_failure(
                self.settings.data_dir,
                record,
                message,
                now=now,
            )
            if notify:
                record["notified_at"] = now.isoformat(timespec="seconds")
                record["notified_error"] = message[:300]
                instagram_token_mod.save(self.settings.data_dir, record)
            self._instagram_record_cache = record
            log.warning("instagram token refresh failed: %s", error)
            if notify:
                self._notify_owner(
                    "Instagram token refresh failed: "
                    f"{error}\n{instagram_token_mod.expiry_note(record, now=now)}"
                )
            return False
        instagram_token_mod.save(self.settings.data_dir, fresh)
        self._instagram_record_cache = fresh
        self._instagram = None
        remaining = instagram_token_mod.days_left(fresh, now=now)
        log.info("instagram token refreshed; %s day(s) left", remaining)
        if not self._instagram_notified:
            self._instagram_notified = True
            self._notify_owner(
                f"Instagram token refreshed automatically. {instagram_token_mod.expiry_note(fresh, now=now)}"
            )
        elif remaining is not None and remaining <= instagram_token_mod.WARN_DAYS:
            self._notify_owner(instagram_token_mod.expiry_note(fresh, now=now))
        return True

    # ------------------------------------------------------------------ run

    def run(self) -> None:
        self._startup()
        while True:
            failed = False
            try:
                self.poll_once()
            except telegram_mod.TelegramError as error:
                log.warning("Telegram API error: %s", error)
                failed = True
            except (ConnectionError, TimeoutError) as error:
                log.warning("Telegram connection problem, retrying: %s", error)
                failed = True
            except Exception:
                log.exception("unhandled error in the main loop")
                failed = True
            # Local maintenance runs in its own guarded steps: a Telegram
            # outage must not stop the console queue, the media jobs, or the
            # Instagram token refresh.
            self._maintenance()
            time.sleep(_CONNECTION_RETRY_SECONDS if failed else 1)

    def _maintenance(self) -> None:
        """Local work that never depends on the Telegram connection."""
        steps = (
            self.maybe_run_daily,
            self.maybe_run_routines,
            self.maybe_poll_media_jobs,
            self.maybe_refresh_instagram_token,
            lambda: panel_actions_mod.drain(self),
        )
        for step in steps:
            try:
                step()
            except telegram_mod.TelegramError as error:
                log.warning("maintenance step failed: %s", error)
            except Exception:  # noqa: BLE001 - one step must not stop the rest
                log.exception("maintenance step failed")

    def _startup(self) -> None:
        me = self._startup_identity()
        log.info("Content Bot started as @%s", me.get("username", "?"))
        try:
            self.api.delete_webhook()
        except telegram_mod.TelegramError:
            pass
        commands = [
            {"command": "start", "description": "Show available commands"},
            {"command": "help", "description": "Show available commands"},
            {"command": "status", "description": "Show configuration and counters"},
            {
                "command": "forget_link",
                "description": "Allow a published link to be drafted again",
            },
            {
                "command": "tools",
                "description": "List the shared tool registry entries",
            },
        ]
        for scope in (None, {"type": "all_private_chats"}):
            try:
                self.api.set_my_commands(commands, scope=scope)
            except telegram_mod.TelegramError:
                pass
        if not self.settings.telegram_users:
            log.warning("CONTENT_TELEGRAM_USERS is empty; no operator can approve drafts")
        if not self.settings.telegram_channel:
            log.warning("CONTENT_TELEGRAM_CHANNEL is empty; approvals cannot publish")
        if self.writer is None:
            log.warning("CONTENT_WRITER_BASE_URL is empty; drafts cannot be generated")
        self.maybe_refresh_instagram_token()

    def _startup_identity(self) -> dict:
        """Return getMe, retrying the transient failures seen at boot.

        A rejected token is reported immediately: retrying it would only delay
        the failure. Connection errors (wrapped as ``TelegramNetworkError``)
        are retried with a growing delay.
        """
        delay = _STARTUP_BACKOFF_SECONDS
        for attempt in range(1, _STARTUP_ATTEMPTS + 1):
            try:
                return self.api.get_me()
            except telegram_mod.TelegramNetworkError as error:
                if attempt >= _STARTUP_ATTEMPTS:
                    log.error(
                        "Telegram unreachable at startup after %s attempts: %s",
                        attempt,
                        error,
                    )
                    raise
                log.warning(
                    "Telegram unreachable at startup (attempt %s/%s): %s; retrying in %ss",
                    attempt,
                    _STARTUP_ATTEMPTS,
                    error,
                    delay,
                )
                time.sleep(delay)
                delay *= 2
            except telegram_mod.TelegramError as error:
                log.error("Bot token rejected: %s", error)
                raise
        raise telegram_mod.TelegramError("getMe did not return a result")

    # ---------------------------------------------------------------- polls

    def poll_once(self) -> int:
        updates = self.api.get_updates(offset=self._offset, timeout=25)
        for update in updates:
            update_id = int(update.get("update_id", 0))
            self._offset = max(self._offset, update_id + 1)
            try:
                self.handle_update(update)
            except (ConnectionError, TimeoutError) as error:
                log.warning("update %s hit a connection problem: %s", update_id, error)
            except Exception:
                log.exception("update %s failed", update_id)
        return len(updates)

    def handle_update(self, update: dict) -> None:
        message = update.get("message")
        if isinstance(message, dict):
            self.handle_message(message)
            return
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self.handle_callback(callback)

    # ------------------------------------------------------------- messages

    def _is_allowed(self, user_id) -> bool:
        return isinstance(user_id, int) and user_id in self.settings.telegram_users

    def handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        sender = (message.get("from") or {}).get("id")
        if not self._is_allowed(sender):
            log.debug("ignoring message from disallowed user %s", sender)
            return
        chat_id = chat.get("id")
        attachment = self._attachment_from_message(message)
        if attachment is not None:
            if chat_id is not None:
                self._receive_user_media(chat_id, attachment)
            return
        text = str(message.get("text") or "").strip()
        if not text:
            return
        reply_to = message.get("reply_to_message")
        if isinstance(reply_to, dict) and chat_id is not None:
            reply_from = reply_to.get("from") or {}
            reply_id = reply_to.get("message_id")
            if reply_id is not None and reply_from.get("is_bot") is True:
                draft = self.state.draft_for_message(chat_id, reply_id)
                if draft is not None:
                    self._save_feedback(draft, text)
                    self.api.send_message(
                        chat_id,
                        "Feedback saved. Press Reject to get a revised version, "
                        "or Approve to publish the post as it is.",
                    )
                    return
                self.api.send_message(
                    chat_id,
                    "This message is not an active proposal. Reply to a "
                    "proposal message to leave revision feedback.",
                )
                return
        if text in {"/start", "/help"}:
            self.api.send_message(chat_id, self.help_text())
            return
        if text == "/status":
            self.api.send_message(chat_id, self.status_text())
            return
        if text.split("@", 1)[0] == "/tools":
            self.api.send_message(chat_id, self.tools_text(), parse_mode="HTML")
            return
        if text.split("@", 1)[0] == "/instagram":
            refreshed = self.maybe_refresh_instagram_token(force=True)
            note = self.instagram_status_text()
            self.api.send_message(
                chat_id,
                f"{note}\n\nRefreshed just now." if refreshed else note,
            )
            return
        if text.startswith(("/forget-link", "/forget_link")):
            link_match = URL_RE.search(text)
            if link_match:
                self._forget_link(link_match.group(0), chat_id)
            else:
                self.api.send_message(
                    chat_id,
                    "Send /forget_link <url> to allow a previously published link to be drafted again.",
                )
            return
        if self._awaiting_draft(chat_id) is not None:
            self.api.send_message(
                chat_id,
                "A media upload is still pending. Press Cancel upload on the "
                "media question first, then send the link or topic again.",
            )
            return
        match = URL_RE.search(text)
        if match:
            self.request_on_demand(match.group(0), chat_id)
            return
        self.request_on_topic(text, chat_id)

    MAX_USER_MEDIA_BYTES = 20_000_000

    @staticmethod
    def _attachment_from_message(message: dict) -> dict | None:
        """Extract (kind, file_id, file_name) from a photo/video message."""
        photo = message.get("photo")
        if isinstance(photo, list):
            sizes = [p for p in photo if isinstance(p, dict) and p.get("file_id")]
            if sizes:
                best = max(
                    sizes,
                    key=lambda p: p.get("file_size") or p.get("width") or 0,
                )
                return {
                    "kind": "image",
                    "file_id": best["file_id"],
                    "file_name": "upload.jpg",
                    "file_size": int(best.get("file_size") or 0),
                }
        video = message.get("video")
        if isinstance(video, dict) and video.get("file_id"):
            mime = str(video.get("mime_type") or "").lower()
            name = str(video.get("file_name") or "")
            if not name:
                name = "video.webm" if "webm" in mime else "video.mp4"
            return {
                "kind": "video",
                "file_id": video["file_id"],
                "file_name": name,
                "file_size": int(video.get("file_size") or 0),
                "as_document": False,
            }
        document = message.get("document")
        if isinstance(document, dict) and document.get("file_id"):
            mime = str(document.get("mime_type") or "").lower()
            if mime.startswith("video/"):
                name = str(document.get("file_name") or "")
                if not name:
                    name = "video.webm" if "webm" in mime else "video.mp4"
                return {
                    "kind": "video",
                    "file_id": document["file_id"],
                    "file_name": name,
                    "file_size": int(document.get("file_size") or 0),
                    "as_document": True,
                }
        return None

    @staticmethod
    def _safe_extension(file_name: str, kind: str) -> str:
        base = Path(file_name or "").suffix.lstrip(".").lower()
        if base and re.fullmatch(r"[a-z0-9]{2,5}", base):
            return base
        return "jpg" if kind == "image" else "mp4"

    def _awaiting_draft(self, chat_id) -> dict | None:
        drafts = self.state.load().get("drafts") or {}
        for record in drafts.values():
            if (
                isinstance(record, dict)
                and record.get("chat_id") == chat_id
                and (
                    record.get("status") == "awaiting_media"
                    or (
                        record.get("status") == "media_ready"
                        and bool(record.get("media_wait_kind"))
                    )
                )
            ):
                return record
        return None

    def _receive_user_media(self, chat_id, attachment: dict) -> None:
        draft = self._awaiting_draft(chat_id)
        if draft is None:
            self.api.send_message(
                chat_id,
                "No draft is waiting for media. Send a link or a topic first, "
                "pick an image or video option, then send the file.",
            )
            return
        draft_id = str(draft.get("id") or "")
        expected = str(draft.get("media_wait_kind") or "")
        if expected and expected != attachment["kind"]:
            label = "image" if expected == "image" else "video"
            article = "an" if expected == "image" else "a"
            self.api.send_message(
                chat_id,
                f"This draft is waiting for {article} {label}; please send "
                f"{article} {label} file.",
            )
            return
        size = int(attachment.get("file_size") or 0)
        if (
            str(attachment.get("kind") or "") == "video"
            and size > self.MAX_USER_MEDIA_BYTES
        ):
            self._keep_telegram_media(draft, attachment)
            return
        try:
            file_info = self.api.get_file(str(attachment["file_id"]))
        except telegram_mod.TelegramError as error:
            if str(attachment.get("kind") or "") == "video":
                # Telegram never hands out download paths for files above its
                # bot download limit; the file id can still be republished.
                self._keep_telegram_media(draft, attachment)
                return
            self.api.send_message(chat_id, f"Could not download the file: {error}")
            return
        if int(file_info.get("file_size") or 0) > self.MAX_USER_MEDIA_BYTES:
            if str(attachment.get("kind") or "") == "video":
                self._keep_telegram_media(draft, attachment)
                return
            self.api.send_message(
                chat_id,
                "The file is larger than 20 MB; Telegram limits bot downloads. "
                "Send a smaller file.",
            )
            return
        file_path = str(file_info.get("file_path") or "")
        if not file_path:
            self.api.send_message(
                chat_id,
                "Telegram did not return a download path for that file.",
            )
            return
        try:
            content = self.api.download_file(
                file_path,
                max_bytes=self.MAX_USER_MEDIA_BYTES + 1_000_000,
            )
        except telegram_mod.TelegramError as error:
            self.api.send_message(chat_id, f"Could not download the file: {error}")
            return
        if not content:
            self.api.send_message(chat_id, "The downloaded file is empty.")
            return
        extension = self._safe_extension(
            str(attachment.get("file_name") or ""),
            str(attachment["kind"]),
        )
        if attachment["kind"] == "image":
            content = self._brand_uploaded_image(content, extension)
        media_dir = Path(self.settings.data_dir) / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        collecting = attachment["kind"] == "image" and bool(draft.get("media_collect"))
        previous_files: list = []
        if collecting:
            previous_files = list((draft.get("media") or {}).get("files") or [])
        if collecting:
            local_path = media_dir / f"{draft_id}-{len(previous_files) + 1}.{extension}"
        else:
            local_path = media_dir / f"{draft_id}.{extension}"
        try:
            local_path.write_bytes(content)
        except OSError as error:
            self.api.send_message(chat_id, f"Could not store the media file: {error}")
            return
        filename = local_path.name
        media = dict(draft.get("media") or {})
        if not collecting:
            media = {
                "kind": attachment["kind"],
                "driver": "user-upload",
                "status": "done",
                "artifact": filename,
                "local_path": str(local_path),
                "duration": "",
            }
        else:
            files = list(previous_files)
            files.append(
                {"name": filename, "local_path": str(local_path), "kind": "image"}
            )
            media = {
                "kind": "image",
                "driver": "user-upload",
                "status": "collecting",
                "artifact": filename,
                "local_path": str(local_path),
                "duration": "",
                "files": files,
                "count": len(files),
            }
        self.state.update_draft(
            draft_id,
            {
                "status": "media_ready",
                "media": media,
                "media_wait_kind": "image" if collecting else None,
                "media_collect": collecting or bool(draft.get("media_collect")),
            },
        )
        self._record_event(draft_id, "user_media_received", filename)
        if collecting:
            count = len(media["files"])
            ask_id = draft.get("ask_message_id")
            if ask_id is not None:
                self._edit_safe(
                    chat_id,
                    int(ask_id),
                    f"Photo {count} of up to 10 received. Send more photos or "
                    "press Done on the preview to stop collecting.",
                    telegram_mod.upload_wait_keyboard(draft_id),
                )
            self._drop_preview(chat_id, draft)
            if not self._send_media_preview(
                draft_id,
                keyboard=telegram_mod.user_media_preview_keyboard,
                collecting=True,
            ):
                self.api.send_message(
                    chat_id,
                    "Media received, but the preview could not be sent.",
                )
            return
        if str(media.get("kind") or "") == "video":
            self._ask_video_edit(draft_id, draft, media)
            return
        self._finish_user_media(draft_id, draft, media)

    _VIDEO_CONTENT_TYPES = {
        "mp4": "video/mp4",
        "m4v": "video/mp4",
        "mov": "video/quicktime",
        "webm": "video/webm",
        "mkv": "video/x-matroska",
        "avi": "video/x-msvideo",
    }

    def _video_content_type(self, path: Path) -> str:
        suffix = path.suffix.lstrip(".").lower()
        return self._VIDEO_CONTENT_TYPES.get(suffix, "video/mp4")

    def _ask_video_edit(self, draft_id: str, draft: dict, media: dict) -> None:
        """Ask whether an operator-recorded clip should be edited first.

        A file the operator sends is kept untouched until they answer; the
        usual Approve still decides whether the post is published.
        """
        chat_id = draft.get("chat_id")
        if chat_id is None:
            return
        try:
            sent = self.api.send_message(
                chat_id,
                "Video received. Should I edit it before publishing "
                "(normalise the file, cap the size, fix rotation), or publish "
                "it exactly as it is?",
                telegram_mod.video_edit_keyboard(draft_id),
            )
        except telegram_mod.TelegramError:
            log.warning("video edit question failed for draft %s", draft_id)
            self._finish_user_media(draft_id, draft, media)
            return
        self.state.update_draft(
            draft_id,
            {
                "status": "media_ready",
                "media": media,
                "video_edit_pending": True,
                "video_edit_ask_message_id": sent.get("message_id"),
            },
        )
        self._record_event(draft_id, "video_edit_asked")

    def _resolve_video_edit(
        self, query_id: str, sub: str, record: dict, draft_id: str
    ) -> None:
        """Handle "Edit it" / "Publish as-is" for one operator clip."""
        if not record.get("video_edit_pending"):
            self._safe_answer(query_id, "This clip was already handled.")
            return
        media = dict(record.get("media") or {})
        chat_id = record.get("chat_id")
        ask_id = record.get("video_edit_ask_message_id")
        if sub == "video_keep":
            self.state.update_draft(
                draft_id,
                {"video_edit_pending": False, "video_edit_choice": "as-is"},
            )
            self._record_event(draft_id, "video_kept_as_is")
            if chat_id is not None and ask_id is not None:
                self._edit_safe(
                    chat_id,
                    int(ask_id),
                    "Publishing the clip as it is.",
                )
            self._finish_user_media(draft_id, record, media)
            self._safe_answer(query_id, "The clip will publish unchanged.")
            return
        if self.media is None:
            self._safe_answer(
                query_id,
                "Media Studio is not configured (CONTENT_MEDIA_STUDIO_URL); "
                "choose Publish as-is instead.",
            )
            return
        local_path = str(media.get("local_path") or "")
        path = Path(local_path) if local_path else None
        if path is None or not path.is_file():
            self._safe_answer(
                query_id,
                "Editing needs the clip itself, but only Telegram holds it. "
                "Send a copy under 20 MB or choose Publish as-is.",
            )
            return
        try:
            content = path.read_bytes()
        except OSError as error:
            self._safe_answer(query_id, f"The clip could not be read: {error}")
            return
        try:
            upload_id = self.media.upload_video(
                content,
                filename=path.name,
                content_type=self._video_content_type(path),
            )
            job_id = self.media.submit(
                self.settings.video_edit_driver,
                "Prepare the operator-recorded clip for publishing.",
                params={"upload_id": upload_id},
            )
        except media_mod.MediaStudioError as error:
            self._record_event(draft_id, "video_edit_start_failed", str(error))
            self._safe_answer(
                query_id,
                f"Editing could not start: {error}. Choose Publish as-is to "
                "use the clip unchanged.",
            )
            return
        edit_source = {
            "artifact": str(media.get("artifact") or ""),
            "local_path": local_path,
            "file_id": str(media.get("file_id") or ""),
            "as_document": bool(media.get("as_document")),
        }
        self.state.update_draft(
            draft_id,
            {
                "status": "media_running",
                "video_edit_pending": False,
                "video_edit_choice": "edit",
                "media": {
                    "kind": "video",
                    "driver": self.settings.video_edit_driver,
                    "status": "running",
                    "job_id": job_id,
                    "artifact": "",
                    "local_path": "",
                    "duration": "",
                    "created_at": self.now_fn().isoformat(),
                    "edit_source": edit_source,
                },
            },
        )
        self._record_event(draft_id, "video_edit_started", job_id)
        if chat_id is not None and ask_id is not None:
            self._edit_safe(
                chat_id,
                int(ask_id),
                "Editing the clip now; the preview follows when it is ready.",
            )
        self._safe_answer(query_id, "Editing started.")

    def _finish_user_media(self, draft_id: str, draft: dict, media: dict) -> None:
        """Refresh the previews after an upload landed on the draft."""
        chat_id = draft.get("chat_id")
        if chat_id is None:
            return
        ask_id = draft.get("ask_message_id")
        if ask_id is not None:
            self._delete_safe(chat_id, int(ask_id))
            self.state.update_draft(draft_id, {"ask_message_id": None})
        text_id = draft.get("text_message_id")
        if text_id is not None:
            current = self.state.get_draft(draft_id)
            if current is None:
                current = dict(draft)
                current["media"] = media
            self._edit_safe(
                chat_id,
                int(text_id),
                f"{self.preview_text(current)}\n\n"
                "Media received. Press Approve on the media message to publish.",
                self._approval_keyboard(draft_id),
            )
        if not self._send_media_preview(
            draft_id,
            keyboard=telegram_mod.user_media_preview_keyboard,
        ):
            self.api.send_message(
                chat_id,
                "Media received, but the preview could not be sent.",
            )

    def _keep_telegram_media(self, draft: dict, attachment: dict) -> None:
        """Attach a file that is too large for the Bot API to download.

        Telegram only serves downloads up to 20 MB, but a bot may send a file
        it has received back to any chat by its file id. The draft therefore
        keeps the id instead of local bytes, the preview re-sends it, and the
        Telegram publish forwards it; Instagram and the upload packages need
        the file itself and ask for a smaller copy.
        """
        draft_id = str(draft.get("id") or "")
        chat_id = draft.get("chat_id")
        filename = str(attachment.get("file_name") or "video.mp4")
        size = int(attachment.get("file_size") or 0)
        media = {
            "kind": str(attachment.get("kind") or "video"),
            "driver": "user-upload",
            "status": "done",
            "artifact": filename,
            "local_path": "",
            "file_id": str(attachment.get("file_id") or ""),
            "size": size,
            "oversized": True,
            "as_document": bool(attachment.get("as_document")),
            "duration": "",
        }
        self.state.update_draft(
            draft_id,
            {
                "status": "media_ready",
                "media": media,
                "media_wait_kind": None,
            },
        )
        self._record_event(draft_id, "user_media_kept_on_telegram", filename)
        if chat_id is not None:
            self.api.send_message(
                chat_id,
                "This file is over the 20 MB Telegram bot download limit, so it "
                "stays on Telegram instead of being copied into the bot. It "
                "will still publish to the Telegram channel; Instagram and the "
                "upload packages need the file itself, so send a copy under "
                "20 MB when you need those.",
            )
        self._finish_user_media(draft_id, draft, media)

    _IMAGE_CONTENT_TYPES = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }

    def _brand_uploaded_image(self, content: bytes, extension: str) -> bytes:
        """Ask Media Studio for its brand chip; keep the upload on failure."""
        media = getattr(self, "media", None)
        if media is None or not hasattr(media, "brand_image"):
            return content
        content_type = self._IMAGE_CONTENT_TYPES.get(
            str(extension or "").lower(),
            "image/png",
        )
        try:
            branded = media.brand_image(content, content_type=content_type)
        except media_mod.MediaStudioError as error:
            log.warning("uploaded image branding skipped: %s", error)
            return content
        return branded or content

    _VIDEO_CHARACTER_TEMPLATE = (
        "{handle} is speaking directly to the camera in a clean modern studio. "
        "Medium shot, chest-up framing. Eye-level camera. Soft diffused frontal "
        "lighting with gentle fill. Natural healthy skin texture. Relaxed and "
        "confident posture. Natural blinking and subtle head movements. Very "
        "subtle realistic hand gestures. Use {handle} consistently throughout "
        "the video. Maintain the existing character appearance and youthful "
        "look. Keep the under-eye area soft. Keep facial expressions natural "
        "and subtle. Avoid harsh shadows or exaggerated facial lines. Use "
        "{handle}'s assigned character voice. Natural contemporary Persian "
        "delivery. Accurate lip sync. Clean studio audio. Single continuous "
        "shot. No camera cuts. No subtitles. No captions. No text on screen. "
        "No background music. Photorealistic. Keep the delivery natural, calm, "
        "and conversational. Aspect: {aspect}."
    )
    _PROMPT_ASK_MAX = 3400

    def _video_character_block(self) -> str:
        """Return the reusable Flow character description."""
        handle = self.settings.video_character or "the character"
        template = (
            self.settings.video_character_prompt or self._VIDEO_CHARACTER_TEMPLATE
        )
        return template.replace("{handle}", handle).replace(
            "{aspect}", self.settings.video_aspect
        )

    def _video_beats(self, record: dict, *, seconds: int) -> tuple[list[dict], bool]:
        """Return one script beat per segment and whether a model wrote it."""
        segment_seconds = max(int(self.settings.video_segment_seconds or 10), 5)
        count = max(1, int(round(seconds / segment_seconds)))
        body = str(record.get("body") or "")
        source_url = str(record.get("source_url") or "")
        if source_url:
            body = writer_mod.Writer._strip_source_url(body, source_url)
        writer = self.writer
        if writer is not None and hasattr(writer, "video_script"):
            try:
                beats = writer.video_script(
                    title=str(record.get("title") or ""),
                    body=body,
                    source_url=source_url,
                    segments=count,
                )
            except writer_mod.WriterError as error:
                log.warning("video script generation failed: %s", error)
            else:
                if beats:
                    return beats[:count], True
        chunks = self._fallback_beats(body, count)
        return [{"say": chunk, "visual": ""} for chunk in chunks], False

    @staticmethod
    def _fallback_beats(text: str, count: int) -> list[str]:
        """Split the post text into even chunks when no script model is left."""
        words = [word for word in re.sub(r"\s+", " ", str(text or "")).split(" ") if word]
        if not words:
            return [""] * count
        size = max(1, -(-len(words) // count))
        chunks: list[str] = []
        for index in range(count):
            start = index * size
            end = len(words) if index == count - 1 else start + size
            chunks.append(" ".join(words[start:end]).strip())
        return chunks

    def _video_prompt_package(
        self,
        record: dict,
        *,
        beats: list[dict],
        style: str,
        seconds: int,
    ) -> str:
        """Build one copy-ready prompt package with a prompt per segment."""
        aspect = self.settings.video_aspect
        handle = self.settings.video_character or "the character"
        blocks: list[str] = []
        for index, beat in enumerate(beats, start=1):
            say = str(beat.get("say") or "").strip()
            visual = str(beat.get("visual") or "").strip()
            lines: list[str] = []
            if style == "character":
                if index == 1:
                    lines.append("Segment 1 prompt:")
                else:
                    lines.append(
                        f"Segment {index} prompt (press Extend, then paste):"
                    )
                lines.append(self._video_character_block())
                if index > 1:
                    lines.append(
                        "Continue directly from the last frame of the previous "
                        "clip. Same framing, same lighting, single continuous "
                        "take, no cuts."
                    )
                if say:
                    lines.append(
                        f"{handle} speaks naturally in Persian and says "
                        f'exactly: "{say}"'
                    )
                lines.append(
                    "Ends with a short natural pause. No subtitles, no captions, "
                    "no text on screen, no background music. Photorealistic."
                )
            else:
                shot = visual or (
                    "A clean cinematic shot that illustrates this part of the "
                    "story"
                )
                tail = (
                    f"Clean modern look, photorealistic, {aspect}, no text "
                    "overlays, no watermark, no logos. Single continuous shot, "
                    "no camera cuts."
                )
                if index == 1:
                    lines.append("Segment 1 prompt:")
                    lines.append(f"{shot} {tail}")
                else:
                    lines.append(
                        f"Segment {index} prompt (press Extend, then paste):"
                    )
                    lines.append(
                        f"Continue directly from the last frame. {shot} Same "
                        f"look and lighting. {tail}"
                    )
                if say:
                    lines.append(f'Optional Persian voiceover line: "{say}"')
            blocks.append("\n".join(lines))
        header = (
            f"Reel package: {len(blocks)} segment(s), about {seconds} seconds "
            f"total, {aspect}."
        )
        return "\n\n\n".join([header] + blocks)

    @staticmethod
    def _prompt_chunks(package: str, limit: int = 3000) -> list[str]:
        """Split a prompt package into one copy-ready message per segment."""
        chunks: list[str] = []
        for part in str(package or "").split("\n\n\n"):
            part = part.strip("\n")
            while len(part) > limit:
                chunks.append(part[:limit].rstrip())
                part = part[limit:].lstrip()
            if part:
                chunks.append(part)
        return chunks

    def _send_video_prompt_package(
        self,
        draft_id: str,
        *,
        query_id: str,
        seconds: int,
    ) -> None:
        """Send the segmented Flow package and wait for the finished video."""
        record = self.state.get_draft(draft_id)
        if record is None:
            self._safe_answer(query_id, "This draft is no longer active.")
            return
        style = str(record.get("video_style") or "ai")
        beats, from_model = self._video_beats(record, seconds=seconds)
        package = self._video_prompt_package(
            record,
            beats=beats,
            style=style,
            seconds=seconds,
        )
        footer = (
            "Reel prompt package for this draft. Build the segments with the "
            "tool of your choice (for example Google Flow): paste segment 1, "
            "press Extend for the following segments, join the parts if you "
            "want one file, then send the finished video here."
        )
        if not from_model:
            footer += " (No script model was available; the lines come from the post text.)"
        chat_id = record.get("chat_id")
        ask_text = f"{footer}\n\n<pre>{_html_escape(package)}</pre>"
        inline = len(ask_text) <= self._PROMPT_ASK_MAX
        chunks: list[str] = []
        if not inline:
            header, _, body_text = package.partition("\n\n\n")
            ask_text = f"{footer}\n\n{_html_escape(header)}"
            chunks = self._prompt_chunks(body_text)
        if not self._begin_user_media_wait(
            record,
            kind="video",
            ask_text=ask_text,
        ):
            self._safe_answer(query_id, "Another media upload is already waiting.")
            return
        if chunks and chat_id is not None:
            for chunk in chunks:
                try:
                    self.api.send_message(
                        chat_id,
                        f"<pre>{_html_escape(chunk)}</pre>",
                        parse_mode="HTML",
                    )
                except telegram_mod.TelegramError as error:
                    log.warning("video prompt chunk failed: %s", error)
                    break
        self._safe_answer(query_id, "Prompt package sent; waiting for your video file.")

    def _begin_user_image_wait(
        self,
        record: dict,
        query_id: str,
        *,
        multi: bool,
    ) -> None:
        """Point the media ask message at a single or multi-photo upload."""
        draft_id = str(record.get("id") or "")
        chat_id = record.get("chat_id")
        ask_id = record.get("ask_message_id")
        if record.get("status") == "awaiting_media":
            self._safe_answer(query_id, "Another media upload is already waiting.")
            return
        if chat_id is None or ask_id is None:
            self._safe_answer(query_id, "Another media upload is already waiting.")
            return
        state: dict = {
            "status": "awaiting_media",
            "media_wait_kind": "image",
            "media_collect": multi,
        }
        self.state.update_draft(draft_id, state)
        if multi:
            self._edit_safe(
                chat_id,
                int(ask_id),
                "Send several photos for this post. Send them one by one; "
                "press Done on the preview when finished (up to 10, published "
                "as an Instagram carousel and a Telegram album).",
                telegram_mod.upload_wait_keyboard(draft_id),
            )
            self._record_event(draft_id, "media_collect_start")
            self._safe_answer(
                query_id,
                "Send photos; press Done on the preview when finished.",
            )
        else:
            self._edit_safe(
                chat_id,
                int(ask_id),
                "Send the photo for this post now. It will be attached "
                "to the draft for approval.",
                telegram_mod.upload_wait_keyboard(draft_id),
            )
            self._safe_answer(query_id, "Send the photo.")

    def _begin_user_media_wait(
        self,
        record: dict,
        *,
        kind: str,
        ask_text: str,
    ) -> bool:
        """Point the media ask message at an upload; returns False when busy."""
        draft_id = str(record.get("id") or "")
        chat_id = record.get("chat_id")
        ask_id = record.get("ask_message_id")
        if record.get("status") == "awaiting_media":
            return False
        if chat_id is None or ask_id is None:
            return False
        self._edit_safe(
            chat_id,
            int(ask_id),
            ask_text,
            telegram_mod.upload_wait_keyboard(draft_id),
        )
        self.state.update_draft(
            draft_id,
            {"status": "awaiting_media", "media_wait_kind": kind},
        )
        self._record_event(draft_id, "media_upload_wait", kind)
        return True

    def _forget_link(self, url: str, chat_id) -> None:
        digest = canonical_content_hash(canonicalize_url(url))
        if self.state.forget_published(digest):
            self.api.send_message(
                chat_id,
                "Link forgotten; it can be drafted and published again.",
            )
        else:
            self.api.send_message(
                chat_id,
                "No published record found for that link.",
            )

    def help_text(self) -> str:
        lines = [
            "Content Bot commands:",
            "/start or /help - this message",
            "/status - configuration and counters",
            "/tools - shared tool registry entries",
            "/instagram - Instagram token expiry and an immediate refresh",
            "/forget_link <url> - allow a published link to be drafted again",
            "Send any http(s) link - draft a post with Approve/Reject buttons",
            "Send a topic without a link - search the web and draft a post",
            "After a draft choose Text only, AI image, send your own image,",
            "or get a video prompt and send the finished file back; then",
            "approve the media preview.",
        ]
        if self.settings.platforms_enabled:
            lines.append(
                "More platforms... on a preview sends a copy-ready package for "
                "YouTube, Aparat, or any platform added to editorial-policy.yaml."
            )
        if self._routines_summary() != "not configured":
            lines.append(
                "Scheduled routines run from the policy routines: list; "
                "/status shows the active ones."
            )
        lines.append(
            "Reply to a proposal with edit notes, then press Reject to revise; "
            "press Reject without notes to discard. Approved drafts are "
            "published to the configured Telegram channel with any media."
        )
        return "\n".join(lines)

    def status_text(self) -> str:
        state = self.state.load()
        drafts = len(state.get("drafts") or {})
        published = len(state.get("published") or [])
        today = int(state.get("published_today", 0))
        return (
            "Content Bot status\n"
            f"Operator users: {len(self.settings.telegram_users)}\n"
            f"Publish channel: {self.settings.telegram_channel or 'not configured'}\n"
            f"Writer endpoint: {self.settings.writer_base_url or 'not configured'}\n"
            f"Media Studio: {self.settings.media_studio_url or 'not configured'}\n"
            f"Web search: {'enabled' if self.settings.search_enabled else 'disabled'}\n"
            f"Tool registry: {self._tools_summary()}\n"
            f"Scheduled routines: {self._routines_summary()}\n"
            f"Platform packages: {'enabled' if self.settings.platforms_enabled else 'disabled'}\n"
            f"Instagram auto publish: {'enabled' if self.settings.instagram_publish_enabled else 'disabled'}\n"
            f"Pending drafts: {drafts}\n"
            f"Published today: {today}\n"
            f"Total published: {published}"
        )

    def _registry_entries(self) -> tuple:
        """Registry entries this bot may call, if a registry was loaded."""
        registry = self.tools
        if registry is None:
            return ()
        return registry.for_consumer("bot")

    def _tools_summary(self) -> str:
        registry = self.tools
        if registry is None:
            return "not configured (add tools.json to the policy directory)"
        return f"{len(self._registry_entries())} entries for this bot"

    def _routines_summary(self) -> str:
        """One line about the enabled scheduled routines in the policy."""
        try:
            policy = workflow.load_policy(self.settings.policy_dir)
        except Exception:  # noqa: BLE001
            return "not configured"
        routines = [
            item
            for item in (policy.get("routines") or [])
            if isinstance(item, dict)
            and str(item.get("id") or "").strip()
            and item.get("enabled") is not False
        ]
        if not routines:
            return "not configured"
        labels = sorted({str(item.get("platform") or item["id"]) for item in routines})
        return f"{len(routines)} active ({', '.join(labels)})"

    def tools_text(self) -> str:
        """Operator view of the shared tool registry."""
        registry = self.tools
        if registry is None:
            return (
                "No tool registry is configured. Add tools.json next to "
                "editorial-policy.yaml (the repository ships a default)."
            )
        entries = self._registry_entries()
        if not entries:
            return "The tool registry holds no entry for this bot."
        missing = {
            tool_id: env_name
            for tool_id, env_name in registry.missing_credentials(os.environ)
            if registry.find(tool_id) in entries
        }
        lines = ["Tool registry", f"Source: {registry.path}", ""]
        for tool in entries:
            state = "ready"
            if tool.auth_env and tool.id in missing:
                state = f"missing {tool.auth_env}"
            elif tool.auth_env:
                state = f"{tool.auth_type} credential set"
            lines.append(
                f"<b>{_html_escape(tool.id)}</b> [{_html_escape(tool.kind)}] "
                f"- {state}"
            )
            lines.append(f"  {_html_escape(tool.title)}: {_html_escape(tool.endpoint())}")
            if tool.capabilities:
                lines.append(
                    f"  capabilities: {_html_escape(', '.join(tool.capabilities))}"
                )
            if tool.notes:
                lines.append(f"  {_html_escape(tool.notes)}")
        warnings = [w for w in registry.warnings]
        if warnings:
            lines.append("")
            lines.append("Warnings: " + "; ".join(_html_escape(w) for w in warnings))
        return "\n".join(lines)

    def request_on_demand(self, url: str, chat_id) -> None:
        try:
            html_text = self.fetch_page(url)
        except fetch.FetchError as error:
            self.api.send_message(chat_id, f"Could not fetch the link: {error}")
            return
        article = extract.extract_article(html_text, url)
        body_text = article["text"]
        if len(body_text) < MIN_ARTICLE_CHARS and self.settings.search_enabled:
            self.api.send_message(
                chat_id,
                "The page has little readable text; searching for more context.",
            )
            try:
                results = self.search_topic(
                    article["title"] or url,
                    limit=self.settings.search_max_results,
                    timeout=self.settings.search_timeout,
                )
            except search_mod.SearchError as error:
                self.api.send_message(chat_id, f"Search failed: {error}")
                return
            if not results:
                self.api.send_message(
                    chat_id,
                    "The page did not contain enough readable text to work with.",
                )
                return
            body_text = "\n\n".join(
                [body_text]
                + [
                    f"{result['title']}\n{result['snippet']}"
                    for result in results
                    if result.get("snippet") or result.get("title")
                ]
            )
        raw_item = {
            "title": article["title"] or url,
            "url": url,
            "published_at": article["published_at"],
            "summary": article["description"],
            "text": body_text,
            "source": None,
            "category": "",
            "tags": [],
        }
        self._accept_item(raw_item, chat_id)

    def request_on_topic(self, query: str, chat_id) -> None:
        """Draft from a plain topic by searching the web for context."""
        query = (query or "").strip()
        if not query:
            return
        if not self.settings.topic_drafts_enabled:
            self.api.send_message(
                chat_id,
                "Topic drafting is disabled (CONTENT_TOPIC_DRAFTS_ENABLED=false).",
            )
            return
        if not self.settings.search_enabled:
            self.api.send_message(
                chat_id,
                "Web search is disabled (CONTENT_SEARCH_ENABLED=false).",
            )
            return
        self.api.send_message(chat_id, f"Searching for: {query}")
        try:
            results = self.search_topic(
                query,
                limit=self.settings.search_max_results,
                timeout=self.settings.search_timeout,
            )
        except search_mod.SearchError as error:
            self.api.send_message(chat_id, f"Search failed: {error}")
            return
        if not results:
            self.api.send_message(
                chat_id,
                "No usable search results were found for that topic.",
            )
            return
        first = results[0]
        body_text = "\n\n".join(
            f"{result['title']}\n{result['snippet']}"
            for result in results
            if result.get("snippet") or result.get("title")
        )
        raw_item = {
            "title": first.get("title") or query,
            "url": first.get("url") or "",
            "published_at": "",
            "summary": first.get("snippet") or "",
            "text": body_text,
            "source": "search",
            "category": "",
            "tags": [],
        }
        self._accept_item(raw_item, chat_id)

    def _accept_item(self, raw_item: dict, chat_id) -> None:
        """Run the shared editorial pipeline for one operator-sent item."""
        policy = workflow.load_policy(self.settings.policy_dir)
        on_demand = workflow.on_demand_settings(policy)
        item, rejection = workflow.evaluate_single(
            raw_item,
            policy,
            now=self.now_fn(),
            enforce_freshness=bool(on_demand.get("enforce_freshness", False)),
        )
        if item is None:
            label = workflow.rejection_label(rejection or {})
            self.api.send_message(chat_id, f"Rejected before drafting: {label}")
            return
        content_hash = str(item.get("content_hash") or "")
        if content_hash and self.state.is_known(content_hash):
            self.api.send_message(chat_id, "This link was already published before.")
            return
        self.send_draft(item, chat_id, kind="on_demand")

    def send_draft(self, item: dict, chat_id, *, kind: str):
        """Draft one item and send it for approval; returns the draft id."""
        if self.writer is None:
            self.api.send_message(
                chat_id,
                "No writer endpoint is configured; add CONTENT_WRITER_BASE_URL and restart.",
            )
            return None
        try:
            lessons = self.state.lessons(6)
            guidance = "\n".join(f"- {lesson}" for lesson in lessons)
            post = self.writer.generate_post(item, guidance=guidance)
        except writer_mod.WriterError as error:
            self.api.send_message(chat_id, f"Copy generation failed: {error}")
            return None
        draft_id = secrets.token_urlsafe(9)
        record = {
            "id": draft_id,
            "kind": kind,
            "chat_id": chat_id,
            "title": post["title"],
            "body": post["body"],
            "source_url": post["source_url"] or str(item.get("url") or ""),
            "source_title": str(item.get("title") or ""),
            "canonical_url": str(item.get("canonical_url") or ""),
            "content_hash": str(item.get("content_hash") or ""),
            "category": str(item.get("category") or ""),
            "created_at": self.now_fn().isoformat(),
            "status": "text",
            "text_message_id": None,
            "ask_message_id": None,
            "media": None,
            "history": [],
        }
        sent = self.api.send_message(
            chat_id,
            self.preview_text(record),
            self._approval_keyboard(draft_id),
            parse_mode="HTML",
        )
        record["message_id"] = sent.get("message_id")
        record["text_message_id"] = sent.get("message_id")
        self.state.add_draft(draft_id, record)
        self._record_event(draft_id, "draft_sent")
        if kind == "on_demand" and self.media is not None:
            self._send_media_ask(record)
        return draft_id

    def _send_media_ask(self, record: dict) -> None:
        """Offer media generation for one fresh on-demand draft."""
        draft_id = str(record["id"])
        chat_id = record.get("chat_id")
        try:
            ask = self.api.send_message(
                chat_id,
                "Add media to this post? Text only / AI image / send your own "
                "image / video prompt (you create the video).",
                telegram_mod.media_choice_keyboard(draft_id),
            )
        except telegram_mod.TelegramError:
            log.warning("media ask could not be sent for draft %s", draft_id)
            return
        self.state.update_draft(
            draft_id,
            {"ask_message_id": ask.get("message_id"), "status": "media_ask"},
        )
        self._record_event(draft_id, "media_ask_sent")

    def _record_event(self, draft_id: str, event: str, detail: str = "") -> None:
        """Append one audit event to a draft without failing the caller."""
        try:
            record = self.state.get_draft(draft_id)
            if record is None:
                return
            history = list(record.get("history") or [])
            history.append(
                {
                    "at": self.now_fn().isoformat(),
                    "event": event,
                    "detail": str(detail)[:400],
                }
            )
            self.state.update_draft(draft_id, {"history": history})
        except Exception:  # noqa: BLE001
            log.debug("history event skipped for draft %s", draft_id)

    # ------------------------------------------------------------- media

    def _media_callback(self, query_id: str, sub: str, draft_id: str) -> None:
        record = self.state.get_draft(draft_id)
        if record is None:
            self._safe_answer(query_id, "This draft is no longer active.")
            return
        if sub == "none":
            self.state.update_draft(
                draft_id,
                {
                    "status": "text_only",
                    "media": {"kind": "none"},
                    "media_duration_asked": False,
                    "media_wait_kind": None,
                },
            )
            self._record_event(draft_id, "media_none")
            self._safe_answer(
                query_id,
                "Text-only post. Press Approve on the draft message to publish.",
            )
            return
        if sub == "cancel_upload":
            if record.get("status") != "awaiting_media":
                self._safe_answer(query_id, "No upload is waiting for this draft.")
                return
            chat_id = record.get("chat_id")
            ask_id = record.get("ask_message_id")
            self.state.update_draft(
                draft_id,
                {"status": "media_ask", "media_wait_kind": None},
            )
            self._record_event(draft_id, "media_upload_cancelled")
            if chat_id is not None and ask_id is not None:
                self._edit_safe(
                    chat_id,
                    int(ask_id),
                    "Should I also create media for this post?",
                    telegram_mod.media_choice_keyboard(draft_id),
                )
            self._safe_answer(query_id, "Upload cancelled.")
            return
        if sub in {"video_keep", "video_edit"}:
            self._resolve_video_edit(query_id, sub, record, draft_id)
            return
        if sub == "user_image":
            self._begin_user_image_wait(record, query_id, multi=False)
            return
        if sub == "user_images":
            self._begin_user_image_wait(record, query_id, multi=True)
            return
        if sub == "video_prompt":
            ask_id = record.get("ask_message_id")
            chat_id = record.get("chat_id")
            if chat_id is None or ask_id is None:
                self._safe_answer(query_id, "No active media question was found.")
                return
            if not self.settings.video_character_enabled:
                self.state.update_draft(draft_id, {"video_style": "ai"})
                self._edit_safe(
                    chat_id,
                    int(ask_id),
                    "How long should the reel be?",
                    telegram_mod.video_prompt_duration_keyboard(draft_id),
                )
                self._safe_answer(query_id, "Choose a length.")
                return
            self._edit_safe(
                chat_id,
                int(ask_id),
                "Should the reel use your saved Flow character or pure AI shots?",
                telegram_mod.video_style_keyboard(draft_id, character=True),
            )
            self._safe_answer(query_id, "Choose the reel style.")
            return
        if sub in {"vstyle_char", "vstyle_ai"}:
            style = "character" if sub == "vstyle_char" else "ai"
            ask_id = record.get("ask_message_id")
            chat_id = record.get("chat_id")
            if style == "character" and not self.settings.video_character_enabled:
                self._safe_answer(
                    query_id,
                    "No character is configured (CONTENT_VIDEO_CHARACTER).",
                )
                return
            if chat_id is None or ask_id is None:
                self._safe_answer(query_id, "No active media question was found.")
                return
            self.state.update_draft(draft_id, {"video_style": style})
            self._edit_safe(
                chat_id,
                int(ask_id),
                "How long should the reel be?",
                telegram_mod.video_prompt_duration_keyboard(draft_id),
            )
            self._safe_answer(query_id, "Choose a length.")
            return
        if sub in {"script10", "script30"}:
            self._send_video_prompt_package(
                draft_id,
                query_id=query_id,
                seconds=10 if sub == "script10" else 30,
            )
            return
        if sub == "video":
            ask_id = record.get("ask_message_id")
            chat_id = record.get("chat_id")
            if chat_id is None or ask_id is None:
                self._safe_answer(query_id, "No active media question was found.")
                return
            if record.get("media_duration_asked"):
                self.state.update_draft(draft_id, {"media_duration_asked": False})
                self._start_media_job(
                    draft_id,
                    self.settings.video_driver,
                    "video",
                    "",
                    query_id=query_id,
                )
                return
            self.state.update_draft(draft_id, {"media_duration_asked": True})
            self._edit_safe(
                chat_id,
                int(ask_id),
                "Choose an approximate video length.",
                telegram_mod.media_duration_keyboard(draft_id),
            )
            self._safe_answer(query_id, "Choose a duration.")
            return
        media = record.get("media") or {}
        if sub == "done":
            if not bool(record.get("media_collect")) or str(media.get("kind") or "") != "image":
                self._safe_answer(query_id, "No photo collection is active for this draft.")
                return
            collected = list(media.get("files") or [])
            if not collected:
                self._safe_answer(query_id, "Send at least one photo first.")
                return
            first = collected[0]
            self.state.update_draft(
                draft_id,
                {
                    "status": "media_ready",
                    "media": {
                        "kind": "image",
                        "driver": "user-upload",
                        "status": "done",
                        "artifact": str(first.get("name") or ""),
                        "local_path": str(first.get("local_path") or ""),
                        "duration": "",
                        "files": collected,
                        "count": len(collected),
                    },
                    "media_wait_kind": None,
                    "media_collect": True,
                },
            )
            self._record_event(draft_id, "media_collect_done", str(len(collected)))
            chat_id = record.get("chat_id")
            text_id = record.get("text_message_id")
            if text_id is not None:
                current = self.state.get_draft(draft_id)
                if current is not None:
                    self._edit_safe(
                        chat_id,
                        int(text_id),
                        f"{self.preview_text(current)}\n\n"
                        f"Photo collection closed with {len(collected)} photos; "
                        "press Approve on the preview to publish.",
                        self._approval_keyboard(draft_id),
                    )
            self._drop_preview(chat_id, record)
            if not self._send_media_preview(
                draft_id,
                keyboard=telegram_mod.user_media_preview_keyboard,
            ):
                self._safe_answer(query_id, "Preview could not be sent.")
                return
            self._safe_answer(query_id, f"Collection closed with {len(collected)} photos.")
            return
        if sub == "retry":
            driver = str(media.get("driver") or self.settings.image_driver)
            kind = str(media.get("kind") or "image")
            duration = str(media.get("duration") or "")
            self._start_media_job(
                draft_id,
                driver,
                kind,
                duration,
                query_id=query_id,
            )
            return
        if sub == "image":
            self._start_media_job(
                draft_id,
                self.settings.image_driver,
                "image",
                "",
                query_id=query_id,
            )
            return
        if sub == "video30":
            self._start_media_job(
                draft_id,
                self.settings.video_driver,
                "video",
                "up_to_30_seconds",
                query_id=query_id,
            )
            return
        self._safe_answer(query_id, "Unknown media choice.")

    def _start_media_job(
        self,
        draft_id: str,
        driver: str,
        kind: str,
        duration: str,
        *,
        query_id: str = "",
    ) -> None:
        if self.media is None:
            self._safe_answer(
                query_id,
                "Media Studio is not configured (CONTENT_MEDIA_STUDIO_URL).",
            )
            return
        record = self.state.get_draft(draft_id)
        if record is None:
            self._safe_answer(query_id, "This draft is no longer active.")
            return
        chat_id = record.get("chat_id")
        ask_id = record.get("ask_message_id")
        title = str(record.get("title") or "").strip()
        body = re.sub(r"\s+", " ", str(record.get("body") or "")).strip()
        if not body and title:
            body = title
        duration_hint = ""
        if duration == "up_to_30_seconds":
            duration_hint = " Aim for a clip under 30 seconds."
        prompt = (
            f"Create a {kind} that illustrates the following social media post. "
            f"Do not include text overlays or watermarks.{duration_hint}\n"
            f"Title: {title}\nPost: {body[:800]}"
        )
        try:
            job_id = self.media.submit(driver, prompt, params={"duration": duration})
        except media_mod.MediaStudioError as error:
            self._record_event(draft_id, "media_submit_failed", str(error))
            if chat_id is not None and ask_id is not None:
                self._edit_safe(
                    chat_id,
                    int(ask_id),
                    f"Media job could not start: {error}",
                    telegram_mod.media_retry_keyboard(draft_id),
                )
            self._safe_answer(query_id, f"Media job failed: {error}")
            return
        self.state.update_draft(
            draft_id,
            {
                "status": "media_running",
                "media_duration_asked": False,
                "media": {
                    "kind": kind,
                    "driver": driver,
                    "duration": duration,
                    "job_id": job_id,
                    "status": "running",
                    "artifact": "",
                    "local_path": "",
                    "created_at": self.now_fn().isoformat(),
                },
            },
        )
        self._record_event(draft_id, "media_job_started", f"{driver} {job_id}")
        self._drop_preview(chat_id, record)
        if chat_id is not None and ask_id is not None:
            self._edit_safe(
                chat_id,
                int(ask_id),
                f"Creating the {kind} now; this can take several minutes.",
            )
        self._safe_answer(query_id, f"{kind.capitalize()} job started.")

    def maybe_poll_media_jobs(self) -> None:
        """Advance drafts whose media job finished while the bot polled."""
        if self.media is None:
            return
        now = self.now_fn()
        for draft_id, record in list((self.state.load().get("drafts") or {}).items()):
            if not isinstance(record, dict) or record.get("status") != "media_running":
                if (
                    isinstance(record, dict)
                    and record.get("status") == "media_ready"
                    and not record.get("preview_message_id")
                    and not record.get("video_edit_pending")
                ):
                    if not self._send_media_preview(draft_id):
                        self._media_failed(draft_id, "Media preview could not be sent.")
                continue
            media = record.get("media") or {}
            job_id = str(media.get("job_id") or "")
            if not job_id:
                continue
            created = self._parse_dt(media.get("created_at"))
            if created is not None and (now - created).total_seconds() > self.settings.media_job_timeout_seconds:
                self._media_failed(draft_id, "Media job timed out.")
                continue
            try:
                job = self.media.job(job_id)
            except media_mod.MediaStudioError as error:
                log.warning("media job %s lookup failed: %s", job_id, error)
                continue
            status = str(job.get("status") or "")
            if status in {"queued", "running", ""}:
                continue
            if status == "done":
                self._media_finished(draft_id, job)
            else:
                detail = str(job.get("error") or "media job failed")
                self._media_failed(draft_id, detail)

    def _media_finished(self, draft_id: str, job: dict) -> None:
        record = self.state.get_draft(draft_id)
        if record is None:
            return
        artifact = self.media.pick_artifact(job)
        if artifact is None:
            self._media_failed(draft_id, "Job finished without a usable media artifact.")
            return
        name, kind = artifact
        job_id = str((record.get("media") or {}).get("job_id") or "")
        try:
            content = self.media.download(job_id, name)
        except media_mod.MediaStudioError as error:
            self._media_failed(draft_id, str(error))
            return
        extension = name.rsplit(".", 1)[-1].lower() if "." in name else ("mp4" if kind == "video" else "png")
        media_dir = Path(self.settings.data_dir) / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        local_path = str(media_dir / f"{draft_id}.{extension}")
        try:
            Path(local_path).write_bytes(content)
        except OSError as error:
            self._media_failed(draft_id, f"Could not store the media file: {error}")
            return
        updated_media = dict(record.get("media") or {})
        updated_media.update(
            {
                "status": "done",
                "artifact": name,
                "kind": kind,
                "local_path": local_path,
            }
        )
        self.state.update_draft(
            draft_id,
            {"status": "media_ready", "media": updated_media},
        )
        self._record_event(draft_id, "media_ready", name)
        chat_id = record.get("chat_id")
        text_id = record.get("text_message_id")
        ask_id = record.get("ask_message_id")
        edited = str(updated_media.get("driver") or "") == str(
            self.settings.video_edit_driver
        )
        if chat_id is not None and text_id is not None:
            self._edit_safe(
                chat_id,
                int(text_id),
                f"{self.preview_text(record)}\n\n"
                + ("Edited clip is ready below." if edited else "Media preview is ready below."),
                self._approval_keyboard(draft_id),
            )
        if chat_id is not None and ask_id is not None:
            self._edit_safe(chat_id, int(ask_id), "Media ready.")
        if not self._send_media_preview(draft_id):
            self._media_failed(draft_id, "Media preview could not be sent.")

    def _approval_keyboard(self, draft_id: str) -> dict:
        """Draft buttons with the publishing options this deployment enables."""
        return telegram_mod.approval_keyboard(
            draft_id,
            platforms=self.settings.platforms_enabled,
        )

    def _preview_keyboard(
        self, factory, draft_id: str, *, collecting: bool = False
    ) -> dict:
        """Build preview buttons with the options the factory supports."""
        options = {
            "instagram": self.settings.instagram_publish_enabled,
            "platforms": self.settings.platforms_enabled,
        }
        if collecting:
            try:
                return factory(draft_id, collecting=True, **options)
            except TypeError:
                pass
        try:
            return factory(draft_id, **options)
        except TypeError:
            return factory(draft_id)

    def discard_draft(self, record: dict) -> None:
        """Drop one draft and delete the Telegram messages that belong to it."""
        draft_id = str(record.get("id") or "")
        chat_id = record.get("chat_id")
        if draft_id:
            self.state.drop_draft(draft_id)
        self._delete_safe(chat_id, record.get("ask_message_id"))
        self._delete_safe(chat_id, record.get("preview_message_id"))
        self._delete_safe(chat_id, record.get("preview_keyboard_message_id"))

    def _drop_preview(self, chat_id, record: dict) -> None:
        """Delete the preview message(s) of one draft and forget their ids."""
        if chat_id is not None:
            self._delete_safe(chat_id, record.get("preview_message_id"))
            self._delete_safe(chat_id, record.get("preview_keyboard_message_id"))
        draft_id = str(record.get("id") or "")
        if draft_id and self.state.get_draft(draft_id) is not None:
            self.state.update_draft(
                draft_id,
                {"preview_message_id": None, "preview_keyboard_message_id": None},
            )

    def _send_media_preview(
        self,
        draft_id: str,
        keyboard=telegram_mod.media_preview_keyboard,
        *,
        collecting: bool = False,
    ) -> bool:
        """Send the stored media as a preview message; returns success."""
        record = self.state.get_draft(draft_id)
        if record is None:
            return False
        media = record.get("media") or {}
        kind = str(media.get("kind") or "")
        chat_id = record.get("chat_id")
        if chat_id is None:
            return False
        markup = self._preview_keyboard(keyboard, draft_id, collecting=collecting)
        files = list(media.get("files") or [])
        use_album = collecting or len(files) >= 2
        if use_album:
            if kind != "image" or not files:
                return False
            entries: list[tuple[str, bytes]] = []
            for item in files:
                path = Path(str(item.get("local_path") or ""))
                if not path.is_file():
                    return False
                try:
                    entries.append((path.name, path.read_bytes()))
                except OSError as error:
                    log.warning(
                        "media preview read failed for %s: %s", draft_id, error
                    )
                    return False
            try:
                sent = self.api.send_media_group(
                    chat_id,
                    entries,
                    caption=f"Media preview for the draft above.\n{len(entries)} photos",
                )
            except telegram_mod.TelegramError as error:
                log.warning(
                    "media group preview send failed for %s: %s", draft_id, error
                )
                return False
            message_id = sent.get("message_id")
            if message_id is not None:
                self.state.update_draft(draft_id, {"preview_message_id": message_id})
            # Telegram albums cannot carry inline keyboards, so the
            # approval buttons go in a follow-up message below the photos.
            try:
                buttons = self.api.send_message(
                    chat_id,
                    f"{len(entries)} photos ready above.",
                    markup,
                )
            except telegram_mod.TelegramError as error:
                log.warning(
                    "media group buttons failed for %s: %s", draft_id, error
                )
                return False
            buttons_id = buttons.get("message_id")
            if buttons_id is not None:
                self.state.update_draft(
                    draft_id, {"preview_keyboard_message_id": buttons_id}
                )
            return True
        file_id = str(media.get("file_id") or "")
        if kind == "video" and file_id and not str(media.get("local_path") or ""):
            # Files above the bot download limit never land in local storage;
            # Telegram can still deliver them back from the stored id.
            try:
                sent = self.api.send_video_by_id(
                    chat_id,
                    file_id,
                    caption="Media preview for the draft above.",
                    reply_markup=markup,
                    as_document=bool(media.get("as_document")),
                )
            except telegram_mod.TelegramError as error:
                log.warning("media preview resend failed for %s: %s", draft_id, error)
                return False
            message_id = sent.get("message_id")
            if message_id is not None:
                self.state.update_draft(draft_id, {"preview_message_id": message_id})
            return True
        path = Path(str(media.get("local_path") or ""))
        if kind not in {"image", "video"} or not path.is_file():
            return False
        try:
            content = path.read_bytes()
        except OSError as error:
            log.warning("media preview read failed for %s: %s", draft_id, error)
            return False
        filename = path.name
        try:
            if kind == "image":
                sent = self.api.send_photo(
                    chat_id,
                    filename,
                    content,
                    caption=f"Media preview for the draft above.\n{filename}",
                    reply_markup=markup,
                )
            else:
                sent = self.api.send_video(
                    chat_id,
                    filename,
                    content,
                    caption=f"Media preview for the draft above.\n{filename}",
                    reply_markup=markup,
                )
        except telegram_mod.TelegramError as error:
            log.warning("media preview send failed for %s: %s", draft_id, error)
            return False
        message_id = sent.get("message_id")
        if message_id is not None:
            self.state.update_draft(draft_id, {"preview_message_id": message_id})
        return True

    def _video_edit_failed(
        self, draft_id: str, record: dict, media: dict, detail: str
    ) -> None:
        """Keep the operator clip when an edit job cannot finish.

        The original file is restored, the preview is re-sent, and the
        operator can still publish the clip unchanged.
        """
        source = media.get("edit_source") or {}
        restored = {
            "kind": "video",
            "driver": "user-upload",
            "status": "done",
            "artifact": str(source.get("artifact") or ""),
            "local_path": str(source.get("local_path") or ""),
            "duration": "",
            "edit_failed": str(detail)[:300],
        }
        file_id = str(source.get("file_id") or "")
        if file_id:
            restored["file_id"] = file_id
            restored["as_document"] = bool(source.get("as_document"))
        self.state.update_draft(
            draft_id,
            {
                "status": "media_ready",
                "media": restored,
                "video_edit_choice": "edit-failed",
            },
        )
        self._record_event(draft_id, "video_edit_failed", str(detail))
        chat_id = record.get("chat_id")
        ask_id = record.get("video_edit_ask_message_id")
        if chat_id is not None and ask_id is not None:
            self._edit_safe(
                chat_id,
                int(ask_id),
                f"Editing failed: {detail}\nThe clip stays as it is; press "
                "Approve on the preview to publish it unchanged.",
            )
        self._finish_user_media(draft_id, {**record, "media": restored}, restored)

    def _media_failed(self, draft_id: str, detail: str) -> None:
        record = self.state.get_draft(draft_id)
        if record is None:
            return
        media = dict(record.get("media") or {})
        source = media.get("edit_source")
        if isinstance(source, dict) and source:
            self._video_edit_failed(draft_id, record, media, str(detail))
            return
        media.update({"status": "failed", "error": str(detail)[:300]})
        self.state.update_draft(draft_id, {"status": "media_failed", "media": media})
        self._record_event(draft_id, "media_failed", str(detail))
        chat_id = record.get("chat_id")
        ask_id = record.get("ask_message_id")
        if chat_id is not None and ask_id is not None:
            self._edit_safe(
                chat_id,
                int(ask_id),
                f"Media generation failed: {detail}",
                telegram_mod.media_retry_keyboard(draft_id),
            )
        elif chat_id is not None:
            # Scheduled drafts have no media-ask message to edit; without a
            # note the operator would never learn the image did not arrive.
            try:
                self.api.send_message(
                    chat_id,
                    f"Media generation failed for this scheduled draft: {detail}",
                )
            except telegram_mod.TelegramError:
                pass

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _edit_safe(self, chat_id, message_id: int, text: str, keyboard=None) -> None:
        try:
            self.api.edit_message_text(
                chat_id,
                int(message_id),
                text,
                keyboard,
                parse_mode="HTML",
            )
        except telegram_mod.TelegramError:
            try:
                self.api.edit_message_text(chat_id, int(message_id), text, keyboard)
            except telegram_mod.TelegramError:
                try:
                    self.api.send_message(chat_id, text, keyboard)
                except telegram_mod.TelegramError:
                    pass

    def _delete_safe(self, chat_id, message_id) -> None:
        if message_id is None or chat_id is None:
            return
        try:
            self.api.delete_message(chat_id, int(message_id))
        except telegram_mod.TelegramError:
            pass

    def _save_feedback(self, draft: dict, text: str) -> None:
        feedback = list(draft.get("feedback") or [])
        feedback.append(text)
        self.state.update_draft(str(draft["id"]), {"feedback": feedback})
        self.state.add_lesson(text)
        self._record_event(str(draft["id"]), "feedback_saved", text)

    def preview_text(self, record: dict) -> str:
        kind = str(record.get("kind") or "")
        if kind == "daily":
            label = "Daily proposal"
        elif kind == "routine":
            label = "Scheduled proposal"
        else:
            label = "Draft proposal"
        channel = self.settings.telegram_channel or "(no channel configured)"
        body = str(record.get("body") or "")
        source_url = str(record.get("source_url") or "")
        source_line = ""
        if source_url and source_url not in body:
            source_line = f"\nSource: {_html_escape(source_url)}"
        return (
            f"{label}\n"
            f"{_html_title(str(record.get('title') or ''))}\n\n"
            f"{_rtl_body_html(body)}\n"
            f"{source_line}\n"
            f"Publish to {_html_escape(channel)}; reply with edit notes and "
            f"press Reject to revise; press Reject alone to discard."
        )

    @staticmethod
    def channel_text(record: dict) -> str:
        title = _html_title(str(record.get("title") or ""))
        return f"{title}\n\n{_rtl_body_html(str(record.get('body') or ''))}"

    # ------------------------------------------------------------ callbacks

    def handle_callback(self, callback: dict) -> None:
        query_id = str(callback.get("id") or "")
        sender = (callback.get("from") or {}).get("id")
        if not self._is_allowed(sender):
            self.api.answer_callback_query(query_id, "Not allowed.")
            return
        data = str(callback.get("data") or "")
        if data.startswith("media:"):
            tokens = data.split(":", 2)
            if len(tokens) == 3:
                self._media_callback(query_id, tokens[1], tokens[2])
            else:
                self.api.answer_callback_query(query_id, "Unknown media action.")
            return
        if data.startswith("platforms:"):
            draft_id = data.split(":", 1)[1]
            record = self.state.get_draft(draft_id)
            if record is None:
                self.api.answer_callback_query(query_id, "This draft is no longer active.")
                return
            self._offer_platforms(query_id, record)
            return
        if data.startswith("package:"):
            tokens = data.split(":", 2)
            if len(tokens) == 3 and tokens[2]:
                self._send_platform_package(query_id, tokens[1], tokens[2])
            else:
                self.api.answer_callback_query(query_id, "Unknown platform action.")
            return
        action, separator, draft_id = data.partition(":")
        if not separator or not draft_id:
            self.api.answer_callback_query(query_id, "Unknown action.")
            return
        record = self.state.get_draft(draft_id)
        if record is None:
            self.api.answer_callback_query(query_id, "This draft is no longer active.")
            return
        chat_id = record.get("chat_id")
        message_id = record.get("message_id")
        if action == "approve":
            self._approve(query_id, record, chat_id, message_id, targets=("telegram",))
            return
        if action in {"approve_ig", "approve_both"}:
            targets = (
                ("instagram",)
                if action == "approve_ig"
                else ("telegram", "instagram")
            )
            self._approve(query_id, record, chat_id, message_id, targets=targets)
            return
        if action == "cancel":
            self.state.update_draft(
                draft_id,
                {"discard_pending": False},
            )
            if chat_id is not None and message_id is not None:
                self._edit_safe(
                    chat_id,
                    int(message_id),
                    self.preview_text(record),
                    self._approval_keyboard(draft_id),
                )
            self.api.answer_callback_query(query_id, "Draft kept.")
            return
        if action == "reject":
            if record.get("feedback"):
                self._revise_draft(query_id, record, chat_id, message_id)
                return
            if not record.get("discard_pending"):
                self.state.update_draft(draft_id, {"discard_pending": True})
                if chat_id is not None and message_id is not None:
                    self._edit_safe(
                        chat_id,
                        int(message_id),
                        "Really discard this draft? Press Reject again to "
                        "delete it, or Cancel to keep it.",
                        telegram_mod.discard_confirm_keyboard(draft_id),
                    )
                self.api.answer_callback_query(
                    query_id,
                    "Press Reject again to confirm discarding.",
                )
                return
            self.discard_draft(record)
            if chat_id is not None and message_id is not None:
                try:
                    self.api.edit_message_text(chat_id, int(message_id), "Rejected.")
                except telegram_mod.TelegramError:
                    pass
            self.api.answer_callback_query(query_id, "Draft rejected.")
            return
        self.api.answer_callback_query(query_id, "Unknown action.")

    def _revise_draft(self, query_id: str, record: dict, chat_id, message_id) -> None:
        if self.writer is None:
            self.api.answer_callback_query(
                query_id,
                "No writer endpoint is configured; cannot revise.",
            )
            return
        draft_id = str(record.get("id") or "")
        feedback = "\n".join(f"- {line}" for line in (record.get("feedback") or []))
        try:
            post = self.writer.revise_post(
                title=str(record.get("title") or ""),
                body=str(record.get("body") or ""),
                feedback=feedback,
                source_url=str(record.get("source_url") or ""),
            )
        except writer_mod.WriterError as error:
            log.warning("revision failed for draft %s: %s", draft_id, error)
            self._safe_answer(query_id, f"Revision failed: {error}")
            return
        updated = {
            "title": post["title"],
            "body": post["body"],
            "source_url": post["source_url"] or str(record.get("source_url") or ""),
            "updated_at": self.now_fn().isoformat(),
            "feedback": [],
            "discard_pending": False,
        }
        self.state.update_draft(draft_id, updated)
        preview = self.preview_text({**record, **updated})
        keyboard = self._approval_keyboard(draft_id)
        try:
            if chat_id is None or message_id is None:
                raise telegram_mod.TelegramError("no anchor message to edit")
            self.api.edit_message_text(
                chat_id,
                int(message_id),
                preview,
                keyboard,
                parse_mode="HTML",
            )
        except telegram_mod.TelegramError:
            sent = self.api.send_message(chat_id, preview, keyboard, parse_mode="HTML")
            self.state.update_draft(draft_id, {"message_id": sent.get("message_id")})
        self._safe_answer(
            query_id,
            "Revised. Approve, add more feedback, or reject to discard.",
        )

    def _safe_answer(self, query_id: str, text: str) -> None:
        """Answer a callback query, ignoring failures on stale query ids."""
        if not query_id:
            return
        try:
            self.api.answer_callback_query(query_id, text)
        except telegram_mod.TelegramError as error:
            log.debug("callback answer skipped: %s", error)

    def _offer_platforms(self, query_id: str, record: dict) -> None:
        """Send the manual-upload platform chooser for one draft."""
        chat_id = record.get("chat_id")
        if chat_id is None:
            self.api.answer_callback_query(query_id, "No chat is attached to this draft.")
            return
        if not self.settings.platforms_enabled:
            self.api.answer_callback_query(
                query_id,
                "Platform upload packages are turned off "
                "(CONTENT_PLATFORMS_ENABLED).",
            )
            return
        policy = workflow.load_policy(self.settings.policy_dir)
        profiles = platforms_mod.load_profiles(policy)
        if not profiles:
            self.api.answer_callback_query(
                query_id,
                "No manual platform is configured; add a platforms section to "
                "editorial-policy.yaml.",
            )
            return
        pairs = [(key, profile.label) for key, profile in profiles.items()]
        self.api.send_message(
            chat_id,
            "Pick a platform for a copy-ready upload package:",
            telegram_mod.platforms_keyboard(str(record.get("id") or ""), pairs),
        )
        self._safe_answer(query_id, "Choose a platform.")

    def _send_platform_package(self, query_id: str, key: str, draft_id: str) -> None:
        """Send one platform package plus the stored media for a draft."""
        if not self.settings.platforms_enabled:
            self.api.answer_callback_query(
                query_id,
                "Platform upload packages are turned off "
                "(CONTENT_PLATFORMS_ENABLED).",
            )
            return
        record = self.state.get_draft(draft_id)
        if record is None:
            self.api.answer_callback_query(query_id, "This draft is no longer active.")
            return
        policy = workflow.load_policy(self.settings.policy_dir)
        profiles = platforms_mod.load_profiles(policy)
        profile = profiles.get(str(key or "").strip().lower())
        if profile is None:
            self.api.answer_callback_query(query_id, "Unknown platform.")
            return
        chat_id = record.get("chat_id")
        if chat_id is None:
            self.api.answer_callback_query(query_id, "No chat is attached to this draft.")
            return
        try:
            self.api.send_message(
                chat_id,
                platforms_mod.package_text(record, profile),
                parse_mode="HTML",
            )
        except telegram_mod.TelegramError as error:
            self.api.answer_callback_query(query_id, f"Package failed: {error}")
            return
        self._send_package_media(chat_id, record, profile)
        self._safe_answer(query_id, f"{profile.label} package sent.")

    def _send_package_media(self, chat_id, record: dict, profile) -> None:
        """Re-send the stored media so it can be uploaded without scrolling."""
        entries = platforms_mod.stored_media(record)
        if not entries:
            media = record.get("media") or {}
            if media.get("oversized"):
                try:
                    self.api.send_message(
                        chat_id,
                        f"The {profile.label} package needs the file itself, but "
                        "this video is over the 20 MB Telegram bot download limit "
                        "and only lives on Telegram. Send a copy under 20 MB to "
                        "get the upload file for this package.",
                    )
                except telegram_mod.TelegramError as error:
                    log.warning("package size note failed: %s", error)
            return
        caption = f"{profile.label} upload file"
        if len(entries) > 1:
            files: list[tuple[str, bytes]] = []
            for _kind, path, name in entries:
                file_path = Path(path)
                if not file_path.is_file():
                    continue
                try:
                    files.append((name, file_path.read_bytes()))
                except OSError as error:
                    log.warning("package media unreadable (%s): %s", path, error)
            if files:
                try:
                    self.api.send_media_group(chat_id, files[:10], caption=caption)
                except telegram_mod.TelegramError as error:
                    log.warning("package media send failed: %s", error)
            return
        kind, path, name = entries[0]
        file_path = Path(path)
        if not file_path.is_file():
            log.warning("package media missing from storage: %s", path)
            return
        try:
            payload = file_path.read_bytes()
        except OSError as error:
            log.warning("package media unreadable (%s): %s", path, error)
            return
        try:
            if kind == "video":
                self.api.send_video(chat_id, name, payload, caption=caption)
            else:
                self.api.send_photo(chat_id, name, payload, caption=caption)
        except telegram_mod.TelegramError as error:
            log.warning("package media send failed: %s", error)

    def _approve(
        self,
        query_id: str,
        record: dict,
        chat_id,
        message_id,
        *,
        targets: tuple[str, ...] = ("telegram",),
    ) -> bool:
        """Publish one approved draft; False means the draft was refused."""
        channel = self.settings.telegram_channel
        if "telegram" in targets and not channel:
            self._safe_answer(
                query_id,
                "No publish channel is configured (CONTENT_TELEGRAM_CHANNEL).",
            )
            return False
        if "instagram" in targets and not self.settings.instagram_publish_enabled:
            if not self.settings.instagram_enabled:
                reason = (
                    "Instagram is not configured; set INSTAGRAM_BUSINESS_ID and "
                    "INSTAGRAM_ACCESS_TOKEN."
                )
            else:
                reason = (
                    "Instagram automatic publishing is off; set "
                    "INSTAGRAM_AUTO_PUBLISH=true to enable it."
                )
            self._safe_answer(query_id, reason)
            return False
        policy = workflow.load_policy(self.settings.policy_dir)
        zone = _policy_zone(policy)
        day = self.now_fn().astimezone(zone).date().isoformat()
        self.state.reset_day(day)
        on_demand = str(record.get("kind") or "") == "on_demand"
        settings = workflow.on_demand_settings(policy) if on_demand else {}
        subject_to_limit = not on_demand or not bool(
            settings.get("unlimited_approvals", True)
        )
        if subject_to_limit:
            limit = int((policy.get("pipeline") or {}).get("max_approved_per_day", 3))
            if int(self.state.load().get("published_today", 0)) >= limit:
                self._safe_answer(query_id, f"Daily publish limit reached ({limit}).")
                return False
        if record.get("status") == "media_running":
            self._safe_answer(
                query_id,
                "Media is still being generated; wait or choose Text only first.",
            )
            return False
        if record.get("status") == "awaiting_media":
            self._safe_answer(
                query_id,
                "Waiting for your media file; send it here or choose Text only first.",
            )
            return False
        if (
            "instagram" in targets
            and record.get("status") == "media_ready"
            and not (record.get("media") or {}).get("files")
            and str((record.get("media") or {}).get("kind") or "") not in {"image", "video"}
        ):
            self._safe_answer(
                query_id,
                "Instagram publishing needs photos or a video attached to the draft.",
            )
            return False
        if "instagram" in targets and (record.get("media") or {}).get("oversized"):
            note = (
                "Instagram needs the video file itself, but this clip is over "
                "the 20 MB Telegram bot download limit. Send a copy under "
                "20 MB and attach it again."
            )
            self._safe_answer(query_id, note)
            return False
        # Acknowledge before the slow publish; the result goes into the
        # draft message so a stale callback id cannot fail the update.
        self._safe_answer(query_id, "Publishing...")
        ig_error = None
        if "instagram" in targets:
            try:
                self._publish_to_instagram(record)
            except instagram_mod.InstagramError as error:
                ig_error = str(error)
        tg_error = None
        if "telegram" in targets:
            try:
                self._publish_record(record)
            except telegram_mod.TelegramError as error:
                tg_error = str(error)
        if ig_error or tg_error:
            errors = []
            if ig_error:
                errors.append(f"Instagram: {ig_error}")
            if tg_error:
                errors.append(f"Telegram: {tg_error}")
            self._safe_answer(query_id, "Publish had errors: " + "; ".join(errors))
            return False
        self.state.remember_published(
            str(record.get("content_hash") or ""),
            str(record.get("category") or ""),
            day,
            count_toward_limit=subject_to_limit,
        )
        draft_id = str(record.get("id") or "")
        self.state.drop_draft(draft_id)
        self._delete_safe(chat_id, record.get("ask_message_id"))
        self._delete_safe(chat_id, record.get("preview_message_id"))
        self._delete_safe(chat_id, record.get("preview_keyboard_message_id"))
        destinations = []
        if "telegram" in targets:
            destinations.append(channel or "Telegram")
        if "instagram" in targets:
            destinations.append("Instagram")
        summary = f"Published to {' + '.join(destinations)}."
        if chat_id is not None and message_id is not None:
            try:
                self.api.edit_message_text(
                    chat_id,
                    int(message_id),
                    summary,
                )
            except telegram_mod.TelegramError:
                pass
        return True

    def _publish_to_instagram(self, record: dict) -> dict:
        """Publish one approved draft to Instagram through the Graph API."""
        publisher = self._instagram_publisher()
        if publisher is None:
            raise instagram_mod.InstagramError(
                "Instagram is not configured (INSTAGRAM_BUSINESS_ID / "
                "INSTAGRAM_ACCESS_TOKEN)."
            )
        media = record.get("media") or {}
        if media.get("oversized"):
            raise instagram_mod.InstagramError(
                "This video is over the 20 MB Telegram bot download limit and "
                "only lives on Telegram; Instagram needs the file itself, so "
                "send a copy under 20 MB and attach it again."
            )
        items: list[dict] = []
        files = list(media.get("files") or [])
        if files:
            items = [
                {"kind": "image", "path": str(item.get("local_path") or "")}
                for item in files
            ]
        elif str(media.get("kind") or "") in {"image", "video"}:
            items = [
                {
                    "kind": str(media.get("kind") or ""),
                    "path": str(media.get("local_path") or ""),
                }
            ]
        if not items:
            raise instagram_mod.InstagramError(
                "Instagram publishing needs media attached to the draft; "
                "text-only posts cannot be published to Instagram."
            )
        caption = instagram_mod.build_caption(
            str(record.get("title") or ""),
            str(record.get("body") or ""),
            str(record.get("source_url") or ""),
        )
        return publisher.publish(caption, items)

    def _publish_record(self, record: dict) -> None:
        """Publish one approved draft to the Telegram channel."""
        channel = self.settings.telegram_channel
        media = record.get("media") or {}
        kind = str(media.get("kind") or "")
        local_path = str(media.get("local_path") or "")
        files = list(media.get("files") or [])
        if kind == "image" and len(files) >= 2:
            messages = _media_caption_messages(record)
            caption = messages[0]
            entries: list[tuple[str, bytes]] = []
            for item in files:
                path = Path(str(item.get("local_path") or ""))
                if not path.is_file():
                    raise telegram_mod.TelegramError("media file is missing from storage")
                try:
                    entries.append((path.name, path.read_bytes()))
                except OSError as error:
                    raise telegram_mod.TelegramError(
                        f"media file could not be read: {error}"
                    ) from error
            self.api.send_media_group(
                channel,
                entries,
                caption=caption,
                parse_mode="HTML",
            )
            for continuation in messages[1:]:
                self.api.send_message(channel, continuation, parse_mode="HTML")
            return
        file_id = str(media.get("file_id") or "")
        if kind == "video" and file_id and not local_path:
            messages = _media_caption_messages(record)
            self.api.send_video_by_id(
                channel,
                file_id,
                caption=messages[0],
                parse_mode="HTML",
                as_document=bool(media.get("as_document")),
            )
            for continuation in messages[1:]:
                self.api.send_message(channel, continuation, parse_mode="HTML")
            return
        if kind not in {"image", "video"} or not local_path:
            self.api.send_message(channel, self.channel_text(record), parse_mode="HTML")
            return
        path = Path(local_path)
        if not path.is_file():
            raise telegram_mod.TelegramError("media file is missing from storage")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise telegram_mod.TelegramError(f"media file could not be read: {error}") from error
        messages = _media_caption_messages(record)
        caption = messages[0]
        if kind == "image":
            self.api.send_photo(
                channel,
                path.name,
                content,
                caption=caption,
                parse_mode="HTML",
            )
        else:
            self.api.send_video(
                channel,
                path.name,
                content,
                caption=caption,
                parse_mode="HTML",
            )
        for continuation in messages[1:]:
            self.api.send_message(channel, continuation, parse_mode="HTML")

    # ---------------------------------------------------------------- daily

    @staticmethod
    def _minutes_of_day(raw, default: int) -> int:
        """Parse an "HH:MM" policy value into minutes, falling back on default."""
        try:
            hours_text, minutes_text = str(raw).split(":", 1)
            hours, minutes = int(hours_text), int(minutes_text)
        except (TypeError, ValueError):
            return default
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            return hours * 60 + minutes
        return default

    def _daily_schedule(self) -> tuple[ZoneInfo, int]:
        policy = workflow.load_policy(self.settings.policy_dir)
        pipeline = policy.get("pipeline") or {}
        zone = _policy_zone(policy)
        proposal_minutes = self._minutes_of_day(
            pipeline.get("daily_proposal_time"), 8 * 60
        )
        return zone, proposal_minutes

    def maybe_run_daily(self) -> None:
        if not self.settings.scheduler_enabled:
            return
        zone, proposal_minutes = self._daily_schedule()
        now_local = self.now_fn().astimezone(zone)
        day = now_local.date().isoformat()
        if str(self.state.load().get("daily_last_run") or "") == day:
            return
        if now_local.hour * 60 + now_local.minute < proposal_minutes:
            return
        try:
            self.run_daily(day)
        except Exception:
            log.exception("daily run failed")
            raise

    def _owner_chat_id(self):
        return min(self.settings.telegram_users) if self.settings.telegram_users else None

    def _mark_daily_run(self, day: str) -> None:
        data = self.state.load()
        data["daily_last_run"] = day
        self.state.save()

    def _research(self, policy: dict) -> tuple[dict, list, list, int]:
        """Research step: fetch feeds, then normalize, filter, rank, and pick."""
        raw_items = []
        failed = 0
        for source in workflow.load_sources(self.settings.policy_dir):
            try:
                body = self.fetch_feed(source["url"])
                raw_items.extend(
                    extract.parse_rss(source["name"], source["url"], body)
                )
            except (fetch.FetchError, ValueError) as error:
                failed += 1
                log.warning("feed %s failed: %s", source["url"], error)
        prepared = workflow.prepare_daily(raw_items, policy, now=self.now_fn())
        pipeline = policy.get("pipeline") or {}
        max_streak = int(pipeline.get("max_consecutive_same_category", 3))
        queue, skipped = workflow.select_with_category_mix(
            prepared["candidates"],
            self.state.load().get("last_categories") or [],
            max_streak,
        )
        return prepared, queue, skipped, failed

    def run_daily(self, day: str) -> None:
        owner = self._owner_chat_id()
        policy = workflow.load_policy(self.settings.policy_dir)
        if not workflow.load_sources(self.settings.policy_dir):
            self._mark_daily_run(day)
            log.info("daily run: no discovery sources configured; skipped")
            if owner is not None:
                self.api.send_message(
                    owner,
                    "Daily content run: no discovery sources configured. "
                    "Add RSS/Atom feeds to data/content-manager/config/sources.yaml.",
                )
            return
        prepared, queue, skipped, failed = self._research(policy)
        self._mark_daily_run(day)
        if owner is None:
            log.info("daily run: no operator user configured; proposals not sent")
            return
        if not queue:
            message = "Daily content run: no candidates passed the editorial filters."
            if prepared["rejected"] or prepared["dropped"]:
                message += (
                    f" ({len(prepared['rejected'])} rejected, "
                    f"{len(prepared['dropped'])} duplicates)"
                )
            self.api.send_message(owner, message)
            return
        for item in queue:
            try:
                self.send_draft(item, owner, kind="daily")
            except telegram_mod.TelegramError as error:
                log.warning("could not send daily proposal: %s", error)
        if skipped:
            log.info("daily run: %s candidates skipped by the category mix rule", len(skipped))
        if failed:
            log.warning("daily run: %s source(s) failed", failed)

    # ------------------------------------------------------------- routines

    def _routine_due(
        self, routine: dict, now_local, default_minutes: int
    ) -> tuple[bool, str]:
        """Return whether one scheduled routine is due and its period key.

        A daily routine runs once per local day; a weekly routine runs once
        per ISO week on or after its configured weekday. The period key is
        what the state store keeps, so a restart never reruns a period.
        """
        cadence = str(routine.get("cadence") or "daily").strip().lower()
        if cadence not in {"daily", "weekly"}:
            cadence = "daily"
        minutes = self._minutes_of_day(routine.get("time"), default_minutes)
        run_at = clock_time(hour=minutes // 60, minute=minutes % 60)
        if cadence == "daily":
            day = now_local.date()
            period = day.isoformat()
        else:
            name = str(routine.get("weekday") or "monday").strip().lower()
            index = _WEEKDAY_INDEX.get(name[:3], 0)
            day = now_local.date() - timedelta(days=now_local.weekday())
            day = day + timedelta(days=index)
            iso = now_local.isocalendar()
            period = f"{iso.year}-W{iso.week:02d}"
        scheduled = datetime.combine(day, run_at).replace(tzinfo=now_local.tzinfo)
        if now_local < scheduled:
            return False, period
        routine_id = str(routine.get("id") or "")
        runs = self.state.load().get("routine_last_run") or {}
        return str(runs.get(routine_id) or "") != period, period

    def _mark_routine_run(self, routine_id: str, period: str) -> None:
        data = self.state.load()
        runs = dict(data.get("routine_last_run") or {})
        runs[routine_id] = period
        data["routine_last_run"] = runs
        self.state.save()

    def maybe_run_routines(self) -> None:
        """Start every enabled routine whose cadence period came due."""
        if not self.settings.scheduler_enabled:
            return
        try:
            policy = workflow.load_policy(self.settings.policy_dir)
        except Exception:  # noqa: BLE001
            log.exception("routine policy could not be loaded")
            return
        routines = [
            item
            for item in (policy.get("routines") or [])
            if isinstance(item, dict)
        ]
        if not routines:
            return
        pipeline = policy.get("pipeline") or {}
        default_minutes = self._minutes_of_day(
            pipeline.get("daily_proposal_time"), 8 * 60
        )
        zone = _policy_zone(policy)
        now_local = self.now_fn().astimezone(zone)
        for routine in routines:
            routine_id = str(routine.get("id") or "").strip()
            if not routine_id or routine.get("enabled") is False:
                continue
            due, period = self._routine_due(routine, now_local, default_minutes)
            if not due:
                continue
            try:
                self.run_routine(routine, period)
            except Exception:  # noqa: BLE001
                log.exception("routine %s failed", routine_id)

    def run_routine(self, routine: dict, period: str) -> None:
        """One scheduled pass: research, draft, media, then queue for approval."""
        routine_id = str(routine.get("id") or "routine")
        self._mark_routine_run(routine_id, period)
        owner = self._owner_chat_id()
        if owner is None:
            log.info("routine %s: no operator user configured; nothing queued", routine_id)
            return
        policy = workflow.load_policy(self.settings.policy_dir)
        if not workflow.load_sources(self.settings.policy_dir):
            self.api.send_message(
                owner,
                f"Scheduled routine {routine_id}: no discovery sources configured. "
                "Add RSS/Atom feeds to data/content-manager/config/sources.yaml.",
            )
            return
        prepared, queue, skipped, failed = self._research(policy)
        if not queue:
            message = (
                f"Scheduled routine {routine_id}: "
                "no candidates passed the editorial filters."
            )
            if prepared["rejected"] or prepared["dropped"]:
                message += (
                    f" ({len(prepared['rejected'])} rejected, "
                    f"{len(prepared['dropped'])} duplicates)"
                )
            self.api.send_message(owner, message)
            return
        limit = max(1, int(routine.get("count") or 1))
        media_mode = str(routine.get("media") or "auto").strip().lower()
        platform = str(routine.get("platform") or "")
        queued = 0
        for item in queue[:limit]:
            try:
                draft_id = self.send_draft(item, owner, kind="routine")
            except telegram_mod.TelegramError as error:
                log.warning("routine %s: proposal could not be sent: %s", routine_id, error)
                continue
            if draft_id is None:
                continue
            queued += 1
            if media_mode == "auto" and self.media is not None:
                self._start_media_job(
                    draft_id, self.settings.image_driver, "image", ""
                )
        log.info(
            "routine %s (%s): %s queued, %s skipped by the category mix rule, "
            "%s source(s) failed",
            routine_id,
            platform or "no platform",
            queued,
            len(skipped),
            failed,
        )
