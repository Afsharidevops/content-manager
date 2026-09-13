"""Tests for the automatic messenger channels (Bale and Eitaa)."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from content_bot import channels
from content_bot.config import BotSettings
from content_bot.http import HttpError


def settings(**overrides) -> BotSettings:
    base = BotSettings(
        bot_token="123:TESTTOKENABCDEFGHIJKLMN",
        data_dir=tempfile.gettempdir(),
    )
    return replace(base, **overrides)


class ChannelRegistryTests(unittest.TestCase):
    def test_only_configured_channels_are_registered(self):
        self.assertEqual(channels.build_channels(settings()), {})
        bale = channels.build_channels(
            settings(bale_token="t", bale_chat_id="@bale")
        )
        self.assertEqual(set(bale), {"bale"})
        both = channels.build_channels(
            settings(
                bale_token="t",
                bale_chat_id="@bale",
                eitaa_token="e",
                eitaa_chat_id="@eitaa",
            )
        )
        self.assertEqual(set(both), {"bale", "eitaa"})

    def test_bale_uses_the_telegram_shaped_endpoint(self):
        channel = channels.build_channels(
            settings(bale_token="tok", bale_chat_id="@bale")
        )["bale"]
        self.assertEqual(channel.api.root, "https://tapi.bale.ai/bottok")


class EitaaChannelTests(unittest.TestCase):
    def build(self) -> channels.EitaaChannel:
        return channels.EitaaChannel(
            "eitaa", "Eitaa", "etok", "@channel", "https://eitaayar.ir/api"
        )

    def test_send_text_uses_json_first_and_strips_html(self):
        channel = self.build()
        captured = {}

        def fake_json(url, *, payload=None, headers=None, timeout=30):
            captured["url"] = url
            captured["payload"] = payload
            return {"ok": True}

        with mock.patch.object(channels, "request_json", side_effect=fake_json):
            channel.send_text("<b>Title</b>\n\nBody &amp; more")
        self.assertEqual(captured["url"], "https://eitaayar.ir/api/etok/sendMessage")
        self.assertEqual(captured["payload"]["chat_id"], "@channel")
        self.assertEqual(captured["payload"]["text"], "Title\n\nBody & more")

    def test_send_text_falls_back_to_form_data(self):
        channel = self.build()
        seen = {}

        def fake_json(url, *, payload=None, headers=None, timeout=30):
            raise HttpError(415, b'{"ok":false,"description":"unsupported media type"}')

        def fake_bytes(url, *, raw_body=None, content_type=None, timeout=30, **kwargs):
            seen["body"] = raw_body
            seen["content_type"] = content_type
            return 200, json.dumps({"ok": True}).encode()

        with mock.patch.object(channels, "request_json", side_effect=fake_json), \
             mock.patch.object(channels, "request_bytes", side_effect=fake_bytes):
            channel.send_text("hello")
        self.assertEqual(seen["content_type"], "application/x-www-form-urlencoded")
        self.assertIn(b"chat_id=%40channel", seen["body"])
        self.assertIn(b"text=hello", seen["body"])

    def test_send_text_reports_the_gateway_error(self):
        channel = self.build()

        def fake_json(url, *, payload=None, headers=None, timeout=30):
            return {"ok": False, "description": "chat not found"}

        def fake_bytes(url, *, raw_body=None, content_type=None, timeout=30, **kwargs):
            return 200, json.dumps({"ok": False, "description": "chat not found"}).encode()

        with mock.patch.object(channels, "request_json", side_effect=fake_json), \
             mock.patch.object(channels, "request_bytes", side_effect=fake_bytes):
            with self.assertRaises(channels.ChannelError) as caught:
                channel.send_text("hello")
        self.assertIn("chat not found", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
