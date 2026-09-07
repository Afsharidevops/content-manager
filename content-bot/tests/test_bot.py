"""Handler-level tests for the Content Bot with injected fakes."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from content_bot.bot import ContentBot
from content_bot.config import BotSettings
from content_bot.telegram import TelegramApi

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "content" / "config"

HTML_PAGE = """<html><head>
  <title>Container image layers explained</title>
  <meta property="og:title" content="Container image layers explained">
</head><body><article>
  <p>Container images are built from ordered layers that reuse cached output.</p>
  <p>Each layer is content-addressed and shared between unrelated images on the host.</p>
  <p>Understanding layers helps engineers keep images small and builds repeatable.</p>
</article></body></html>"""


class FakeWriter:
    def __init__(self):
        self.calls = []

    def generate_post(self, item):
        self.calls.append(item)
        return {
            "title": "Generated title",
            "body": "Generated body text.",
            "source_url": "https://example.com/layers",
        }


class FakeApi(TelegramApi):
    def __init__(self):
        super().__init__("123:TESTTOKENABCDEFGHIJKLMN")
        self.calls = []
        self.sent_messages = []

    def _transport(self, url, payload):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, payload))
        if method == "getMe":
            return {"ok": True, "result": {"username": "content_test_bot"}}
        if method == "sendMessage":
            self.sent_messages.append(payload)
            return {"ok": True, "result": {"message_id": 100 + len(self.sent_messages)}}
        return {"ok": True, "result": True}


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = BotSettings(
            bot_token="123:TESTTOKENABCDEFGHIJKLMN",
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=str(POLICY_DIR),
            data_dir=self.tmp.name,
            scheduler_enabled=False,
        )
        self.api = FakeApi()
        self.writer = FakeWriter()
        self.bot = ContentBot(
            settings,
            api=self.api,
            writer=self.writer,
            fetch_page=lambda url: HTML_PAGE,
            fetch_feed=lambda url: b"",
            now_fn=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        )

    def test_link_message_creates_draft_with_approval_buttons(self):
        self.bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "https://example.com/layers",
            }
        )
        self.assertEqual(len(self.writer.calls), 1)
        preview = self.api.sent_messages[-1]
        self.assertEqual(preview["chat_id"], 11)
        buttons = preview["reply_markup"]["inline_keyboard"][0]
        draft_id = buttons[0]["callback_data"].split(":", 1)[1]
        self.assertEqual(self.bot.state.load()["drafts"][draft_id]["kind"], "on_demand")
        self.api.calls.clear()
        self.bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        published = [payload for method, payload in self.api.calls if method == "sendMessage"]
        self.assertEqual(published[0]["chat_id"], "@channel")
        self.assertEqual(self.bot.state.load()["published_today"], 1)
        self.assertIsNone(self.bot.state.get_draft(draft_id))

    def test_reject_drops_draft(self):
        self.bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "https://example.com/layers",
            }
        )
        draft_id = list(self.bot.state.load()["drafts"].keys())[0]
        self.api.calls.clear()
        self.bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"reject:{draft_id}",
            }
        )
        self.assertIsNone(self.bot.state.get_draft(draft_id))
        answers = [payload for method, payload in self.api.calls if method == "answerCallbackQuery"]
        self.assertIn("Draft rejected.", answers[0]["text"])

    def test_disallowed_user_is_ignored(self):
        self.bot.handle_message(
            {
                "chat": {"id": 99},
                "from": {"id": 99},
                "text": "https://example.com/layers",
            }
        )
        self.assertEqual(self.api.sent_messages, [])
        self.assertEqual(self.bot.state.load()["drafts"], {})

    def test_daily_run_marks_state_without_sources(self):
        self.bot.settings = BotSettings(
            bot_token=self.bot.settings.bot_token,
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=str(POLICY_DIR),
            data_dir=self.tmp.name,
            scheduler_enabled=True,
        )
        self.bot.state = __import__("content_bot.state", fromlist=["StateStore"]).StateStore(
            Path(self.tmp.name) / "state.json"
        )
        self.bot.maybe_run_daily()
        self.assertEqual(self.bot.state.load()["daily_last_run"], "2026-09-07")

    def test_daily_run_waits_for_proposal_time(self):
        self.bot.settings = BotSettings(
            bot_token=self.bot.settings.bot_token,
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=str(POLICY_DIR),
            data_dir=self.tmp.name,
            scheduler_enabled=True,
        )
        self.bot.state = __import__("content_bot.state", fromlist=["StateStore"]).StateStore(
            Path(self.tmp.name) / "state.json"
        )
        self.bot.now_fn = lambda: datetime(2026, 9, 7, 3, 0, tzinfo=timezone.utc)
        self.bot.maybe_run_daily()
        self.assertEqual(self.bot.state.load().get("daily_last_run"), "")
        self.bot.now_fn = lambda: datetime(2026, 9, 7, 5, 0, tzinfo=timezone.utc)
        self.bot.maybe_run_daily()
        self.assertEqual(self.bot.state.load()["daily_last_run"], "2026-09-07")


if __name__ == "__main__":
    unittest.main()
