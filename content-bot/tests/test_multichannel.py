"""Multi-target publishing: one draft, several destinations, one ledger."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from content_bot import accounts, channels, platforms
from content_bot.bot import ContentBot
from content_bot.config import BotSettings
from content_bot.telegram import TelegramApi

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "content" / "config"

ACCOUNTS_YAML = """\
accounts:
  linkedin:
    personal:
      type: person
      access_token: token-personal
      author_urn: urn:li:person:1
    locallab:
      type: organization
      access_token: token-locallab
      author_urn: urn:li:organization:2
"""


class FakeApi(TelegramApi):
    def __init__(self):
        super().__init__("123:TESTTOKENABCDEFGHIJKLMN")
        self.sent_messages: list[dict] = []
        self.answers: list[str] = []

    def _transport(self, url, payload, *, timeout=35):
        method = url.rsplit("/", 1)[-1]
        if method == "sendMessage":
            self.sent_messages.append(payload)
        return {"ok": True, "result": {"message_id": 500 + len(self.sent_messages)}}

    def answer_callback_query(self, callback_query_id, text=""):
        self.answers.append(text)
        return True


class FakeWriter:
    def __init__(self, body="Rewritten body."):
        self.body = body
        self.chat_calls: list[dict] = []

    def chat(self, messages, max_tokens=None):
        self.chat_calls.append({"messages": messages, "max_tokens": max_tokens})
        return self.body


class FakeChannel:
    """Automatic channel that records what it was asked to publish."""

    def __init__(self, key: str, label: str, error: Exception | None = None):
        self.key = key
        self.label = label
        self.error = error
        self.sent: list[str] = []
        self.last_remote_id = f"remote-{key}"

    def send_text(self, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(text)

    def send_photo(self, filename, data, caption):  # pragma: no cover - text drafts only
        self.send_text(caption)

    def send_video(self, filename, data, caption):  # pragma: no cover - text drafts only
        self.send_text(caption)

    def send_album(self, entries, caption):  # pragma: no cover - text drafts only
        self.send_text(caption)
        return True


class MultiTargetPublishTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        Path(self.tmp, "social-accounts.yaml").write_text(ACCOUNTS_YAML, encoding="utf-8")
        (Path(self.tmp) / "editorial-policy.yaml").write_text(
            "publishing:\n  targets:\n    - linkedin_personal\n    - bale\n",
            encoding="utf-8",
        )
        settings = BotSettings(
            bot_token="123:TESTTOKENABCDEFGHIJKLMN",
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=self.tmp,
            data_dir=self.tmp,
            scheduler_enabled=False,
            platforms_enabled=True,
            bale_token="bale-token",
            bale_chat_id="@bale",
        )
        self.api = FakeApi()
        self.writer = FakeWriter()
        self.bot = ContentBot(
            settings,
            api=self.api,
            writer=self.writer,
            fetch_page=lambda url: "",
            fetch_feed=lambda url: b"",
            now_fn=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        )
        self.draft_id = "draft-multi"
        self.bot.state.add_draft(
            self.draft_id,
            {
                "id": self.draft_id,
                "chat_id": 11,
                "title": "Layer caching",
                "body": "Original body.",
                "source_url": "https://example.com/layers",
                "published_targets": [],
            },
        )

    def profiles(self):
        return platforms.load_profiles(
            {"publishing": {"targets": ["linkedin_personal", "bale"]}},
            self.bot.accounts,
        )

    def test_every_resolved_target_records_its_own_publication(self):
        personal = FakeChannel("linkedin_personal", "LinkedIn (personal)")
        bale = FakeChannel("bale", "Bale")
        self.bot.channels = {"linkedin_personal": personal, "bale": bale}
        self.bot._publish_to_targets("q1", self.bot.state.get_draft(self.draft_id), self.profiles())
        rows = self.bot.state.publications_for(self.draft_id)
        self.assertEqual(["linkedin_personal", "bale"], [row["target"] for row in rows])
        self.assertEqual(["published", "published"], [row["status"] for row in rows])
        self.assertEqual("remote-linkedin_personal", rows[0]["remote_id"])
        self.assertTrue(personal.sent and bale.sent)
        summary = [m for m in self.api.sent_messages if "Publish results:" in str(m.get("text"))]
        self.assertTrue(summary)
        self.assertIn("LinkedIn (personal): published", str(summary[-1]["text"]))

    def test_a_failed_target_does_not_stop_the_next_one(self):
        personal = FakeChannel(
            "linkedin_personal", "LinkedIn (personal)", channels.ChannelError("LinkedIn HTTP 401")
        )
        bale = FakeChannel("bale", "Bale")
        self.bot.channels = {"linkedin_personal": personal, "bale": bale}
        self.bot._publish_to_targets("q1", self.bot.state.get_draft(self.draft_id), self.profiles())
        rows = self.bot.state.publications_for(self.draft_id)
        self.assertEqual(["failed", "published"], [row["status"] for row in rows])
        self.assertIn("LinkedIn HTTP 401", rows[0]["error"])
        # Only targets with a tone and a template are rewritten; a messenger
        # channel without one keeps the original draft body.
        self.assertIn("Original body.", bale.sent[0])
        self.assertNotIn("Rewritten body.", bale.sent[0])

    def test_adaptation_uses_the_writer_and_stores_the_variant(self):
        personal = FakeChannel("linkedin_personal", "LinkedIn (personal)")
        self.bot.channels = {"linkedin_personal": personal, "bale": FakeChannel("bale", "Bale")}
        self.bot._publish_to_targets("q1", self.bot.state.get_draft(self.draft_id), self.profiles())
        variant = self.bot.state.variant_for(self.draft_id, "linkedin_personal")
        self.assertIsNotNone(variant)
        self.assertEqual("linkedin_personal", variant["tone"])
        self.assertEqual("Rewritten body.", variant["generated_text"])
        self.assertIn("Rewritten body.", personal.sent[0])
        self.assertEqual(1, len(self.writer.chat_calls))

    def test_the_stored_variant_is_reused_on_a_retry(self):
        self.bot.channels = {"linkedin_personal": FakeChannel("linkedin_personal", "L")}
        record = self.bot.state.get_draft(self.draft_id)
        first = self.bot._publish_to_channel(
            "q1", record, self.profiles()["linkedin_personal"], self.bot.channels["linkedin_personal"]
        )
        self.bot.state.update_draft(self.draft_id, {"published_targets": []})
        second = self.bot._publish_to_channel(
            "q2",
            self.bot.state.get_draft(self.draft_id),
            self.profiles()["linkedin_personal"],
            self.bot.channels["linkedin_personal"],
        )
        self.assertEqual("published", first["status"])
        self.assertEqual("published", second["status"])
        self.assertEqual(1, len(self.writer.chat_calls))

    def test_a_manual_target_is_skipped_with_a_reason(self):
        self.bot.channels = {}
        self.bot.state.update_draft(
            self.draft_id,
            {"targets": [{"platform": "linkedin", "account": "personal"}]},
        )
        self.bot._publish_to_targets("q1", self.bot.state.get_draft(self.draft_id), self.profiles())
        rows = self.bot.state.publications_for(self.draft_id)
        self.assertEqual(1, len(rows))
        self.assertEqual("skipped", rows[0]["status"])
        self.assertIn("no token", rows[0]["error"])

    def test_linkedin_channel_reports_video_drafts_as_unsupported(self):
        account = accounts.accounts_from_env(
            {"CONTENT_LINKEDIN_ACCESS_TOKEN": "t", "CONTENT_LINKEDIN_PERSON_ID": "1"}
        )["linkedin_personal"]
        channel = channels.LinkedInChannel(account, self.bot.settings)
        with self.assertRaises(channels.ChannelError) as caught:
            channel.send_video("clip.mp4", b"bytes", "caption")
        self.assertIn("video", str(caught.exception))
