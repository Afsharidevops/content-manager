"""Tests for the console action queue drained by the Content Bot."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from content_bot import bot as bot_mod
from content_bot import panel_actions as panel_mod
from content_bot.bot import ContentBot
from content_bot.config import BotSettings
from content_bot.telegram import TelegramApi

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "content" / "config"


class FakeApi(TelegramApi):
    def __init__(self):
        super().__init__("123:TESTTOKENABCDEFGHIJKLMN")
        self.calls = []
        self.sent_messages = []

    def _transport(self, url, payload, *, timeout=35):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, payload))
        if method == "sendMessage":
            self.sent_messages.append(payload)
            return {"ok": True, "result": {"message_id": 100 + len(self.sent_messages)}}
        return {"ok": True, "result": True}


class FakeWriter:
    def generate_post(self, item, guidance=""):
        return {
            "title": "Generated title",
            "body": "Generated body text.",
            "source_url": "https://example.com/layers",
        }


class FakeMedia:
    def __init__(self):
        self.submits = []

    def submit(self, driver, prompt, params=None):
        self.submits.append((driver, prompt, params or {}))
        return f"job-{len(self.submits)}"


class PanelActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = BotSettings(
            bot_token="123:TESTTOKENABCDEFGHIJKLMN",
            telegram_channel="@channel",
            telegram_users=frozenset({11}),
            policy_dir=str(POLICY_DIR),
            data_dir=self.tmp.name,
            scheduler_enabled=False,
        )
        self.api = FakeApi()
        self.media = FakeMedia()
        self.bot = ContentBot(
            self.settings,
            api=self.api,
            writer=FakeWriter(),
            media=self.media,
            fetch_page=lambda url: "",
            fetch_feed=lambda url: b"",
            now_fn=lambda: datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
        )

    # ------------------------------------------------------------- helpers

    def add_draft(self, status="media_ask", **extra):
        draft_id = "draft-1"
        record = {
            "id": draft_id,
            "kind": "on_demand",
            "chat_id": 11,
            "message_id": 101,
            "ask_message_id": 102,
            "title": "Generated title",
            "body": "Generated body text.",
            "source_url": "https://example.com/layers",
            "content_hash": "hash-1",
            "category": "platform",
            "status": status,
            "media": None,
            "created_at": "2026-09-10T12:00:00+00:00",
        }
        record.update(extra)
        self.bot.state.add_draft(draft_id, record)
        return draft_id

    def queue(self, action, draft_id="draft-1"):
        path = panel_mod.requests_path(self.settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "id": "req1",
            "draft_id": draft_id,
            "action": action,
            "requested_at": "2026-09-10T12:00:00+00:00",
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        return row

    def results(self):
        payload = json.loads(
            panel_mod.results_path(self.settings).read_text(encoding="utf-8")
        )
        return payload["results"]

    def notices(self):
        return [payload["text"] for payload in self.api.sent_messages]

    # --------------------------------------------------------------- tests

    def test_text_only_action_answers_the_media_question(self):
        draft_id = self.add_draft("media_ask")
        self.queue("text-only")
        self.assertEqual(panel_mod.drain(self.bot), 1)
        self.assertEqual(self.bot.state.get_draft(draft_id)["status"], "text_only")
        result = self.results()[-1]
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "text-only")
        self.assertTrue(any(text.startswith("Panel:") for text in self.notices()))
        path = panel_mod.requests_path(self.settings)
        self.assertTrue(not path.exists() or path.stat().st_size == 0)

    def test_image_action_starts_a_media_job(self):
        draft_id = self.add_draft("media_ask")
        self.queue("image")
        panel_mod.drain(self.bot)
        record = self.bot.state.get_draft(draft_id)
        self.assertEqual(record["status"], "media_running")
        self.assertEqual(record["media"]["driver"], self.settings.image_driver)
        self.assertEqual(len(self.media.submits), 1)

    def test_image_action_reports_a_missing_media_studio(self):
        self.bot.media = None
        draft_id = self.add_draft("media_ask")
        self.queue("image")
        panel_mod.drain(self.bot)
        result = self.results()[-1]
        self.assertFalse(result["ok"])
        self.assertEqual(self.bot.state.get_draft(draft_id)["status"], "media_ask")
        self.assertIn("Media Studio", result["message"])

    def test_publish_action_uses_the_approval_path(self):
        draft_id = self.add_draft("text_only", media={"kind": "none"})
        self.queue("publish")
        panel_mod.drain(self.bot)
        self.assertIsNone(self.bot.state.get_draft(draft_id))
        published = [payload for method, payload in self.api.calls if method == "sendMessage"]
        self.assertTrue(any(payload.get("chat_id") == "@channel" for payload in published))
        result = self.results()[-1]
        self.assertTrue(result["ok"])
        self.assertIn("Published to Telegram", result["message"])

    def test_publish_action_is_skipped_while_media_is_running(self):
        draft_id = self.add_draft("media_running")
        self.queue("publish")
        panel_mod.drain(self.bot)
        result = self.results()[-1]
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "skipped")
        self.assertIsNotNone(self.bot.state.get_draft(draft_id))

    def test_discard_action_removes_the_draft_and_its_messages(self):
        self.add_draft("awaiting_media", preview_message_id=103)
        self.queue("discard")
        panel_mod.drain(self.bot)
        self.assertIsNone(self.bot.state.get_draft("draft-1"))
        deleted = [payload for method, payload in self.api.calls if method == "deleteMessage"]
        self.assertEqual(
            sorted(payload["message_id"] for payload in deleted),
            [102, 103],
        )
        self.assertTrue(self.results()[-1]["ok"])

    def test_unknown_draft_is_reported_without_failing(self):
        self.queue("discard", draft_id="missing-1")
        panel_mod.drain(self.bot)
        result = self.results()[-1]
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["ok"])

    def test_queue_is_consumed_even_when_a_request_is_unknown(self):
        self.queue("discard", draft_id="missing-1")
        panel_mod.drain(self.bot)
        self.assertEqual(panel_mod.drain(self.bot), 0)
        self.assertEqual(len(self.results()), 1)

    def test_malformed_queue_lines_are_ignored(self):
        path = panel_mod.requests_path(self.settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json\n\n", encoding="utf-8")
        self.assertEqual(panel_mod.drain(self.bot), 0)

    def test_drain_is_a_noop_without_a_queue_file(self):
        self.assertEqual(panel_mod.drain(self.bot), 0)
        self.assertFalse(panel_mod.results_path(self.settings).exists())

    def test_maintenance_runs_every_step_even_when_one_fails(self):
        draft_id = self.add_draft("media_ask")

        def boom():
            raise RuntimeError("step failed")

        self.bot.maybe_run_daily = boom
        self.bot.maybe_poll_media_jobs = boom
        self.bot.maybe_refresh_instagram_token = boom
        self.queue("text-only")
        self.bot._maintenance()
        self.assertEqual(self.bot.state.get_draft(draft_id)["status"], "text_only")

    def test_local_maintenance_still_runs_after_a_telegram_failure(self):
        calls = []
        self.bot.poll_once = lambda: (_ for _ in ()).throw(ConnectionError("reset"))
        self.bot.maybe_run_daily = lambda: calls.append("daily")
        self.bot.maybe_run_routines = lambda: calls.append("routines")
        self.bot.maybe_poll_media_jobs = lambda: calls.append("media")
        self.bot.maybe_refresh_instagram_token = lambda: calls.append("token")

        def stop(bot):
            calls.append("drain")
            raise KeyboardInterrupt

        with mock.patch.object(bot_mod.panel_actions_mod, "drain", stop):
            with mock.patch.object(bot_mod.time, "sleep", lambda seconds: None):
                with self.assertRaises(KeyboardInterrupt):
                    self.bot.run()
        self.assertEqual(calls[-5:], ["daily", "routines", "media", "token", "drain"])

    def test_only_the_newest_results_are_kept(self):
        panel_mod.record_results(
            self.settings,
            [
                {"id": str(index), "draft_id": "d", "action": "discard", "ok": True}
                for index in range(panel_mod.RESULT_LIMIT + 10)
            ],
        )
        stored = json.loads(
            panel_mod.results_path(self.settings).read_text(encoding="utf-8")
        )
        self.assertEqual(len(stored["results"]), panel_mod.RESULT_LIMIT)
        self.assertEqual(stored["results"][-1]["id"], str(panel_mod.RESULT_LIMIT + 9))


class SingletonGuard(unittest.TestCase):
    """The queue paths must stay identical to the panel side."""

    def test_queue_file_names_match_the_panel_contract(self):
        self.assertEqual(panel_mod.REQUEST_FILE, "panel-actions.requests.jsonl")
        self.assertEqual(panel_mod.RESULT_FILE, "panel-actions.results.json")
        self.assertEqual(panel_mod.LOCK_FILE, "panel-actions.lock")


if __name__ == "__main__":
    unittest.main()
