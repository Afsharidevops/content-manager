"""Tests for long-lived Instagram token maintenance."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from content_bot import instagram_token as token_mod
from content_bot.bot import ContentBot
from content_bot.config import BotSettings
from content_bot.http import HttpError
from content_bot.telegram import TelegramApi

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
POLICY_DIR = Path(__file__).resolve().parents[2] / "content" / "config"


class FakeJson:
    """Stands in for ``http.request_json`` and records the requested URL."""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.urls = []

    def __call__(self, url, *, payload=None, headers=None, timeout=30):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.payload


class FakeApi(TelegramApi):
    def __init__(self):
        super().__init__("123:TESTTOKENABCDEFGHIJKLMN")
        self.sent_messages = []

    def _transport(self, url, payload, *, timeout=35):
        if url.rsplit("/", 1)[-1] == "sendMessage":
            self.sent_messages.append(payload)
            return {"ok": True, "result": {"message_id": 100 + len(self.sent_messages)}}
        return {"ok": True, "result": True}


def settings_for(**overrides) -> BotSettings:
    values = {
        "bot_token": "123:TESTTOKENABCDEFGHIJKLMN",
        "instagram_business_id": "17841426952001533",
        "instagram_access_token": "env-token",
        "instagram_api_base": "https://graph.instagram.com",
        "instagram_api_version": "v26.0",
        "telegram_users": frozenset({11}),
        "policy_dir": str(POLICY_DIR),
    }
    values.update(overrides)
    return BotSettings(**values)


class RefreshUrlTests(unittest.TestCase):
    def test_instagram_login_uses_the_refresh_endpoint(self):
        url, source = token_mod.refresh_url(settings_for(), "abc123")
        self.assertEqual(source, "instagram-login")
        self.assertIn("https://graph.instagram.com/refresh_access_token", url)
        self.assertIn("grant_type=ig_refresh_token", url)
        self.assertIn("access_token=abc123", url)

    def test_facebook_login_uses_the_exchange_endpoint(self):
        settings = settings_for(
            instagram_api_base="https://graph.facebook.com",
            instagram_app_id="1234567890",
            instagram_app_secret="secret",
        )
        url, source = token_mod.refresh_url(settings, "abc123")
        self.assertEqual(source, "facebook-login")
        self.assertIn("https://graph.facebook.com/v26.0/oauth/access_token", url)
        self.assertIn("grant_type=fb_exchange_token", url)
        self.assertIn("client_secret=secret", url)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = self.tmp.name

    def test_refresh_stores_the_extended_token_with_owner_only_permissions(self):
        request = FakeJson({"access_token": "fresh", "expires_in": 5_184_000})
        record = token_mod.refresh(settings_for(), "env-token", request=request, now=NOW)
        self.assertEqual(record["access_token"], "fresh")
        self.assertEqual(record["source"], "instagram-login")
        self.assertEqual(record["expires_at"], "2026-11-09T12:00:00+00:00")
        self.assertIn("refresh_access_token", request.urls[0])
        token_mod.save(self.data_dir, record)
        path = Path(self.data_dir) / token_mod.TOKEN_FILE
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["access_token"], "fresh")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(token_mod.load(self.data_dir)["expires_in"], 5_184_000)

    def test_refresh_reports_the_api_error_message(self):
        body = json.dumps({"error": {"message": "Session has expired"}}).encode()
        request = FakeJson(error=HttpError(400, body))
        with self.assertRaises(token_mod.InstagramTokenError) as caught:
            token_mod.refresh(settings_for(), "env-token", request=request, now=NOW)
        self.assertIn("Session has expired", str(caught.exception))

    def test_refresh_reports_connection_problems(self):
        request = FakeJson(error=ConnectionError("dns"))
        with self.assertRaises(token_mod.InstagramTokenError):
            token_mod.refresh(settings_for(), "env-token", request=request, now=NOW)

    def test_refresh_needs_a_token(self):
        with self.assertRaises(token_mod.InstagramTokenError):
            token_mod.refresh(settings_for(), "  ", now=NOW)

    def test_refresh_rejects_a_payload_without_a_token(self):
        request = FakeJson({"error_message": "no token here"})
        with self.assertRaises(token_mod.InstagramTokenError) as caught:
            token_mod.refresh(settings_for(), "env-token", request=request, now=NOW)
        self.assertIn("no token here", str(caught.exception))

    def test_expiry_helpers(self):
        record = {"access_token": "x", "expires_at": "2026-09-20T12:00:00+00:00"}
        self.assertEqual(token_mod.days_left(record, now=NOW), 10)
        self.assertFalse(token_mod.is_expired(record, now=NOW))
        self.assertTrue(token_mod.is_expired(record, now=NOW + timedelta(days=10)))

    def test_refresh_is_due_for_missing_or_stale_records(self):
        self.assertTrue(token_mod.refresh_due({}, now=NOW))
        self.assertTrue(token_mod.refresh_due({"access_token": "x"}, now=NOW))
        fresh = {
            "access_token": "x",
            "refreshed_at": "2026-09-09T12:00:00+00:00",
        }
        self.assertFalse(token_mod.refresh_due(fresh, now=NOW))
        stale = {
            "access_token": "x",
            "refreshed_at": "2026-09-01T12:00:00+00:00",
        }
        self.assertTrue(token_mod.refresh_due(stale, now=NOW))

    def test_failure_record_keeps_the_working_token(self):
        stored = {"access_token": "keep-me", "refreshed_at": "2026-09-01T00:00:00+00:00"}
        token_mod.save(self.data_dir, stored)
        updated = token_mod.record_failure(self.data_dir, stored, "HTTP 400", now=NOW)
        self.assertEqual(updated["access_token"], "keep-me")
        self.assertEqual(updated["last_error"], "HTTP 400")
        self.assertEqual(token_mod.load(self.data_dir)["last_error"], "HTTP 400")

    def test_repeated_failures_are_not_notified_twice(self):
        self.assertTrue(token_mod.should_notify({}, "HTTP 400", now=NOW))
        record = {"notified_error": "HTTP 400", "notified_at": NOW.isoformat()}
        self.assertFalse(token_mod.should_notify(record, "HTTP 400", now=NOW))
        self.assertTrue(token_mod.should_notify(record, "HTTP 401", now=NOW))
        self.assertTrue(
            token_mod.should_notify(record, "HTTP 400", now=NOW + timedelta(hours=7))
        )

    def test_expiry_note_mentions_the_remaining_days(self):
        note = token_mod.expiry_note(
            {"expires_at": "2026-10-10T12:00:00+00:00"}, now=NOW
        )
        self.assertIn("30 day(s) left", note)
        soon = token_mod.expiry_note({"expires_at": "2026-09-13T12:00:00+00:00"}, now=NOW)
        self.assertIn("Renew it soon", soon)
        self.assertIn("no refresh record", token_mod.expiry_note({}, now=NOW))

    def test_take_request_consumes_the_panel_trigger(self):
        self.assertFalse(token_mod.take_request(self.data_dir))
        path = token_mod.request_path(self.data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("2026-09-10T12:00:00+00:00\n", encoding="utf-8")
        self.assertTrue(token_mod.take_request(self.data_dir))
        self.assertFalse(token_mod.take_request(self.data_dir))


class BotMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = FakeApi()

    def make_bot(self, **overrides):
        settings = settings_for(data_dir=self.tmp.name, **overrides)
        return ContentBot(
            settings,
            api=self.api,
            writer=None,
            now_fn=lambda: NOW,
        )

    def test_successful_refresh_replaces_the_token_and_notifies_once(self):
        bot = self.make_bot()
        request = FakeJson({"access_token": "fresh", "expires_in": 5_184_000})
        with mock.patch.object(token_mod, "request_json", request):
            self.assertTrue(bot.maybe_refresh_instagram_token())
        self.assertEqual(bot._instagram_token(), "fresh")
        self.assertEqual(len(self.api.sent_messages), 1)
        self.assertIn("refreshed automatically", self.api.sent_messages[0]["text"])
        self.assertIn("60 day(s) left", self.api.sent_messages[0]["text"])
        stored = token_mod.load(self.tmp.name)
        self.assertEqual(stored["access_token"], "fresh")

    def test_failed_refresh_keeps_the_env_token_and_warns(self):
        bot = self.make_bot()
        body = json.dumps({"error": {"message": "Session has expired"}}).encode()
        request = FakeJson(error=HttpError(400, body))
        with mock.patch.object(token_mod, "request_json", request):
            self.assertFalse(bot.maybe_refresh_instagram_token())
        self.assertEqual(bot._instagram_token(), "env-token")
        self.assertEqual(len(self.api.sent_messages), 1)
        self.assertIn("refresh failed", self.api.sent_messages[0]["text"])
        self.assertEqual(token_mod.load(self.tmp.name)["last_error"], "HTTP 400: Session has expired")

    def test_a_fresh_record_skips_the_http_call(self):
        bot = self.make_bot()
        token_mod.save(
            self.tmp.name,
            {
                "access_token": "stored",
                "refreshed_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(days=60)).isoformat(),
                "source": "instagram-login",
            },
        )
        bot._instagram_record_cache = None
        request = FakeJson({"access_token": "should-not-be-used"})
        with mock.patch.object(token_mod, "request_json", request):
            self.assertFalse(bot.maybe_refresh_instagram_token())
        self.assertEqual(request.urls, [])
        self.assertEqual(bot._instagram_token(), "stored")

    def test_panel_request_forces_one_refresh(self):
        bot = self.make_bot()
        token_mod.save(
            self.tmp.name,
            {
                "access_token": "stored",
                "refreshed_at": NOW.isoformat(),
                "expires_at": (NOW + timedelta(days=60)).isoformat(),
            },
        )
        bot._instagram_record_cache = None
        token_mod.request_path(self.tmp.name).write_text("now\n", encoding="utf-8")
        request = FakeJson({"access_token": "forced", "expires_in": 5_184_000})
        with mock.patch.object(token_mod, "request_json", request):
            self.assertTrue(bot.maybe_refresh_instagram_token())
        self.assertEqual(bot._instagram_token(), "forced")

    def test_status_text_reports_the_stored_expiry(self):
        bot = self.make_bot()
        token_mod.save(
            self.tmp.name,
            {
                "access_token": "stored",
                "refreshed_at": "2026-09-01T12:00:00+00:00",
                "expires_at": "2026-10-10T12:00:00+00:00",
                "source": "instagram-login",
            },
        )
        bot._instagram_record_cache = None
        text = bot.instagram_status_text()
        self.assertIn("30 day(s) left", text)
        self.assertIn("instagram-login", text)


if __name__ == "__main__":
    unittest.main()
