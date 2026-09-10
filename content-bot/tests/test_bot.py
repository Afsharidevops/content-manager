"""Handler-level tests for the Content Bot with injected fakes."""

from __future__ import annotations

import json
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
        self.video_calls = []

    def video_script(self, *, title, body, source_url="", segments=3):
        self.video_calls.append(
            {
                "title": title,
                "body": body,
                "source_url": source_url,
                "segments": segments,
            }
        )
        return [
            {
                "say": f"جمله شماره {index}",
                "visual": f"Shot number {index} of the story",
            }
            for index in range(1, segments + 1)
        ]

    def generate_post(self, item, guidance=""):
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
        self.uploads = []
        self.downloads = []
        self.download_bytes = b"fake-media-bytes"
        self.file_size = len(self.download_bytes)

    def _transport(self, url, payload):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, payload))
        if method == "getMe":
            return {"ok": True, "result": {"username": "content_test_bot"}}
        if method == "sendMessage":
            self.sent_messages.append(payload)
            return {"ok": True, "result": {"message_id": 100 + len(self.sent_messages)}}
        if method == "getFile":
            return {
                "ok": True,
                "result": {
                    "file_path": f"docs/{payload['file_id']}.bin",
                    "file_size": self.file_size,
                },
            }
        if method == "deleteMessage":
            return {"ok": True, "result": True}
        return {"ok": True, "result": True}

    def download_file(self, file_path, max_bytes=25_000_000):
        self.downloads.append(file_path)
        return self.download_bytes

    def _upload(self, method, fields, *, file_field, filename, file_bytes):
        self.uploads.append((method, fields, file_field, filename, file_bytes))
        return {"ok": True, "result": {"message_id": 200 + len(self.uploads)}}

    def send_media_group(self, chat_id, files, *, caption="", parse_mode=None):
        self.uploads.append(("sendMediaGroup", {"chat_id": chat_id}, None, "", b""))
        self.sent_messages.append(
            {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}
        )
        return {"message_id": 300 + len(self.uploads)}


class FakeMedia:
    def __init__(self, artifact=("image_0.png", "image")):
        self.submits = []
        self.downloads = []
        self.job_ids = []
        self.status_by_job = {}
        self.artifact = artifact
        self.brand_calls = []
        self.fail_brand = False

    def brand_image(self, content, *, content_type="image/png"):
        if self.fail_brand:
            from content_bot.mediastudio import MediaStudioError

            raise MediaStudioError("brand service unavailable")
        self.brand_calls.append(content_type)
        return b"branded:" + content

    def submit(self, driver, prompt, params=None):
        self.submits.append((driver, prompt, params or {}))
        job_id = f"job-{len(self.submits)}"
        self.job_ids.append(job_id)
        self.status_by_job[job_id] = "running"
        return job_id

    def job(self, job_id):
        status = self.status_by_job.get(job_id, "running")
        artifacts = (
            [{"name": self.artifact[0], "kind": self.artifact[1], "size": 4}]
            if status == "done"
            else []
        )
        return {"id": job_id, "status": status, "artifacts": artifacts}

    def download(self, job_id, name):
        self.downloads.append((job_id, name))
        if self.artifact[1] == "video":
            return b"\x00\x00\x00\x18ftypmp42video"
        return b"\x89PNG\r\n\x1a\nimage"

    def pick_artifact(self, job):
        for artifact in job.get("artifacts") or []:
            if artifact.get("kind") in {"image", "video"} and artifact.get("name"):
                return artifact["name"], artifact["kind"]
        return None


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
        preview = self.api.sent_messages[0]
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
        self.assertTrue(self.bot.state.get_draft(draft_id)["discard_pending"])
        self.bot.handle_callback(
            {
                "id": "q3",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"reject:{draft_id}",
            }
        )
        self.assertIsNone(self.bot.state.get_draft(draft_id))
        answers = [payload for method, payload in self.api.calls if method == "answerCallbackQuery"]
        self.assertIn("Draft rejected.", answers[-1]["text"])

    def test_cancel_keeps_draft(self):
        self.bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "https://example.com/layers",
            }
        )
        draft_id = list(self.bot.state.load()["drafts"].keys())[0]
        self.bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"reject:{draft_id}",
            }
        )
        self.bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"cancel:{draft_id}",
            }
        )
        record = self.bot.state.get_draft(draft_id)
        self.assertIsNotNone(record)
        self.assertFalse(record["discard_pending"])

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


def _media_settings(
    tmp_dir: str,
    *,
    instagram: bool = False,
    character: str = "",
) -> BotSettings:
    return BotSettings(
        bot_token="123:TESTTOKENABCDEFGHIJKLMN",
        telegram_channel="@channel",
        telegram_users=frozenset({11}),
        policy_dir=str(POLICY_DIR),
        data_dir=tmp_dir,
        scheduler_enabled=False,
        media_studio_url="http://media-studio:8850",
        video_character=character,
        instagram_business_id="17841400000000000" if instagram else "",
        instagram_access_token="IGQ-test-token" if instagram else "",
    )


class MediaFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = _media_settings(self.tmp.name)
        self.api = FakeApi()
        self.writer = FakeWriter()
        self.media = FakeMedia()

    def build_bot(self, *, artifact=("image_0.png", "image"), search=None):
        self.media = FakeMedia(artifact=artifact)
        return ContentBot(
            self.settings,
            api=self.api,
            writer=self.writer,
            media=self.media,
            search_topic=search or (lambda query, **kwargs: []),
            fetch_page=lambda url: HTML_PAGE,
            fetch_feed=lambda url: b"",
            now_fn=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        )

    def package_text(self):
        """Join every message body so package asserts cover chunked sends."""
        parts = []
        for method, payload in self.api.calls:
            if method not in {"editMessageText", "sendMessage"}:
                continue
            if not isinstance(payload, dict):
                continue
            text = str(payload.get("text") or "")
            if text:
                parts.append(text)
        return "\n".join(parts)

    def send_link(self, bot):
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "https://example.com/layers",
            }
        )
        drafts = bot.state.load()["drafts"]
        self.assertEqual(len(drafts), 1)
        return next(iter(drafts))

    def test_topic_message_searches_and_drafts(self):
        queries = []

        def fake_search(query, *, limit=5, timeout=25):
            queries.append(query)
            return [
                {
                    "title": "Containers explained",
                    "url": "https://example.com/containers",
                    "snippet": "A long enough snippet explaining container images for the draft.",
                }
            ]

        bot = self.build_bot(search=fake_search)
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "container images for beginners",
            }
        )
        self.assertEqual(queries, ["container images for beginners"])
        self.assertEqual(len(self.writer.calls), 1)
        self.assertTrue(
            any("Searching for" in m["text"] for m in self.api.sent_messages)
        )
        self.assertTrue(
            any(m["text"].startswith("Draft proposal") for m in self.api.sent_messages)
        )

    def test_short_page_falls_back_to_search(self):
        bot = self.build_bot(
            search=lambda query, **kwargs: [
                {
                    "title": "Extra context",
                    "url": "https://example.com/more",
                    "snippet": "Rich context found by search for the same topic.",
                }
            ]
        )
        bot.fetch_page = lambda url: "<html><body><article>too short</article></body></html>"
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "text": "https://example.com/layers",
            }
        )
        self.assertEqual(len(self.writer.calls), 1)
        draft_id = next(iter(bot.state.load()["drafts"]))
        self.assertEqual(
            bot.state.load()["drafts"][draft_id]["source_url"],
            "https://example.com/layers",
        )

    def test_image_choice_generates_preview_and_publishes(self):
        bot = self.build_bot(artifact=("image_0.png", "image"))
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:image:{draft_id}",
            }
        )
        self.assertEqual(self.media.submits[0][0], "api-image")
        self.assertEqual(bot.state.load()["drafts"][draft_id]["status"], "media_running")
        self.media.status_by_job[self.media.job_ids[0]] = "done"
        bot.maybe_poll_media_jobs()
        state = bot.state.load()["drafts"][draft_id]
        self.assertEqual(state["status"], "media_ready")
        self.assertEqual(state["media"]["artifact"], "image_0.png")
        preview = [u for u in self.api.uploads if u[0] == "sendPhoto" and u[1]["chat_id"] == 11]
        self.assertEqual(len(preview), 1)
        self.assertTrue(bot.state.lessons(1) == [])
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        published_media = [
            u for u in self.api.uploads if u[0] == "sendPhoto" and u[1]["chat_id"] == "@channel"
        ]
        self.assertEqual(len(published_media), 1)
        self.assertEqual(published_media[0][1]["parse_mode"], "HTML")
        self.assertTrue(published_media[0][1]["caption"].startswith("<b>"))
        self.assertNotIn(
            "draft",
            bot.state.load().get("drafts", {}),
        )
        self.assertNotIn(draft_id, bot.state.load()["drafts"])

    def test_media_ask_offers_ai_image_upload_and_video_prompt(self):
        bot = self.build_bot()
        self.send_link(bot)
        asks = [m for m in self.api.sent_messages if "Add media to this post" in m["text"]]
        self.assertEqual(len(asks), 1)
        labels = []
        for row in asks[0]["reply_markup"]["inline_keyboard"]:
            labels.extend(button["text"] for button in row)
        self.assertIn("AI image", labels)
        self.assertIn("Send my image", labels)
        self.assertIn("My video (get a prompt)", labels)

    def test_user_uploaded_photo_attaches_and_publishes(self):
        bot = self.build_bot(artifact=("image_0.png", "image"))
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:user_image:{draft_id}",
            }
        )
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "awaiting_media")
        self.assertEqual(record["media_wait_kind"], "image")
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "photo": [
                    {"file_id": "small", "file_size": 10, "width": 10, "height": 10},
                    {"file_id": "big", "file_size": 500, "width": 100, "height": 100},
                ],
            }
        )
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "media_ready")
        self.assertEqual(record["media"]["driver"], "user-upload")
        self.assertEqual(record["media"]["kind"], "image")
        self.assertTrue(Path(record["media"]["local_path"]).is_file())
        stored = Path(record["media"]["local_path"]).read_bytes()
        self.assertTrue(stored.startswith(b"branded:"))
        self.assertEqual(self.media.brand_calls, ["image/jpeg"])
        preview = [
            u for u in self.api.uploads if u[0] == "sendPhoto" and u[1]["chat_id"] == 11
        ]
        self.assertEqual(len(preview), 1)
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        published = [
            u for u in self.api.uploads if u[0] == "sendPhoto" and u[1]["chat_id"] == "@channel"
        ]
        self.assertEqual(len(published), 1)
        self.assertTrue(published[0][1]["caption"].startswith("<b>"))

    def test_media_preview_offers_instagram_buttons_when_configured(self):
        self.settings = _media_settings(self.tmp.name, instagram=True)
        bot = self.build_bot()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:user_image:{draft_id}",
            }
        )
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "photo": [
                    {"file_id": "big", "file_size": 500, "width": 100, "height": 100}
                ],
            }
        )
        previews = [
            upload
            for upload in self.api.uploads
            if upload[0] == "sendPhoto"
            and upload[1]["chat_id"] == 11
            and "reply_markup" in upload[1]
        ]
        self.assertEqual(len(previews), 1)
        markup = json.loads(previews[0][1]["reply_markup"])
        callbacks = [
            button["callback_data"]
            for row in markup["inline_keyboard"]
            for button in row
        ]
        self.assertIn(f"approve_ig:{draft_id}", callbacks)
        self.assertIn(f"approve_both:{draft_id}", callbacks)

    def test_album_preview_offers_instagram_buttons_when_configured(self):
        self.settings = _media_settings(self.tmp.name, instagram=True)
        bot = self.build_bot()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:user_images:{draft_id}",
            }
        )
        for index in (1, 2):
            bot.handle_message(
                {
                    "chat": {"id": 11},
                    "from": {"id": 11},
                    "photo": [
                        {
                            "file_id": f"photo-{index}",
                            "file_size": 500,
                            "width": 100,
                            "height": 100,
                        }
                    ],
                }
            )
        follow_ups = [
            message
            for message in self.api.sent_messages
            if message.get("reply_markup")
            and "photos ready above" in str(message.get("text") or "")
        ]
        self.assertTrue(follow_ups)
        callbacks = [
            button["callback_data"]
            for row in follow_ups[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertIn(f"approve:{draft_id}", callbacks)
        self.assertIn(f"approve_ig:{draft_id}", callbacks)

    def test_approve_to_instagram_skips_the_telegram_channel(self):
        self.settings = _media_settings(self.tmp.name, instagram=True)
        bot = self.build_bot()
        published = {}

        class FakePublisher:
            def publish(self, caption, items):
                published["caption"] = caption
                published["items"] = items
                return {"kind": "photo", "media_id": "ig-1"}

        bot._instagram = FakePublisher()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:user_image:{draft_id}",
            }
        )
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "photo": [
                    {"file_id": "big", "file_size": 500, "width": 100, "height": 100}
                ],
            }
        )
        self.api.calls.clear()
        self.api.uploads.clear()
        bot.handle_callback(
            {
                "id": "qig",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve_ig:{draft_id}",
            }
        )
        self.assertEqual(published["items"][0]["kind"], "image")
        self.assertTrue(published["caption"])
        self.assertEqual(
            [upload for upload in self.api.uploads if upload[1].get("chat_id") == "@channel"],
            [],
        )
        self.assertIsNone(bot.state.get_draft(draft_id))
        edits = [
            payload.get("text", "")
            for method, payload in self.api.calls
            if method == "editMessageText"
        ]
        self.assertTrue(any("Published to Instagram." in text for text in edits))

    def test_approve_to_both_publishes_telegram_and_instagram(self):
        self.settings = _media_settings(self.tmp.name, instagram=True)
        bot = self.build_bot()
        published = {}

        class FakePublisher:
            def publish(self, caption, items):
                published["items"] = items
                return {"kind": "photo", "media_id": "ig-2"}

        bot._instagram = FakePublisher()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:user_image:{draft_id}",
            }
        )
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "photo": [
                    {"file_id": "big", "file_size": 500, "width": 100, "height": 100}
                ],
            }
        )
        self.api.uploads.clear()
        bot.handle_callback(
            {
                "id": "qboth",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve_both:{draft_id}",
            }
        )
        self.assertTrue(
            [upload for upload in self.api.uploads if upload[1].get("chat_id") == "@channel"]
        )
        self.assertEqual(published["items"][0]["kind"], "image")
        self.assertIsNone(bot.state.get_draft(draft_id))

    def test_uploaded_image_branding_failure_keeps_original(self):
        bot = self.build_bot(artifact=("image_0.png", "image"))
        self.media.fail_brand = True
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:user_image:{draft_id}",
            }
        )
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "photo": [{"file_id": "big", "file_size": 500, "width": 100, "height": 100}],
            }
        )
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "media_ready")
        self.assertEqual(Path(record["media"]["local_path"]).read_bytes(), b"fake-media-bytes")

    def test_uploaded_video_is_not_branded(self):
        bot = self.build_bot(artifact=("clip.mp4", "video"))
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:video_prompt:{draft_id}",
            }
        )
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:script10:{draft_id}",
            }
        )
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "video": {
                    "file_id": "vid1",
                    "mime_type": "video/mp4",
                    "file_name": "clip.mp4",
                },
            }
        )
        self.assertEqual(self.media.brand_calls, [])
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "media_ready")

    def test_video_prompt_character_package_has_extend_segments(self):
        self.settings = _media_settings(self.tmp.name, character="@mohammad")
        bot = self.build_bot()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:video_prompt:{draft_id}",
            }
        )
        style_rows = [
            payload["reply_markup"]["inline_keyboard"]
            for method, payload in self.api.calls
            if method == "editMessageText" and isinstance(payload, dict)
        ][-1]
        labels = [button["text"] for row in style_rows for button in row]
        self.assertIn("With my character", labels)
        self.assertIn("AI promo (no character)", labels)
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:vstyle_char:{draft_id}",
            }
        )
        bot.handle_callback(
            {
                "id": "q3",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:script30:{draft_id}",
            }
        )
        package = self.package_text()
        self.assertIn("3 segment(s)", package)
        self.assertIn("Segment 1 prompt:", package)
        self.assertIn("Segment 2 prompt (press Extend, then paste):", package)
        self.assertIn("Segment 3 prompt (press Extend, then paste):", package)
        self.assertIn("@mohammad is speaking directly to the camera", package)
        self.assertIn("Continue directly from the last frame", package)
        self.assertIn("جمله شماره 1", package)
        self.assertEqual(len(bot.writer.video_calls), 1)
        self.assertEqual(bot.writer.video_calls[0]["segments"], 3)

    def test_long_character_prompt_sends_one_message_per_segment(self):
        self.settings = _media_settings(self.tmp.name, character="@mohammad")
        bot = self.build_bot()
        draft_id = self.send_link(bot)
        for index, data in enumerate(
            (
                f"media:video_prompt:{draft_id}",
                f"media:vstyle_char:{draft_id}",
                f"media:script30:{draft_id}",
            )
        ):
            bot.handle_callback(
                {
                    "id": f"q{index}",
                    "from": {"id": 11},
                    "message": {"chat": {"id": 11}, "message_id": 103},
                    "data": data,
                }
            )
        chunks = [
            message["text"]
            for message in self.api.sent_messages
            if str(message.get("text") or "").startswith("<pre>")
        ]
        self.assertEqual(len(chunks), 3)
        self.assertIn("Segment 1 prompt:", chunks[0])
        self.assertIn("Segment 2 prompt (press Extend, then paste):", chunks[1])
        self.assertIn("Segment 3 prompt (press Extend, then paste):", chunks[2])
        for chunk in chunks:
            self.assertIn("9:16 vertical", chunk)
            self.assertLessEqual(len(chunk), 3000)

    def test_ai_promo_package_uses_shots_without_the_character(self):
        self.settings = _media_settings(self.tmp.name, character="@mohammad")
        bot = self.build_bot()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:video_prompt:{draft_id}",
            }
        )
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:vstyle_ai:{draft_id}",
            }
        )
        bot.handle_callback(
            {
                "id": "q3",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:script10:{draft_id}",
            }
        )
        package = self.package_text()
        self.assertIn("1 segment(s)", package)
        self.assertIn("Shot number 1 of the story", package)
        self.assertNotIn("@mohammad", package)
        self.assertIn("Optional Persian voiceover line", package)

    def test_video_prompt_falls_back_to_post_text_without_script_model(self):
        bot = self.build_bot()

        class PlainWriter:
            def generate_post(self, item, guidance=""):
                return {"title": "T", "body": "B", "source_url": ""}

            def revise_post(self, **kwargs):
                return {"title": "T", "body": "B", "source_url": ""}

        bot.writer = PlainWriter()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:video_prompt:{draft_id}",
            }
        )
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:script10:{draft_id}",
            }
        )
        package = self.package_text()
        self.assertIn("No script model was available", package)
        self.assertIn("Segment 1 prompt:", package)

    def test_video_prompt_then_uploaded_video_publishes(self):
        bot = self.build_bot(artifact=("clip.mp4", "video"))
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:video_prompt:{draft_id}",
            }
        )
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:script10:{draft_id}",
            }
        )
        edits = [
            payload["text"]
            for method, payload in self.api.calls
            if method == "editMessageText" and isinstance(payload, dict)
        ]
        self.assertTrue(any("Reel prompt package" in text for text in edits))
        self.assertTrue(any("<pre>" in text for text in edits))
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "awaiting_media")
        self.assertEqual(record["media_wait_kind"], "video")
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "video": {
                    "file_id": "vid1",
                    "mime_type": "video/mp4",
                    "file_name": "clip.mp4",
                },
            }
        )
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "media_ready")
        self.assertEqual(record["media"]["kind"], "video")
        self.assertEqual(record["media"]["driver"], "user-upload")
        self.assertTrue(record["media"]["local_path"].endswith(".mp4"))
        preview = [
            u for u in self.api.uploads if u[0] == "sendVideo" and u[1]["chat_id"] == 11
        ]
        self.assertEqual(len(preview), 1)
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        published = [
            u for u in self.api.uploads if u[0] == "sendVideo" and u[1]["chat_id"] == "@channel"
        ]
        self.assertEqual(len(published), 1)

    def test_upload_without_pending_draft_is_rejected(self):
        bot = self.build_bot()
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "photo": [{"file_id": "x", "file_size": 1}],
            }
        )
        self.assertTrue(
            any(
                "No draft is waiting for media" in message["text"]
                for message in self.api.sent_messages
            )
        )

    def test_upload_wrong_kind_keeps_waiting(self):
        bot = self.build_bot()
        draft_id = self.send_link(bot)
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:user_image:{draft_id}",
            }
        )
        bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "video": {"file_id": "vid1", "mime_type": "video/mp4"},
            }
        )
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "awaiting_media")
        self.assertTrue(
            any(
                "waiting for an image" in message["text"]
                for message in self.api.sent_messages
            )
        )

    def test_cancel_upload_returns_to_media_ask(self):
        bot = self.build_bot()
        draft_id = self.send_link(bot)
        ask_id = bot.state.load()["drafts"][draft_id]["ask_message_id"]
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": ask_id},
                "data": f"media:user_image:{draft_id}",
            }
        )
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": ask_id},
                "data": f"media:cancel_upload:{draft_id}",
            }
        )
        record = bot.state.load()["drafts"][draft_id]
        self.assertEqual(record["status"], "media_ask")
        self.assertIsNone(record.get("media_wait_kind"))

    def test_long_media_post_publishes_caption_plus_continuation(self):
        bot = self.build_bot(artifact=("image_0.png", "image"))
        draft_id = self.send_link(bot)
        paragraphs = [
            f"Chapter paragraph {index} with filler words repeated over and over to make the article long."
            for index in range(60)
        ]
        body = "\n\n".join(paragraphs) + "\n\nhttps://example.com/layers"
        bot.state.update_draft(
            draft_id,
            {"body": body, "source_url": "https://example.com/layers"},
        )
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 103},
                "data": f"media:image:{draft_id}",
            }
        )
        self.media.status_by_job[self.media.job_ids[0]] = "done"
        bot.maybe_poll_media_jobs()
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        published_media = [
            u for u in self.api.uploads if u[0] == "sendPhoto" and u[1]["chat_id"] == "@channel"
        ]
        self.assertEqual(len(published_media), 1)
        caption = published_media[0][1]["caption"]
        self.assertLessEqual(len(caption), 1024)
        self.assertNotIn('href="https://example.com/layers"', caption)
        channel_texts = [
            m for m in self.api.sent_messages if m["chat_id"] == "@channel"
        ]
        self.assertGreaterEqual(len(channel_texts), 1)
        self.assertTrue(all(len(m["text"]) <= 4096 for m in channel_texts))
        self.assertTrue(channel_texts[0]["text"].startswith("…"))
        self.assertTrue(channel_texts[-1]["text"].endswith("</a>"))
        self.assertIn('href="https://example.com/layers"', channel_texts[-1]["text"])
        combined = caption + "\n\n" + "\n\n".join(m["text"] for m in channel_texts)
        for paragraph in paragraphs:
            self.assertIn(paragraph, combined)

    def test_video_duration_then_preview_and_publish(self):
        bot = self.build_bot(artifact=("flow_video.mp4", "video"))
        draft_id = self.send_link(bot)
        ask_id = bot.state.load()["drafts"][draft_id]["ask_message_id"]
        bot.handle_callback(
            {
                "id": "q1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": ask_id},
                "data": f"media:video:{draft_id}",
            }
        )
        self.assertEqual(len(self.media.submits), 0)
        bot.handle_callback(
            {
                "id": "q2",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": ask_id},
                "data": f"media:video:{draft_id}",
            }
        )
        self.assertEqual(self.media.submits[0][0], "flow-video")
        self.media.status_by_job[self.media.job_ids[0]] = "done"
        bot.maybe_poll_media_jobs()
        preview = [u for u in self.api.uploads if u[0] == "sendVideo" and u[1]["chat_id"] == 11]
        self.assertEqual(len(preview), 1)
        bot.handle_callback(
            {
                "id": "q3",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        published_media = [
            u for u in self.api.uploads if u[0] == "sendVideo" and u[1]["chat_id"] == "@channel"
        ]
        self.assertEqual(len(published_media), 1)


class MediaCaptionTest(unittest.TestCase):
    def test_short_body_fits_in_caption(self):
        from content_bot.bot import _media_caption

        record = {
            "title": "تیتر نمونه",
            "body": "بدنه کوتاه برای تست.\n\nhttps://example.com/x",
            "source_url": "https://example.com/x",
        }
        caption = _media_caption(record)
        self.assertLessEqual(len(caption), 1024)
        self.assertTrue(caption.startswith("<b>"))
        self.assertIn('href="https://example.com/x"', caption)
        self.assertTrue(caption.endswith("</a>"))

    def test_long_body_caption_fits_and_link_moves_to_last_message(self):
        from content_bot.bot import _media_caption, _media_caption_messages

        long_body = "\n\n".join(
            f"پاراگراف شماره {index} با کمی متن تکراری برای بلند کردن کپشن" for index in range(80)
        )
        record = {
            "title": "تیتر بلند",
            "body": f"{long_body}\n\nhttps://example.com/y",
            "source_url": "https://example.com/y",
        }
        caption = _media_caption(record)
        self.assertLessEqual(len(caption), 1024)
        self.assertNotIn('href="https://example.com/y"', caption)
        messages = _media_caption_messages(record)
        self.assertIn('href="https://example.com/y"', messages[-1])

    def test_short_body_is_a_single_media_message(self):
        from content_bot.bot import _media_caption, _media_caption_messages

        record = {
            "title": "A short post",
            "body": "One paragraph that easily fits the caption.\n\nhttps://example.com/z",
            "source_url": "https://example.com/z",
        }
        messages = _media_caption_messages(record)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0], _media_caption(record))
        self.assertLessEqual(len(messages[0]), 1024)

    def test_long_body_is_split_into_caption_and_continuation(self):
        from content_bot.bot import _media_caption_messages

        paragraphs = [
            f"Paragraph number {index} with repeated filler text to inflate the caption size."
            for index in range(40)
        ]
        record = {
            "title": "A long post",
            "body": "\n\n".join(paragraphs) + "\n\nhttps://example.com/long",
            "source_url": "https://example.com/long",
        }
        messages = _media_caption_messages(record)
        self.assertGreater(len(messages), 1)
        self.assertLessEqual(len(messages[0]), 1024)
        self.assertTrue(all(len(message) <= 4096 for message in messages))
        self.assertNotIn('href="https://example.com/long"', messages[0])
        self.assertTrue(messages[1].startswith("…"))
        self.assertTrue(messages[-1].endswith("</a>"))
        self.assertIn('href="https://example.com/long"', messages[-1])
        combined = "\n\n".join(messages)
        for paragraph in paragraphs:
            self.assertIn(paragraph, combined)


class BotRtlRenderTest(unittest.TestCase):
    """Mixed Persian/Latin lines keep a right-to-left base direction."""

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
        self.bot = ContentBot(
            settings,
            api=FakeApi(),
            writer=FakeWriter(),
            fetch_page=lambda url: HTML_PAGE,
            fetch_feed=lambda url: b"",
            now_fn=lambda: datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        )
        self.record = {
            "kind": "on_demand",
            "title": "معرفی ابزار",
            "body": "Argo Workflows یک ابزار متنباز است.\n\n"
            "اگر با Kubernetes کار میکنید، این ابزار گزینه مناسبی است.\n\n"
            "https://example.com/argo",
            "source_url": "https://example.com/argo",
        }

    def test_latin_start_lines_are_prefixed_with_rtl_mark(self):
        from content_bot.bot import _rtl_body_html

        html = _rtl_body_html(self.record["body"])
        self.assertIn("\u200fArgo Workflows یک ابزار", html)
        self.assertIn("\n\nاگر با Kubernetes", html)

    def test_pure_english_lines_keep_ltr(self):
        from content_bot.bot import _rtl_body_html

        html = _rtl_body_html("Only English words here.\n\nhttps://example.com/x")
        self.assertEqual(html.count("\u200f"), 0)

    def test_preview_and_channel_text_force_rtl_on_mixed_lines(self):
        preview = self.bot.preview_text(self.record)
        self.assertIn("\u200fArgo Workflows", preview)
        channel = self.bot.channel_text(self.record)
        self.assertIn("\u200fArgo Workflows", channel)
        self.assertNotIn("\u200fاگر با Kubernetes", channel)


class MultiPhotoAndInstagramTests(BotTestCase):
    def setUp(self):
        super().setUp()
        from content_bot.mediastudio import MediaStudio

        class AskOnlyFakeMedia(FakeMedia):
            def submit(self, driver, prompt, params=None):
                from content_bot.mediastudio import MediaStudioError

                raise MediaStudioError("ask only")

        self.bot.media = AskOnlyFakeMedia()

    def _draft_with_media_ask(self, *, choice: str = "user_images"):
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
                "id": "qm1",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"media:{choice}:{draft_id}",
            }
        )
        return draft_id

    def _send_photo(self, draft_id, index):
        self.bot.handle_message(
            {
                "chat": {"id": 11},
                "from": {"id": 11},
                "photo": [{"file_id": f"photo-{index}", "file_size": 1000}],
            }
        )

    def test_send_several_images_collects_album(self):
        draft_id = self._draft_with_media_ask()
        self._send_photo(draft_id, 1)
        self._send_photo(draft_id, 2)
        record = self.bot.state.get_draft(draft_id)
        self.assertEqual(record["status"], "media_ready")
        self.assertTrue(record["media_collect"])
        media = record["media"]
        self.assertEqual(len(media["files"]), 2)
        self.assertEqual(media["kind"], "image")
        self.assertTrue(
            any(method == "sendMediaGroup" for method, _, _, _, _ in self.api.uploads)
        )

    def test_done_closes_collection_and_publishes_album(self):
        draft_id = self._draft_with_media_ask()
        self._send_photo(draft_id, 1)
        self._send_photo(draft_id, 2)
        self.bot.handle_callback(
            {
                "id": "qdone",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"media:done:{draft_id}",
            }
        )
        record = self.bot.state.get_draft(draft_id)
        self.assertIsNone(record["media_wait_kind"])
        self.assertEqual(len(record["media"]["files"]), 2)
        self.api.uploads.clear()
        self.bot.handle_callback(
            {
                "id": "qappr",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve:{draft_id}",
            }
        )
        self.assertTrue(
            any(method == "sendMediaGroup" for method, _, _, _, _ in self.api.uploads)
        )
        self.assertIsNone(self.bot.state.get_draft(draft_id))

    def test_single_photo_preview_has_plain_approval_without_config(self):
        draft_id = self._draft_with_media_ask(choice="user_image")
        self._send_photo(draft_id, 1)
        previews = [
            upload
            for upload in self.api.uploads
            if upload[0] == "sendPhoto"
            and upload[1]["chat_id"] == 11
            and "reply_markup" in upload[1]
        ]
        self.assertEqual(len(previews), 1)
        markup = json.loads(previews[0][1]["reply_markup"])
        callbacks = [
            button["callback_data"]
            for row in markup["inline_keyboard"]
            for button in row
        ]
        self.assertIn(f"approve:{draft_id}", callbacks)
        self.assertNotIn(f"approve_ig:{draft_id}", callbacks)

    def test_album_preview_sends_buttons_in_follow_up_message(self):
        draft_id = self._draft_with_media_ask()
        self._send_photo(draft_id, 1)
        self._send_photo(draft_id, 2)
        follow_ups = [
            message
            for message in self.api.sent_messages
            if message.get("reply_markup")
            and "photos ready above" in str(message.get("text") or "")
        ]
        self.assertTrue(follow_ups)
        callbacks = [
            button["callback_data"]
            for row in follow_ups[-1]["reply_markup"]["inline_keyboard"]
            for button in row
        ]
        self.assertIn(f"approve:{draft_id}", callbacks)
        self.assertIn(f"reject:{draft_id}", callbacks)
        self.assertIn(f"media:done:{draft_id}", callbacks)
        self.assertNotIn(f"approve_ig:{draft_id}", callbacks)

    def test_instagram_approval_buttons_require_configuration(self):
        draft_id = self._draft_with_media_ask()
        self._send_photo(draft_id, 1)
        self.bot.handle_callback(
            {
                "id": "qdone",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"media:done:{draft_id}",
            }
        )
        self.api.calls.clear()
        self.bot.handle_callback(
            {
                "id": "qig",
                "from": {"id": 11},
                "message": {"chat": {"id": 11}, "message_id": 101},
                "data": f"approve_ig:{draft_id}",
            }
        )
        answers = [payload for method, payload in self.api.calls if method == "answerCallbackQuery"]
        self.assertTrue(any("Instagram is not configured" in a.get("text", "") for a in answers))
        self.assertIsNotNone(self.bot.state.get_draft(draft_id))
