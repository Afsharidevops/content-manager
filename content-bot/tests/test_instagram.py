"""Unit tests for the official Instagram Graph API publisher."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from content_bot import instagram as instagram_mod
from content_bot.instagram import InstagramError, InstagramPublisher, build_caption


def _graph_response(payload: dict, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload).encode("utf-8")


class FakeGraphTransport:
    """Stand-in for request_bytes that models one Instagram publish run."""

    def __init__(self):
        self.calls = []
        self.containers = 0
        self.poll_rounds = 0
        self.fail_container = ""
        self.container_error = ""

    def __call__(self, url, *, method=None, raw_body=None, content_type=None, timeout=None):
        self.calls.append(
            {
                "method": method or ("GET" if raw_body is None else "POST"),
                "url": url,
                "body": (raw_body or b"").decode("utf-8"),
            }
        )
        if method == "GET":
            if "status_code" in url:
                self.poll_rounds += 1
                if self.fail_container and self.fail_container in url:
                    return _graph_response(
                        {
                            "status_code": "ERROR",
                            "status": "error",
                            "error_message": self.container_error or "broken media",
                        }
                    )
                if self.poll_rounds == 1:
                    return _graph_response(
                        {"status_code": "IN_PROGRESS", "status": "processing"}
                    )
                return _graph_response({"status_code": "FINISHED", "status": "done"})
            return _graph_response({"id": "ig-1", "username": "locallab"})
        if "media_publish" in url:
            return _graph_response({"id": "published-1"})
        self.containers += 1
        return _graph_response({"id": f"container-{self.containers}"})


class CaptionTests(unittest.TestCase):
    def test_caption_keeps_title_body_and_appends_source_once(self):
        caption = build_caption(
            "**A bold title**",
            "First paragraph.\n\nSecond paragraph.",
            "https://example.com/post",
        )
        self.assertTrue(caption.startswith("A bold title"))
        self.assertIn("First paragraph.", caption)
        self.assertIn("Second paragraph.", caption)
        self.assertEqual(caption.count("https://example.com/post"), 1)
        self.assertTrue(caption.endswith("https://example.com/post"))

    def test_persian_caption_starts_with_rtl_mark(self):
        title = "ساخت اپلیکیشن با Hercules فقط با توضیح نیاز کسب و کار"
        caption = build_caption(
            title,
            "Hercules یک ابزار ساخت اپلیکیشن است.",
            "https://hercules.app",
        )
        self.assertTrue(caption.startswith("\u200f"))
        self.assertEqual(caption.splitlines()[0], f"\u200f{title}")
        self.assertIn("\n\n\u200fHercules یک ابزار", caption)
        self.assertTrue(caption.endswith("https://hercules.app"))

    def test_english_caption_stays_without_rtl_marks(self):
        caption = build_caption("Plain English title", "Plain body.", "")
        self.assertFalse(caption.startswith("\u200f"))
        self.assertNotIn("\u200f", caption)

    def test_caption_does_not_duplicate_source_already_in_body(self):
        caption = build_caption(
            "Title",
            "Read the full post at https://example.com/post",
            "https://example.com/post",
        )
        self.assertEqual(caption.count("https://example.com/post"), 1)

    def test_caption_strips_markup_and_limits_length(self):
        body = "\n\n".join(
            f"Paragraph {index} with some **bold** words." for index in range(300)
        )
        caption = build_caption("Title `code`", body, "https://example.com/long")
        self.assertLessEqual(len(caption), instagram_mod.CAPTION_MAX)
        self.assertNotIn("**", caption)
        self.assertNotIn("`", caption)
        self.assertTrue(caption.endswith("https://example.com/long"))


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp/instagram-publisher-test")
        self.media_dir = self.tmp / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.image = self.media_dir / "shot.png"
        self.image.write_bytes(b"\x89PNG")
        self.video = self.media_dir / "clip.mp4"
        self.video.write_bytes(b"mp4-bytes")
        self.addCleanup(self.image.unlink, missing_ok=True)
        self.addCleanup(self.video.unlink, missing_ok=True)
        self.publisher = InstagramPublisher(
            "business-1",
            "token-1",
            media_base_url="https://media.example.com/bot",
            media_root=str(self.tmp),
        )
        self.transport = FakeGraphTransport()

    def _publish(self, items):
        with mock.patch.object(instagram_mod, "request_bytes", self.transport), mock.patch.object(
            instagram_mod.time, "sleep"
        ):
            return self.publisher.publish("Caption text", items)

    def test_public_url_maps_local_file_to_public_url(self):
        self.assertEqual(
            self.publisher.public_url(self.image),
            "https://media.example.com/bot/media/shot.png",
        )

    def test_public_url_requires_base_url(self):
        publisher = InstagramPublisher("business-1", "token-1", media_root=str(self.tmp))
        with self.assertRaisesRegex(InstagramError, "PUBLIC_BASE_URL"):
            publisher.public_url(self.image)

    def test_public_url_rejects_files_outside_root(self):
        outside = Path("/etc/hostname")
        with self.assertRaisesRegex(InstagramError, "outside"):
            self.publisher.public_url(outside)

    def test_validate_returns_account(self):
        with mock.patch.object(instagram_mod, "request_bytes", self.transport):
            self.assertEqual(
                self.publisher.validate(),
                {"id": "ig-1", "username": "locallab"},
            )

    def test_validate_requires_configuration(self):
        publisher = InstagramPublisher("", "")
        with self.assertRaisesRegex(InstagramError, "not configured"):
            publisher.validate()

    def test_single_photo_publish(self):
        result = self._publish([{"kind": "image", "path": str(self.image)}])
        self.assertEqual(result, {"kind": "photo", "media_id": "published-1"})
        posts = [
            call
            for call in self.transport.calls
            if call["url"].endswith("/business-1/media")
        ]
        self.assertEqual(len(posts), 1)
        self.assertIn("image_url=", posts[0]["body"])

    def test_carousel_publish_creates_children_first(self):
        paths = [self.media_dir / f"photo-{index}.png" for index in range(3)]
        for path in paths:
            path.write_bytes(b"png")
            self.addCleanup(path.unlink, missing_ok=True)
        result = self._publish([{"kind": "image", "path": str(path)} for path in paths])
        self.assertEqual(result["kind"], "carousel")
        media_calls = [
            call
            for call in self.transport.calls
            if call["url"].endswith("/business-1/media")
        ]
        self.assertEqual(len(media_calls), 4)
        carousel_call = media_calls[-1]
        self.assertIn("media_type=CAROUSEL", carousel_call["body"])
        self.assertIn(
            "children=container-1%2Ccontainer-2%2Ccontainer-3",
            carousel_call["body"],
        )

    def test_carousel_refuses_more_than_ten_photos(self):
        items = [
            {"kind": "image", "path": str(self.image)}
            for _ in range(instagram_mod.CAROUSEL_MAX + 1)
        ]
        with self.assertRaisesRegex(InstagramError, "between 2 and"):
            self._publish(items)

    def test_video_publish_uses_video_container(self):
        result = self._publish([{"kind": "video", "path": str(self.video)}])
        self.assertEqual(result["kind"], "video")
        self.assertTrue(
            any("media_type=VIDEO" in call["body"] for call in self.transport.calls)
        )

    def test_video_publish_rejects_non_mp4(self):
        webm = self.media_dir / "clip.webm"
        webm.write_bytes(b"webm")
        self.addCleanup(webm.unlink, missing_ok=True)
        with self.assertRaisesRegex(InstagramError, "MP4 or MOV"):
            self._publish([{"kind": "video", "path": str(webm)}])

    def test_mixed_and_empty_posts_are_refused(self):
        with self.assertRaisesRegex(InstagramError, "needs at least one"):
            self._publish([])
        with self.assertRaisesRegex(InstagramError, "cannot mix"):
            self._publish(
                [
                    {"kind": "image", "path": str(self.image)},
                    {"kind": "video", "path": str(self.video)},
                ]
            )

    def test_processing_error_is_reported(self):
        self.transport.fail_container = "container-1"
        with self.assertRaisesRegex(InstagramError, "broken media"):
            self._publish([{"kind": "image", "path": str(self.image)}])

    def test_graph_api_error_maps_code_and_subcode(self):
        def failing(url, *, method=None, raw_body=None, content_type=None, timeout=None):
            return 400, json.dumps(
                {
                    "error": {
                        "message": "Invalid token",
                        "code": 190,
                        "error_subcode": 123,
                    }
                }
            ).encode("utf-8")

        publisher = InstagramPublisher("business-1", "bad-token")
        with mock.patch.object(instagram_mod, "request_bytes", failing):
            with self.assertRaisesRegex(InstagramError, "Invalid token"):
                publisher.validate()

    def test_network_error_is_wrapped(self):
        def failing(*args, **kwargs):
            raise ConnectionError("tunnel down")

        publisher = InstagramPublisher("business-1", "token-1")
        with mock.patch.object(instagram_mod, "request_bytes", failing):
            with self.assertRaisesRegex(InstagramError, "network error"):
                publisher.validate()


if __name__ == "__main__":
    unittest.main()
