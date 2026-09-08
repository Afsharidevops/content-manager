"""Telegram Content Bot: on-demand link posts and daily editorial proposals."""

from __future__ import annotations

import html
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from content_bot import extract, fetch, state as state_mod, telegram as telegram_mod
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


def _html_escape(value) -> str:
    return html.escape(str(value or ""), quote=False)


def _html_title(title: str) -> str:
    """Render one post title as bold, right-to-left HTML."""
    return (
        f"<b>{_TITLE_RTL_OPEN}{_html_escape(title)}"
        f"{_TITLE_RTL_CLOSE}</b>"
    )


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
        self.state = store or state_mod.StateStore(Path(settings.data_dir) / "state.json")
        self.fetch_page = fetch_page or fetch.fetch_page
        self.fetch_feed = fetch_feed or fetch.fetch_feed
        self.now_fn = now_fn
        self._offset = 0

    # ------------------------------------------------------------------ run

    def run(self) -> None:
        self._startup()
        while True:
            try:
                self.poll_once()
                self.maybe_run_daily()
            except telegram_mod.TelegramError as error:
                log.warning("Telegram API error: %s", error)
            except Exception:
                log.exception("unhandled error in the main loop")
            time.sleep(1)

    def _startup(self) -> None:
        try:
            me = self.api.get_me()
            log.info("Content Bot started as @%s", me.get("username", "?"))
        except telegram_mod.TelegramError as error:
            log.error("Bot token rejected: %s", error)
            raise
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

    # ---------------------------------------------------------------- polls

    def poll_once(self) -> int:
        updates = self.api.get_updates(offset=self._offset, timeout=25)
        for update in updates:
            update_id = int(update.get("update_id", 0))
            self._offset = max(self._offset, update_id + 1)
            try:
                self.handle_update(update)
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
        match = URL_RE.search(text)
        if not match:
            self.api.send_message(
                chat_id,
                "Send a link and I will draft a post for your approval.",
            )
            return
        self.request_on_demand(match.group(0), chat_id)

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
        return (
            "Content Bot commands:\n"
            "/start or /help - this message\n"
            "/status - configuration and counters\n"
            "/forget_link <url> - allow a published link to be drafted again\n"
            "Send any http(s) link - draft a post with Approve/Reject buttons\n"
            "Reply to a proposal with edit notes, then press Reject to revise;\n"
            "press Reject without notes to discard. Approved drafts are\n"
            "published to the configured Telegram channel."
        )

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
            f"Pending drafts: {drafts}\n"
            f"Published today: {today}\n"
            f"Total published: {published}"
        )

    def request_on_demand(self, url: str, chat_id) -> None:
        try:
            html_text = self.fetch_page(url)
        except fetch.FetchError as error:
            self.api.send_message(chat_id, f"Could not fetch the link: {error}")
            return
        article = extract.extract_article(html_text, url)
        if len(article["text"]) < MIN_ARTICLE_CHARS:
            self.api.send_message(
                chat_id,
                "The page did not contain enough readable text to work with.",
            )
            return
        raw_item = {
            "title": article["title"] or url,
            "url": url,
            "published_at": article["published_at"],
            "summary": article["description"],
            "text": article["text"],
            "source": None,
            "category": "",
            "tags": [],
        }
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

    def send_draft(self, item: dict, chat_id, *, kind: str) -> None:
        if self.writer is None:
            self.api.send_message(
                chat_id,
                "No writer endpoint is configured; add CONTENT_WRITER_BASE_URL and restart.",
            )
            return
        try:
            post = self.writer.generate_post(item)
        except writer_mod.WriterError as error:
            self.api.send_message(chat_id, f"Copy generation failed: {error}")
            return
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
        }
        sent = self.api.send_message(
            chat_id,
            self.preview_text(record),
            telegram_mod.approval_keyboard(draft_id),
            parse_mode="HTML",
        )
        record["message_id"] = sent.get("message_id")
        self.state.add_draft(draft_id, record)

    def _save_feedback(self, draft: dict, text: str) -> None:
        feedback = list(draft.get("feedback") or [])
        feedback.append(text)
        self.state.update_draft(str(draft["id"]), {"feedback": feedback})

    def preview_text(self, record: dict) -> str:
        label = "Daily proposal" if record.get("kind") == "daily" else "Draft proposal"
        channel = self.settings.telegram_channel or "(no channel configured)"
        return (
            f"{label}\n"
            f"{_html_title(str(record.get('title') or ''))}\n\n"
            f"{_html_escape(str(record.get('body') or ''))}\n\n"
            f"Source: {_html_escape(str(record.get('source_url') or ''))}\n"
            f"Publish to {_html_escape(channel)}; reply with edit notes and "
            f"press Reject to revise; press Reject alone to discard."
        )

    @staticmethod
    def channel_text(record: dict) -> str:
        title = _html_title(str(record.get("title") or ""))
        return f"{title}\n\n{_html_escape(str(record.get('body') or ''))}"

    # ------------------------------------------------------------ callbacks

    def handle_callback(self, callback: dict) -> None:
        query_id = str(callback.get("id") or "")
        sender = (callback.get("from") or {}).get("id")
        if not self._is_allowed(sender):
            self.api.answer_callback_query(query_id, "Not allowed.")
            return
        data = str(callback.get("data") or "")
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
            self._approve(query_id, record, chat_id, message_id)
            return
        if action == "reject":
            if record.get("feedback"):
                self._revise_draft(query_id, record, chat_id, message_id)
                return
            self.state.drop_draft(draft_id)
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
        }
        self.state.update_draft(draft_id, updated)
        preview = self.preview_text({**record, **updated})
        keyboard = telegram_mod.approval_keyboard(draft_id)
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
        try:
            self.api.answer_callback_query(query_id, text)
        except telegram_mod.TelegramError as error:
            log.debug("callback answer skipped: %s", error)

    def _approve(self, query_id: str, record: dict, chat_id, message_id) -> None:
        channel = self.settings.telegram_channel
        if not channel:
            self.api.answer_callback_query(
                query_id,
                "No publish channel is configured (CONTENT_TELEGRAM_CHANNEL).",
            )
            return
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
                self.api.answer_callback_query(
                    query_id,
                    f"Daily publish limit reached ({limit}).",
                )
                return
        try:
            self.api.send_message(channel, self.channel_text(record), parse_mode="HTML")
        except telegram_mod.TelegramError as error:
            self.api.answer_callback_query(query_id, f"Publish failed: {error}")
            return
        self.state.remember_published(
            str(record.get("content_hash") or ""),
            str(record.get("category") or ""),
            day,
            count_toward_limit=subject_to_limit,
        )
        draft_id = str(record.get("id") or "")
        self.state.drop_draft(draft_id)
        if chat_id is not None and message_id is not None:
            try:
                self.api.edit_message_text(
                    chat_id,
                    int(message_id),
                    f"Published to {channel}.",
                )
            except telegram_mod.TelegramError:
                pass
        self.api.answer_callback_query(query_id, "Published to channel.")

    # ---------------------------------------------------------------- daily

    def _daily_schedule(self) -> tuple[ZoneInfo, int]:
        policy = workflow.load_policy(self.settings.policy_dir)
        pipeline = policy.get("pipeline") or {}
        zone = _policy_zone(policy)
        raw_time = str(pipeline.get("daily_proposal_time") or "08:00")
        proposal_minutes = 8 * 60
        try:
            hours_text, minutes_text = raw_time.split(":", 1)
            hours, minutes = int(hours_text), int(minutes_text)
            if 0 <= hours <= 23 and 0 <= minutes <= 59:
                proposal_minutes = hours * 60 + minutes
        except ValueError:
            pass
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

    def run_daily(self, day: str) -> None:
        owner = self._owner_chat_id()
        policy = workflow.load_policy(self.settings.policy_dir)
        sources = workflow.load_sources(self.settings.policy_dir)
        if not sources:
            self._mark_daily_run(day)
            log.info("daily run: no discovery sources configured; skipped")
            if owner is not None:
                self.api.send_message(
                    owner,
                    "Daily content run: no discovery sources configured. "
                    "Add RSS/Atom feeds to data/content-manager/config/sources.yaml.",
                )
            return
        raw_items = []
        failed = 0
        for source in sources:
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
