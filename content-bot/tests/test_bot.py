"""Handler-level tests for the Content Bot with injected fakes."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from content_bot.bot import ContentBot
from content_bot.config import BotSettings
from content_bot.telegram import TelegramApi
from content_pipeline.normalize import canonicalize_url, content_hash as url_content_hash

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
        self.revisions = []

    def generate_post(self, item):
        self.calls.append(item)
        return {
            "title": "Generated title",
            "body": "Generated body text.",
            "source_url": "https://example.com/layers",
        }

    def revise_post(self, *, title, body, feedback, source_url):
        self.revisions.append(
            {"title": title, "body": body, "feedback": feedback, "source_url": source_url}
        )
        return {
            "title": "Revised title",
            "body": "Revised body text.",
            "source_url": source_url,
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
        self.assertEqual(preview["parse_mode"], "HTML")
        self.assertTrue(preview["text"].startswith("Draft proposal\n<b>\u202b"))
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
        self.assertEqual(published[0]["parse_mode"], "HTML")
        self.assertTrue(published[0]["text"].startswith("<b>\u202b"))
        self.assertEqual(self.bot.state.load()["published_today"], 0)
        self.assertEqual(len(self.bot.state.load()["published"]), 1)
        self.assertIsNone(self.bot.state.get_draft(draft_id))

    def test_on_demand_approve_bypasses_daily_limit(self):
        for index in range(3):
            self.bot.state.remember_published(f"hash-{index}", day="2026-09-07")
        self.assertEqual(self.bot.state.load()["published_today"], 3)
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
                "id": "q7",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        published = [payload for method, payload in self.api.calls if method == "sendMessage"]
        self.assertEqual(published[0]["chat_id"], "@channel")
        self.assertEqual(self.bot.state.load()["published_today"], 3)
        self.assertIsNone(self.bot.state.get_draft(draft_id))

    def test_on_demand_limit_can_be_enabled_by_policy(self):
        tmp_policy = Path(self.tmp.name) / "editorial-policy.yaml"
        shutil.copyfile(POLICY_DIR / "editorial-policy.yaml", tmp_policy)
        tmp_policy.write_text(
            tmp_policy.read_text(encoding="utf-8").replace(
                "unlimited_approvals: true", "unlimited_approvals: false"
            )
        )
        self.bot.settings = BotSettings(
            bot_token=self.bot.settings.bot_token,
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=self.tmp.name,
            data_dir=self.tmp.name,
            scheduler_enabled=False,
        )
        for index in range(3):
            self.bot.state.remember_published(f"hash-{index}", day="2026-09-07")
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
                "id": "q8",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        answers = [payload for method, payload in self.api.calls if method == "answerCallbackQuery"]
        self.assertIn("Daily publish limit reached", answers[0]["text"])
        self.assertIsNotNone(self.bot.state.get_draft(draft_id))
        published = [payload for method, payload in self.api.calls if method == "sendMessage"]
        self.assertEqual(published, [])

    def _add_daily_draft(self, draft_id):
        self.bot.state.add_draft(
            draft_id,
            {
                "id": draft_id,
                "kind": "daily",
                "chat_id": 11,
                "message_id": 500,
                "title": "Daily title",
                "body": "Daily body.",
                "source_url": "https://example.com/feed",
                "content_hash": f"daily-{draft_id}",
                "created_at": "2026-09-07T12:00:00+00:00",
            },
        )

    def _approve_callback(self, query_id, draft_id):
        self.bot.handle_callback(
            {
                "id": query_id,
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 500},
                "data": f"approve:{draft_id}",
            }
        )

    def test_daily_approve_counts_toward_limit(self):
        self._add_daily_draft("daily-ok")
        self.api.calls.clear()
        self._approve_callback("qd1", "daily-ok")
        published = [payload for method, payload in self.api.calls if method == "sendMessage"]
        self.assertEqual(published[0]["chat_id"], "@channel")
        self.assertEqual(self.bot.state.load()["published_today"], 1)
        self.assertIsNone(self.bot.state.get_draft("daily-ok"))

    def test_daily_approve_respects_daily_limit(self):
        for index in range(3):
            self.bot.state.remember_published(f"hash-{index}", day="2026-09-07")
        self._add_daily_draft("daily-blocked")
        self.api.calls.clear()
        self._approve_callback("qd2", "daily-blocked")
        answers = [payload for method, payload in self.api.calls if method == "answerCallbackQuery"]
        self.assertIn("Daily publish limit reached", answers[0]["text"])
        self.assertIsNotNone(self.bot.state.get_draft("daily-blocked"))
        published = [payload for method, payload in self.api.calls if method == "sendMessage"]
        self.assertEqual(published, [])

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

    def test_reply_comment_then_reject_revises_draft(self):
        self.bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "https://example.com/layers",
            }
        )
        draft_id = list(self.bot.state.load()["drafts"].keys())[0]
        message_id = self.bot.state.get_draft(draft_id)["message_id"]
        self.api.calls.clear()
        self.bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "Make the intro shorter",
                "reply_to_message": {"message_id": message_id, "from": {"is_bot": True}},
            }
        )
        self.assertEqual(
            self.bot.state.get_draft(draft_id)["feedback"], ["Make the intro shorter"]
        )
        self.api.calls.clear()
        self.bot.handle_callback(
            {
                "id": "q3",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": message_id},
                "data": f"reject:{draft_id}",
            }
        )
        draft = self.bot.state.get_draft(draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["title"], "Revised title")
        self.assertEqual(draft["body"], "Revised body text.")
        self.assertEqual(draft["feedback"], [])
        self.assertEqual(len(self.writer.revisions), 1)
        self.assertEqual(self.writer.revisions[0]["feedback"], "- Make the intro shorter")
        edits = [payload for method, payload in self.api.calls if method == "editMessageText"]
        self.assertTrue(edits)
        self.assertIn("Revised title", edits[0]["text"])
        answers = [payload for method, payload in self.api.calls if method == "answerCallbackQuery"]
        self.assertTrue(answers)

    def test_comment_then_reject_can_iterate_twice(self):
        self.bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "https://example.com/layers",
            }
        )
        draft_id = list(self.bot.state.load()["drafts"].keys())[0]
        message_id = self.bot.state.get_draft(draft_id)["message_id"]
        for note in ("Make the intro shorter", "Now simplify the ending"):
            self.api.calls.clear()
            self.bot.handle_message(
                {
                    "chat": {"id": 11},
                    "from": {"id": 11},
                    "text": note,
                    "reply_to_message": {"message_id": message_id, "from": {"is_bot": True}},
                }
            )
            self.bot.handle_callback(
                {
                    "id": "q9",
                    "from": {"id": 11},
                    "message": {"chat": {"id": 11}, "message_id": message_id},
                    "data": f"reject:{draft_id}",
                }
            )
        self.assertEqual(len(self.writer.revisions), 2)
        self.assertEqual(self.writer.revisions[0]["feedback"], "- Make the intro shorter")
        self.assertEqual(
            self.writer.revisions[1]["feedback"], "- Now simplify the ending"
        )
        draft = self.bot.state.get_draft(draft_id)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["title"], "Revised title")
        self.assertEqual(draft["feedback"], [])

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

    def _policy_dir_without_sources(self) -> str:
        policy_tmp = Path(self.tmp.name) / "policy-empty"
        policy_tmp.mkdir(exist_ok=True)
        shutil.copyfile(
            POLICY_DIR / "editorial-policy.yaml",
            policy_tmp / "editorial-policy.yaml",
        )
        return str(policy_tmp)

    def test_daily_run_marks_state_without_sources(self):
        self.bot.settings = BotSettings(
            bot_token=self.bot.settings.bot_token,
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=self._policy_dir_without_sources(),
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
            policy_dir=self._policy_dir_without_sources(),
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

    def test_forget_link_allows_the_same_link_to_be_drafted_again(self):
        url = "https://example.com/layers"
        self.bot.handle_message(
            {"chat": {"id": 11}, "from": {"id": 11}, "text": url}
        )
        draft_id = list(self.bot.state.load()["drafts"].keys())[0]
        self.bot.handle_callback(
            {
                "id": "q9",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        self.assertEqual(len(self.bot.state.load()["published"]), 1)
        self.api.sent_messages.clear()
        self.bot.handle_message(
            {"chat": {"id": 11}, "from": {"id": 11}, "text": f"/forget-link {url}"}
        )
        self.assertEqual(self.bot.state.load()["published"], [])
        self.assertTrue(self.api.sent_messages[-1]["text"].startswith("Link forgotten"))
        self.bot.handle_message(
            {"chat": {"id": 11}, "from": {"id": 11}, "text": url}
        )
        self.assertEqual(len(self.bot.state.load()["drafts"]), 1)

    def test_forget_link_reports_when_no_record_exists(self):
        self.bot.handle_message(
            {"chat": {"id": 11}, "from": {"id": 11}, "text": "/forget-link https://example.com/never"}
        )
        self.assertTrue(
            self.api.sent_messages[-1]["text"].startswith("No published record found")
        )

    def test_forget_link_underscore_command_clears_record(self):
        url = "https://example.com/forgotten"
        digest = url_content_hash(canonicalize_url(url))
        self.bot.state.remember_published(digest, day="2026-09-07")
        self.assertEqual(len(self.bot.state.load()["published"]), 1)
        self.bot.handle_message(
            {"chat": {"id": 11}, "from": {"id": 11}, "text": f"/forget_link {url}"}
        )
        self.assertEqual(self.bot.state.load()["published"], [])
        self.assertTrue(self.api.sent_messages[-1]["text"].startswith("Link forgotten"))

    def test_startup_registers_command_menu(self):
        self.bot._startup()
        registrations = [payload for method, payload in self.api.calls if method == "setMyCommands"]
        self.assertEqual(len(registrations), 2)
        scopes = [registration.get("scope") for registration in registrations]
        self.assertIn({"type": "all_private_chats"}, scopes)
        names = [command["command"] for command in registrations[0]["commands"]]
        self.assertIn("forget_link", names)
        self.assertIn("status", names)


if __name__ == "__main__":
    unittest.main()
