"""Aparat publishing: pick the platform, send the video, land the upload."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from content_bot import channels, platforms
from content_bot.bot import ContentBot
from content_bot.config import BotSettings
from content_bot.telegram import TelegramApi


class FakeApi(TelegramApi):
    def __init__(self):
        super().__init__("123:TESTTOKENABCDEFGHIJKLMN")
        self.sent_messages: list[dict] = []
        self.answers: list[str] = []

    def _transport(self, url, payload, *, timeout=35):
        if url.rsplit("/", 1)[-1] == "sendMessage":
            self.sent_messages.append(payload)
        return {"ok": True, "result": {"message_id": 900 + len(self.sent_messages)}}

    def answer_callback_query(self, callback_query_id, text=""):
        self.answers.append(text)
        return True


class FakeAparatChannel:
    """Stand-in for the Aparat adapter that records one video publish."""

    key = "aparat"
    label = "Aparat"

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.videos: list[dict] = []
        self.last_remote_id = ""
        self.last_url = ""

    def send_text(self, text):
        raise channels.ChannelError("Aparat publishes videos only")

    def send_photo(self, filename, data, caption):
        raise channels.ChannelError("Aparat publishes videos only")

    def send_video(self, filename, data, caption, *, meta=None):
        if self.error is not None:
            raise self.error
        self.videos.append(
            {"filename": filename, "data": data, "caption": caption, "meta": dict(meta or {})}
        )
        self.last_url = "https://www.aparat.com/v/abc123"
        self.last_remote_id = self.last_url


class AparatFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        settings = BotSettings(
            bot_token="123:TESTTOKENABCDEFGHIJKLMN",
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=self.tmp,
            data_dir=self.tmp,
            scheduler_enabled=False,
            platforms_enabled=True,
            aparat_token="jwt-value",
        )
        self.api = FakeApi()
        self.bot = ContentBot(
            settings,
            api=self.api,
            writer=None,
            fetch_page=lambda url: "",
            fetch_feed=lambda url: b"",
            now_fn=lambda: datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc),
        )
        self.channel = FakeAparatChannel()
        self.bot.channels = {"aparat": self.channel}
        self.draft_id = "draft-aparat"
        self.bot.state.add_draft(
            self.draft_id,
            {
                "id": self.draft_id,
                "chat_id": 11,
                "title": "Layer caching",
                "body": "Body text about #docker images.",
                "source_url": "https://example.com/layers",
                "published_targets": [],
            },
        )

    def record(self) -> dict:
        return self.bot.state.get_draft(self.draft_id)

    def store_video(self) -> str:
        media_dir = Path(self.tmp) / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        path = media_dir / f"{self.draft_id}.mp4"
        path.write_bytes(b"video-bytes")
        self.bot.state.update_draft(
            self.draft_id,
            {
                "media": {
                    "kind": "video",
                    "driver": "user-upload",
                    "status": "done",
                    "artifact": path.name,
                    "local_path": str(path),
                    "duration": "",
                }
            },
        )
        return str(path)

    def test_picking_aparat_without_a_video_asks_for_one(self):
        self.bot._platform_choice("q1", "aparat", self.draft_id)
        record = self.record()
        self.assertEqual("aparat", record.get("pending_target"))
        self.assertEqual("video", record.get("media_wait_kind"))
        self.assertEqual("awaiting_media", record.get("status"))
        self.assertIn("Send the video", str(self.api.sent_messages[-1]["text"]))
        self.assertEqual([], self.channel.videos)
        rows = self.bot.state.publications_for(self.draft_id)
        self.assertEqual("skipped", rows[-1]["status"])
        self.assertIn("waiting for a video", rows[-1]["error"])

    def test_the_video_that_arrives_is_uploaded_and_confirmed(self):
        self.bot._platform_choice("q1", "aparat", self.draft_id)
        self.store_video()
        self.bot._publish_pending_target(self.draft_id)
        self.assertEqual(1, len(self.channel.videos))
        video = self.channel.videos[0]
        self.assertEqual(b"video-bytes", video["data"])
        self.assertEqual("Layer caching", video["meta"]["title"])
        self.assertIn("docker", video["meta"]["tags"])
        self.assertIsNone(self.record().get("pending_target"))
        rows = self.bot.state.publications_for(self.draft_id)
        self.assertEqual("published", rows[-1]["status"])
        self.assertEqual("https://www.aparat.com/v/abc123", rows[-1]["remote_id"])
        self.assertIn("https://www.aparat.com/v/abc123", str(self.api.sent_messages[-1]["text"]))

    def test_the_target_is_remembered_until_the_video_arrives(self):
        self.bot._platform_choice("q1", "aparat", self.draft_id)
        self.bot._publish_pending_target(self.draft_id)
        self.assertEqual([], self.channel.videos)
        self.assertEqual("aparat", self.record().get("pending_target"))
        self.store_video()
        self.bot._publish_pending_target(self.draft_id)
        self.assertEqual(1, len(self.channel.videos))
        self.assertEqual("aparat", self.record().get("published_targets")[0])

    def test_a_failed_upload_keeps_the_draft_and_reports_the_reason(self):
        self.channel.error = channels.ChannelError("Aparat: session expired")
        self.bot._platform_choice("q1", "aparat", self.draft_id)
        self.store_video()
        self.bot.state.update_draft(self.draft_id, {"pending_target": "aparat"})
        self.bot._publish_pending_target(self.draft_id)
        rows = self.bot.state.publications_for(self.draft_id)
        self.assertEqual("failed", rows[-1]["status"])
        self.assertIn("session expired", rows[-1]["error"])
        self.assertIn("session expired", str(self.api.sent_messages[-1]["text"]))

    def test_an_oversized_telegram_clip_is_reported_with_its_limit(self):
        self.bot.state.update_draft(
            self.draft_id,
            {
                "media": {
                    "kind": "video",
                    "driver": "user-upload",
                    "status": "done",
                    "artifact": "clip.mp4",
                    "local_path": "",
                    "file_id": "telegram-file",
                    "oversized": True,
                    "duration": "",
                }
            },
        )
        profile = self.bot._platform_profiles({})["aparat"]
        row = self.bot._publish_to_channel(
            None, self.record(), profile, self.channel
        )
        self.assertEqual("failed", row["status"])
        self.assertIn("20 MB", row["error"])
        self.assertEqual([], self.channel.videos)

    def test_all_targets_leaves_aparat_out_without_a_video(self):
        profiles = self.bot._platform_profiles({})
        default = [key for key in profiles if self.bot._channel_for(profiles[key])]
        self.assertEqual(["aparat"], default)
        self.assertEqual([], self.bot._resolved_targets(self.record(), profiles))
        self.store_video()
        self.assertEqual(
            ["aparat"], self.bot._resolved_targets(self.record(), profiles)
        )

    def test_an_archived_draft_points_back_at_a_new_draft(self):
        self.bot.state.archive_package(self.record())
        self.bot.state.drop_draft(self.draft_id)
        self.bot._platform_choice("q1", "aparat", self.draft_id)
        self.assertIn(
            "no longer waits for media", str(self.api.sent_messages[-1]["text"])
        )
        rows = self.bot.state.publications_for(self.draft_id)
        self.assertEqual("skipped", rows[-1]["status"])

    def test_the_chooser_marks_the_platform_that_needs_a_video(self):
        self.bot._offer_platforms("q1", self.record())
        keyboard = None
        for message in reversed(self.api.sent_messages):
            if "reply_markup" in message:
                keyboard = message["reply_markup"]
                break
        labels = [
            button["text"]
            for row in keyboard["inline_keyboard"]
            for button in row
        ]
        self.assertIn("Aparat (needs a video)", labels)
        self.store_video()
        self.bot._offer_platforms("q2", self.record())
        labels = [
            button["text"]
            for row in self.api.sent_messages[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertIn("Aparat (auto)", labels)

    def test_the_profile_of_the_live_channel_is_automatic_and_video_only(self):
        profile = self.bot._platform_profiles({})["aparat"]
        self.assertEqual("auto", profile.mode)
        self.assertTrue(platforms.needs_video(profile))
        self.assertEqual("aparat", self.bot._channel_for(profile).key)


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
