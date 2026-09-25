"""Tests for the automatic messenger channels (Bale and Eitaa)."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from unittest import mock

from content_bot import aparat, channels
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


class AparatChannelTests(unittest.TestCase):
    def test_aparat_joins_the_registry_when_a_session_is_stored(self):
        registered = channels.build_channels(settings(aparat_token="jwt-value"))
        self.assertIn("aparat", registered)
        self.assertTrue(registered["aparat"].configured)
        self.assertEqual({}, channels.build_channels(settings()))

    def test_aparat_accepts_a_cookie_session(self):
        registered = channels.build_channels(settings(aparat_cookie="AuthV1=abc"))
        self.assertIn("aparat", registered)

    def test_text_and_photo_posts_are_refused_with_a_reason(self):
        channel = channels.build_channels(settings(aparat_token="jwt"))["aparat"]
        for call in (
            lambda: channel.send_text("hello"),
            lambda: channel.send_photo("a.jpg", b"x", "caption"),
            lambda: channel.send_document("a.pdf", b"x", "caption"),
        ):
            with self.assertRaises(channels.ChannelError) as caught:
                call()
            self.assertIn("videos only", str(caught.exception))

    def test_a_video_publish_sends_the_metadata_and_keeps_the_link(self):
        channel = channels.build_channels(
            settings(aparat_token="jwt", aparat_tags=("locallab",))
        )["aparat"]
        captured = {}

        def fake_publish(data, *, filename, title, description, tags, **kwargs):
            captured.update(
                {
                    "data": data,
                    "filename": filename,
                    "title": title,
                    "description": description,
                    "tags": tags,
                }
            )
            return aparat.UploadResult(upload_id="1", video="v", hash="vid42")

        with mock.patch.object(channel.client, "publish", side_effect=fake_publish):
            channel.send_video(
                "clip.mp4",
                b"video-bytes",
                "Layer caching\n\nThe body of the post.",
                meta={"title": "Layer caching", "tags": ["docker", "linux"]},
            )
        self.assertEqual(b"video-bytes", captured["data"])
        self.assertEqual("clip.mp4", captured["filename"])
        self.assertEqual("Layer caching", captured["title"])
        self.assertIn("The body of the post.", captured["description"])
        self.assertEqual(["docker", "linux", "locallab"], captured["tags"])
        self.assertEqual("https://www.aparat.com/v/vid42", channel.last_remote_id)

    def test_the_tags_are_padded_to_the_aparats_minimum(self):
        channel = channels.build_channels(settings(aparat_token="jwt"))["aparat"]
        captured = {}

        def fake_publish(data, *, filename, title, description, tags, **kwargs):
            captured["tags"] = tags
            return aparat.UploadResult(upload_id="1", video="v", hash="vid42")

        with mock.patch.object(channel.client, "publish", side_effect=fake_publish):
            channel.send_video("clip.mp4", b"bytes", "Title", meta={"tags": []})
        self.assertEqual(list(aparat.DEFAULT_TAGS), captured["tags"])

    def test_the_duration_and_thumbnail_of_the_draft_reach_the_client(self):
        channel = channels.build_channels(settings(aparat_token="jwt"))["aparat"]
        captured = {}

        def fake_publish(data, *, filename, title, description, tags, **kwargs):
            captured.update(kwargs)
            return aparat.UploadResult(upload_id="1", video="v", hash="vid42")

        with mock.patch.object(channel.client, "publish", side_effect=fake_publish):
            channel.send_video(
                "clip.mp4",
                b"bytes",
                "Title",
                meta={
                    "duration": "12",
                    "thumbnail": b"poster-bytes",
                    "thumbnail_filename": "poster.jpg",
                },
            )
        self.assertEqual("12", captured["duration"])
        self.assertEqual(b"poster-bytes", captured["thumbnail"])
        self.assertEqual("poster.jpg", captured["thumbnail_filename"])

    def test_a_failed_upload_becomes_a_channel_error(self):
        channel = channels.build_channels(settings(aparat_token="jwt"))["aparat"]
        with mock.patch.object(
            channel.client, "publish", side_effect=aparat.AparatError("session expired")
        ):
            with self.assertRaises(channels.ChannelError) as caught:
                channel.send_video("clip.mp4", b"bytes", "Title")
        self.assertIn("session expired", str(caught.exception))


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

    def test_send_file_rejects_large_uploads_before_gateway_timeout(self):
        channel = self.build()
        with mock.patch.object(channels, "request_multipart") as request:
            with self.assertRaises(channels.ChannelError) as caught:
                channel.send_video("clip.mp4", b"x" * 6_500_001, "Caption")
        self.assertIn("exceeds the 6500000-byte upload limit", str(caught.exception))
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
