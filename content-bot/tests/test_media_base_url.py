"""Tests for discovering the public media base URL Instagram downloads from."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from content_bot import bot as bot_mod
from content_bot import instagram as instagram_mod
from content_bot.bot import (
    ContentBot,
    MEDIA_BASE_URL_FILE,
    TUNNEL_LOG_NAME,
    _base_url_from_file,
    _base_url_from_tunnel_log,
    _clean_base_url,
    _stable_base_url,
)
from content_bot.config import BotSettings

POLICY_DIR = Path(__file__).resolve().parents[2] / "content" / "config"


def settings_for(**overrides) -> BotSettings:
    values = {
        "bot_token": "123:TESTTOKENABCDEFGHIJKLMN",
        "instagram_business_id": "17841426952001533",
        "instagram_access_token": "env-token",
        "telegram_users": frozenset({11}),
        "policy_dir": str(POLICY_DIR),
    }
    values.update(overrides)
    return BotSettings(**values)


class CleanBaseUrlTests(unittest.TestCase):
    def test_accepts_one_https_url_and_removes_trailing_slashes(self):
        self.assertEqual(_clean_base_url("  https://a.example/x/  "), "https://a.example/x")

    def test_rejects_empty_and_non_https_values(self):
        self.assertEqual(_clean_base_url(""), "")
        self.assertEqual(_clean_base_url(None), "")
        self.assertEqual(_clean_base_url("http://a.example/x"), "")
        self.assertEqual(_clean_base_url("https://"), "")

    def test_stable_rejects_quick_tunnel_hosts(self):
        self.assertEqual(_stable_base_url("https://x.trycloudflare.com"), "")
        self.assertEqual(
            _stable_base_url("https://media.locallab.ir/media"),
            "https://media.locallab.ir/media",
        )


class BaseUrlFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = self.tmp.name

    def write(self, text: str) -> None:
        (Path(self.data_dir) / MEDIA_BASE_URL_FILE).write_text(text, encoding="utf-8")

    def test_missing_file_is_empty(self):
        self.assertEqual(_base_url_from_file(self.data_dir), "")

    def test_comments_and_blank_lines_are_skipped(self):
        self.write("# pinned by the deployment\n\nhttps://media.locallab.ir\n")
        self.assertEqual(_base_url_from_file(self.data_dir), "https://media.locallab.ir")

    def test_trailing_comment_on_the_same_line_is_ignored(self):
        self.write("https://media.locallab.ir  # stable\n")
        self.assertEqual(_base_url_from_file(self.data_dir), "https://media.locallab.ir")

    def test_an_unusable_first_line_falls_through_to_the_next(self):
        self.write("not-a-url\nhttps://media.example.com/base\n")
        self.assertEqual(_base_url_from_file(self.data_dir), "https://media.example.com/base")


class TunnelLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = self.tmp.name
        self.log_path = Path(self.data_dir) / "tunnel" / TUNNEL_LOG_NAME

    def write(self, text: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(text, encoding="utf-8")

    def test_missing_log_is_empty(self):
        self.assertEqual(_base_url_from_tunnel_log(self.data_dir), "")

    def test_log_without_a_hostname_is_empty(self):
        self.write("INF Using default configuration\n")
        self.assertEqual(_base_url_from_tunnel_log(self.data_dir), "")

    def test_the_most_recent_hostname_wins(self):
        self.write(
            "INF Your quick Tunnel has been created! Visit it at "
            "https://old-name.trycloudflare.com\n"
            "INF Registered tunnel connection\n"
            "INF Your quick Tunnel has been created! Visit it at "
            "https://new-name.trycloudflare.com\n"
        )
        self.assertEqual(
            _base_url_from_tunnel_log(self.data_dir),
            "https://new-name.trycloudflare.com",
        )

    def test_only_the_recent_tail_of_a_large_log_is_read(self):
        # A long-gone hostname must not come back from an oversized history.
        self.write(
            "https://ancient-name.trycloudflare.com\n"
            + ("INF Registered tunnel connection\n" * 4000)
            + "https://fresh-name.trycloudflare.com\n"
        )
        self.assertEqual(
            _base_url_from_tunnel_log(self.data_dir),
            "https://fresh-name.trycloudflare.com",
        )


class BotResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = self.tmp.name

    def make_bot(self, **overrides) -> ContentBot:
        settings = settings_for(data_dir=self.data_dir, **overrides)
        return ContentBot(settings, api=mock.Mock(), writer=None)

    def write_pin(self, text: str) -> None:
        (Path(self.data_dir) / MEDIA_BASE_URL_FILE).write_text(text, encoding="utf-8")

    def write_tunnel(self, hostname: str) -> None:
        path = Path(self.data_dir) / "tunnel" / TUNNEL_LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"INF Visit it at https://{hostname}.trycloudflare.com\n", encoding="utf-8")

    def test_nothing_configured_resolves_to_empty(self):
        self.assertEqual(self.make_bot()._instagram_media_base_url(), "")

    def test_a_pinned_file_wins_over_the_environment(self):
        self.write_pin("https://media.locallab.ir\n")
        bot = self.make_bot(instagram_media_public_base_url="https://other.example.com")
        self.assertEqual(bot._instagram_media_base_url(), "https://media.locallab.ir")

    def test_a_stable_environment_url_wins_over_the_tunnel_log(self):
        self.write_tunnel("quick-name")
        bot = self.make_bot(instagram_media_public_base_url="https://media.locallab.ir")
        self.assertEqual(bot._instagram_media_base_url(), "https://media.locallab.ir")

    def test_a_stale_quick_tunnel_in_the_environment_falls_through_to_the_log(self):
        self.write_tunnel("live-name")
        bot = self.make_bot(
            instagram_media_public_base_url="https://control-old.trycloudflare.com"
        )
        self.assertEqual(bot._instagram_media_base_url(), "https://live-name.trycloudflare.com")

    def test_the_publisher_is_rebuilt_when_the_base_url_changes(self):
        self.write_tunnel("first-name")
        bot = self.make_bot()
        created = []

        def factory(*args, **kwargs):
            publisher = mock.Mock()
            publisher.media_base_url = kwargs.get("media_base_url", "")
            created.append(publisher)
            return publisher

        with mock.patch.object(instagram_mod, "InstagramPublisher", side_effect=factory):
            first = bot._instagram_publisher()
            self.assertIs(bot._instagram_publisher(), first)
            self.write_tunnel("second-name")
            second = bot._instagram_publisher()

        self.assertIsNot(first, second)
        self.assertEqual(second.media_base_url, "https://second-name.trycloudflare.com")

    def test_status_text_reports_the_resolved_media_host(self):
        self.write_pin("https://media.locallab.ir/media\n")
        text = self.make_bot().instagram_status_text()
        self.assertIn("Media base URL: https://media.locallab.ir/media", text)

    def test_status_text_says_when_no_media_host_exists(self):
        text = self.make_bot().instagram_status_text()
        self.assertIn("not set", text)


if __name__ == "__main__":
    unittest.main()
