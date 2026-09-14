"""Tests for the Aparat upload client and its text helpers."""

from __future__ import annotations

import base64
import json
import unittest
from dataclasses import replace
from unittest import mock

from content_bot import aparat
from content_bot.config import BotSettings


def credentials(**overrides) -> aparat.AparatCredentials:
    return replace(aparat.AparatCredentials(token="jwt-value"), **overrides)


class HelperTests(unittest.TestCase):
    def test_error_detail_reads_the_api_shape(self):
        body = json.dumps(
            {"errors": [{"status": 401, "detail": "کاربر پیدا نشد"}]}
        ).encode("utf-8")
        self.assertEqual("کاربر پیدا نشد", aparat.error_detail(body))

    def test_error_detail_reads_the_validation_shape(self):
        body = json.dumps({"errors": {"account": ["account must not be empty"]}})
        self.assertEqual(
            "account: account must not be empty",
            aparat.error_detail(body.encode("utf-8")),
        )

    def test_error_detail_falls_back_to_the_raw_body(self):
        self.assertEqual(
            "405 Method Not Allowed", aparat.error_detail(b"405 Method Not Allowed")
        )
        self.assertEqual("", aparat.error_detail(b"{}"))

    def test_find_hash_walks_the_response(self):
        self.assertEqual(
            "abc123", aparat.find_hash({"data": {"attributes": {"uid": "abc123"}}})
        )
        self.assertEqual(
            "hash9",
            aparat.find_hash({"data": [{"attributes": {"videohash": "hash9"}}]}),
        )
        self.assertEqual("", aparat.find_hash({"data": {"attributes": {}}}))

    def test_video_url_needs_a_hash(self):
        self.assertEqual("https://www.aparat.com/v/abc", aparat.video_url("abc"))
        self.assertEqual("", aparat.video_url(""))

    def test_clean_tags_drops_hashes_and_duplicates(self):
        self.assertEqual(
            ["linux", "devops"],
            aparat.clean_tags(["#linux", "linux", "devops", "", None, "  "]),
        )

    def test_clean_tags_keeps_the_order_and_the_limit(self):
        tags = aparat.clean_tags([f"tag{index}" for index in range(10)])
        self.assertEqual(aparat.TAG_LIMIT, len(tags))
        self.assertEqual(["tag0", "tag1"], tags[:2])

    def test_split_title_prefers_the_draft_title(self):
        title, description = aparat.split_title("First line\n\nBody text", title="Draft title")
        self.assertEqual("Draft title", title)
        self.assertEqual("First line\n\nBody text", description)

    def test_split_title_falls_back_to_the_first_line_and_trimms_it(self):
        title, description = aparat.split_title("x" * 200)
        self.assertEqual(aparat.TITLE_MAX, len(title))
        self.assertTrue(title.endswith("..."))
        self.assertEqual("x" * 200, description)

    def test_thumbnail_encodes_image_bytes(self):
        url = aparat.thumbnail_data_url(b"\x89PNG\r\n", "poster.png")
        self.assertEqual(
            "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n").decode(),
            url,
        )

    def test_thumbnail_falls_back_to_jpeg_and_passes_a_data_url_through(self):
        self.assertTrue(
            aparat.thumbnail_data_url(b"bytes", "clip.mp4").startswith(
                "data:image/jpeg;base64,"
            )
        )
        stored = "data:image/webp;base64,AAAA"
        self.assertEqual(stored, aparat.thumbnail_data_url(stored))
        self.assertEqual("", aparat.thumbnail_data_url(b""))
        self.assertEqual("", aparat.thumbnail_data_url("not-an-image"))

    def test_normalize_duration_reads_numbers_and_text(self):
        self.assertEqual(12, aparat.normalize_duration("12"))
        self.assertEqual(12, aparat.normalize_duration(12.0))
        self.assertEqual(12.5, aparat.normalize_duration("12.5"))
        self.assertEqual(0, aparat.normalize_duration(""))
        self.assertEqual(0, aparat.normalize_duration(None))
        self.assertEqual(0, aparat.normalize_duration("soon"))
        self.assertEqual(0, aparat.normalize_duration(-3))

    def test_flag_helpers_read_the_stored_spellings(self):
        for value in ("1", "true", "YES", "on", 1, True):
            self.assertTrue(aparat.flag_enabled(value), value)
        for value in ("0", "", "no", None, False):
            self.assertFalse(aparat.flag_enabled(value), value)
        self.assertEqual(1, aparat.flag_number("true"))
        self.assertEqual(0, aparat.flag_number("0"))

    def test_the_default_category_is_technology(self):
        self.assertEqual("Technology and computers", aparat.CATEGORIES["10"])
        self.assertEqual(aparat.DEFAULT_CATEGORY, aparat.AparatCredentials().category)


class UploadTests(unittest.TestCase):
    """The four upload steps, driven by a fake HTTP layer."""

    def setUp(self):
        self.calls: list[dict] = []
        self.chunks: list[dict] = []
        self.sleeps: list[float] = []

        def fake_bytes(
            url,
            *,
            method=None,
            headers=None,
            payload=None,
            raw_body=None,
            content_type=None,
            timeout=30,
            max_bytes=4_000_000,
        ):
            self.calls.append(
                {
                    "url": url,
                    "method": method,
                    "payload": payload,
                    "raw_body": raw_body,
                    "headers": dict(headers or {}),
                }
            )
            if url.endswith("/upload_config"):
                return 200, json.dumps(
                    {"data": {"server": "https://upload.aparat.test"}}
                ).encode("utf-8")
            if url.endswith("/upload_url"):
                return 200, json.dumps(
                    {"data": [{"attributes": {"token": "upload-token", "uploadId": "987"}}]}
                ).encode("utf-8")
            if url.endswith("/chunksdone"):
                return 200, b"{}"
            if "/upload/uploadId/" in url:
                return 200, json.dumps({"data": {"attributes": {"uid": "vid42"}}}).encode(
                    "utf-8"
                )
            return 200, b"{}"

        def fake_multipart(
            url,
            *,
            fields=None,
            file_field="file",
            filename="file",
            file_bytes=b"",
            headers=None,
            timeout=120,
            max_bytes=60_000_000,
        ):
            self.chunks.append(
                {
                    "url": url,
                    "fields": dict(fields or {}),
                    "file_field": file_field,
                    "filename": filename,
                    "bytes": file_bytes,
                    "headers": dict(headers or {}),
                }
            )
            return 200, b"ok"

        for name, target in (
            ("content_bot.aparat.request_bytes", fake_bytes),
            ("content_bot.aparat.request_multipart", fake_multipart),
        ):
            patcher = mock.patch(name, side_effect=target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def client(self, **kwargs) -> aparat.AparatClient:
        settings = {"chunk_bytes": 4}
        settings.update(kwargs)
        settings.setdefault("sleep", self.sleeps.append)
        return aparat.AparatClient(credentials(), **settings)

    def test_publish_runs_the_upload_steps_in_order(self):
        client = self.client()
        result = client.publish(
            b"0123456789",
            filename="clip.mp4",
            title="Layer caching",
            description="A short explanation.",
            tags=["linux", "devops", "oss"],
        )
        self.assertEqual("vid42", result.hash)
        self.assertEqual("https://www.aparat.com/v/vid42", result.url)
        self.assertEqual(
            [
                "https://www.aparat.com/api/fa/v1/video/upload/upload_config",
                "https://www.aparat.com/api/fa/v1/video/upload/upload_url",
                "https://upload.aparat.test/file/" + result.video,
                "https://upload.aparat.test/chunksdone",
                "https://www.aparat.com/api/fa/v1/video/upload/upload/uploadId/987",
            ],
            [call["url"] for call in self.calls],
        )
        metadata = self.calls[-1]["payload"]
        self.assertEqual("Layer caching", metadata["title"])
        self.assertEqual("A short explanation.", metadata["descr"])
        self.assertEqual("linux-devops-oss", metadata["tags"])
        self.assertEqual("10", metadata["category"])
        # The body mirrors the Aparat uploader request, field by field.
        self.assertEqual(
            {
                "video_pass",
                "watermark",
                "watermark_bool",
                "category",
                "comment",
                "kids_friendly",
                "title",
                "descr",
                "tags",
                "subtitle",
                "publish_date",
            },
            set(metadata),
        )
        self.assertEqual(0, metadata["video_pass"])
        self.assertEqual("1", metadata["watermark"])
        self.assertIs(True, metadata["watermark_bool"])
        self.assertEqual([], metadata["subtitle"])
        self.assertIsNone(metadata["publish_date"])

    def test_the_metadata_call_carries_the_uploader_headers(self):
        client = self.client()
        client.publish(b"0123", filename="clip.mp4", title="T", description="D")
        headers = self.calls[-1]["headers"]
        self.assertEqual("application/json; charset=utf-8", headers["Content-Type"])
        self.assertEqual("true", headers["isNext"])
        self.assertEqual("simple", headers["jsonType"])
        self.assertEqual("aparat", headers["domain"])
        self.assertEqual("https://www.aparat.com/reactupload", headers["currentUrl"])
        self.assertEqual("true", headers["isRedesign"])

    def test_duration_and_thumbnail_reach_the_metadata(self):
        client = self.client()
        client.publish(
            b"0123",
            filename="clip.mp4",
            title="T",
            description="D",
            duration="12",
            thumbnail=b"\xff\xd8jpeg-bytes",
            thumbnail_filename="poster.jpg",
        )
        metadata = self.calls[-1]["payload"]
        self.assertEqual(12, metadata["duration"])
        self.assertEqual(
            "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8jpeg-bytes").decode(),
            metadata["thumbnail"],
        )

    def test_an_unknown_duration_stays_out_of_the_metadata(self):
        client = self.client()
        client.publish(
            b"0123", filename="clip.mp4", title="T", description="D", duration=""
        )
        self.assertNotIn("duration", self.calls[-1]["payload"])
        self.assertNotIn("thumbnail", self.calls[-1]["payload"])

    def test_a_failed_chunk_is_uploaded_again(self):
        seen: list[tuple[int, int]] = []
        client = self.client()
        attempts = {"count": 0}
        original = self.chunks

        def flaky(*args, **kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return 500, b"try again"
            return 200, b"ok"

        with mock.patch("content_bot.aparat.request_multipart", side_effect=flaky):
            result = client.publish(
                b"0123",
                filename="clip.mp4",
                title="T",
                description="D",
                progress=lambda done, total: seen.append((done, total)),
            )
        self.assertEqual("vid42", result.hash)
        self.assertEqual(2, attempts["count"])
        self.assertEqual([(1, 1)], seen)
        self.assertEqual([aparat.RETRY_DELAY_SECONDS], self.sleeps)
        self.assertEqual([], original)

    def test_a_repeated_chunk_failure_is_reported_once_the_retries_run_out(self):
        client = self.client(retries=1)
        with mock.patch(
            "content_bot.aparat.request_multipart",
            return_value=(503, b"upload server is busy"),
        ):
            with self.assertRaises(aparat.AparatError) as caught:
                client.publish(b"0123", filename="clip.mp4", title="T", description="D")
        self.assertIn("chunk 1/1", str(caught.exception))
        self.assertIn("upload server is busy", str(caught.exception))
        self.assertEqual([aparat.RETRY_DELAY_SECONDS], self.sleeps)

    def test_a_refused_chunk_is_not_uploaded_again(self):
        client = self.client()
        with mock.patch(
            "content_bot.aparat.request_multipart", return_value=(403, b"forbidden")
        ):
            with self.assertRaises(aparat.AparatError):
                client.publish(b"0123", filename="clip.mp4", title="T", description="D")
        self.assertEqual([], self.sleeps)

    def test_the_upload_close_is_uploaded_again(self):
        client = self.client()
        answers = {"count": 0}

        def flaky_close(url, **kwargs):
            if url.endswith("/chunksdone"):
                answers["count"] += 1
                if answers["count"] == 1:
                    return 500, b"try again"
            if "/upload/uploadId/" in url:
                return 200, json.dumps({"data": {"attributes": {"uid": "vid42"}}}).encode()
            if url.endswith("/upload_config"):
                return 200, json.dumps({"data": {"server": "https://upload.aparat.test"}}).encode()
            if url.endswith("/upload_url"):
                return 200, json.dumps(
                    {"data": [{"attributes": {"token": "t", "uploadId": "9"}}]}
                ).encode()
            return 200, b"{}"

        with mock.patch("content_bot.aparat.request_bytes", side_effect=flaky_close):
            result = client.publish(b"0123", filename="clip.mp4", title="T", description="D")
        self.assertEqual("vid42", result.hash)
        self.assertEqual(2, answers["count"])
        self.assertEqual([aparat.RETRY_DELAY_SECONDS], self.sleeps)

    def test_publishing_without_a_session_is_refused_before_any_call(self):
        client = aparat.AparatClient(credentials(token="", cookie=""), sleep=self.sleeps.append)
        with self.assertRaises(aparat.AparatError) as caught:
            client.publish(b"0123", filename="clip.mp4", title="T", description="D")
        self.assertIn("not configured", str(caught.exception))
        self.assertEqual([], self.calls)
        self.assertEqual([], self.chunks)

    def test_the_session_header_travels_with_every_call(self):
        client = self.client()
        client.publish(b"0123", filename="clip.mp4", title="T", description="D")
        for call in self.calls:
            self.assertEqual("Bearer jwt-value", call["headers"]["Authorization"])

    def test_the_file_is_split_into_chunks(self):
        client = self.client()
        client.publish(b"0123456789", filename="clip.mp4", title="T", description="D")
        self.assertEqual(3, len(self.chunks))
        self.assertEqual(b"0123", self.chunks[0]["bytes"])
        self.assertEqual(b"4567", self.chunks[1]["bytes"])
        self.assertEqual(b"89", self.chunks[2]["bytes"])
        first = self.chunks[0]
        self.assertEqual("https://upload.aparat.test/upload", first["url"])
        self.assertEqual("qqfile", first["file_field"])
        self.assertEqual("clip.mp4", first["filename"])
        self.assertEqual("upload-token", first["headers"]["X-Token"])
        self.assertEqual(3, first["fields"]["qqtotalparts"])
        self.assertEqual(0, first["fields"]["qqpartindex"])
        self.assertEqual(0, first["fields"]["qqpartbyteoffset"])
        self.assertEqual(10, first["fields"]["qqtotalfilesize"])
        self.assertEqual("video/mp4", first["fields"]["qqtype"])

    def test_a_cookie_session_is_sent_instead_of_a_token(self):
        client = aparat.AparatClient(credentials(token="", cookie="AuthV1=abc"))
        client.publish(b"01", filename="clip.mp4", title="T", description="D")
        for call in self.calls:
            self.assertNotIn("Authorization", call["headers"])
            self.assertEqual("AuthV1=abc", call["headers"]["Cookie"])

    def test_progress_reports_every_chunk(self):
        seen: list[tuple[int, int]] = []
        client = self.client()
        client.publish(
            b"0123456789",
            filename="clip.mp4",
            title="T",
            description="D",
            progress=lambda done, total: seen.append((done, total)),
        )
        self.assertEqual([(1, 3), (2, 3), (3, 3)], seen)

    def test_an_empty_file_is_refused_before_uploading(self):
        client = self.client()
        with self.assertRaises(aparat.AparatError):
            client.publish(b"", filename="clip.mp4", title="T", description="D")
        self.assertEqual([], self.chunks)


class FailureTests(UploadTests):
    """The session and the upload server fail in clear words."""

    def test_a_refused_session_names_the_configuration(self):
        with mock.patch(
            "content_bot.aparat.request_bytes",
            return_value=(401, json.dumps({"errors": [{"detail": "کاربر پیدا نشد"}]}).encode()),
        ):
            client = self.client()
            with self.assertRaises(aparat.AparatError) as caught:
                client.upload_config()
        self.assertIn("CONTENT_APARAT_TOKEN", str(caught.exception))
        self.assertIn("کاربر پیدا نشد", str(caught.exception))

    def test_a_missing_session_is_reported_before_the_call(self):
        client = aparat.AparatClient(credentials(token="", cookie=""))
        self.assertFalse(client.credentials.configured)
        with mock.patch(
            "content_bot.aparat.request_bytes", return_value=(401, b"{}")
        ):
            with self.assertRaises(aparat.AparatError) as caught:
                client.upload_config()
        self.assertIn("not configured", str(caught.exception))

    def test_a_failed_chunk_stops_the_upload(self):
        client = self.client()
        with mock.patch(
            "content_bot.aparat.request_multipart",
            return_value=(500, b"chunk rejected"),
        ):
            with self.assertRaises(aparat.AparatError) as caught:
                client.publish(b"0123456789", filename="clip.mp4", title="T", description="D")
        self.assertIn("chunk 1/3", str(caught.exception))
        self.assertIn("chunk rejected", str(caught.exception))

    def test_metadata_errors_reported_with_http_200_are_refused(self):
        client = self.client()
        with mock.patch(
            "content_bot.aparat.request_bytes",
            side_effect=self._metadata_error_bytes,
        ):
            with self.assertRaises(aparat.AparatError) as caught:
                client.publish(b"0123", filename="clip.mp4", title="T", description="D")
        self.assertIn("video title is too short", str(caught.exception))

    @staticmethod
    def _metadata_error_bytes(url, **kwargs):
        if "/upload/uploadId/" in url:
            return 200, json.dumps(
                {"errors": [{"detail": "video title is too short"}]}
            ).encode("utf-8")
        if url.endswith("/upload_config"):
            return 200, json.dumps({"data": {"server": "https://upload.aparat.test"}}).encode("utf-8")
        if url.endswith("/upload_url"):
            return 200, json.dumps(
                {"data": [{"attributes": {"token": "t", "uploadId": "9"}}]}
            ).encode("utf-8")
        return 200, b"{}"

    def test_a_response_without_an_upload_server_is_refused(self):
        client = self.client()
        with mock.patch(
            "content_bot.aparat.request_bytes",
            return_value=(200, json.dumps({"data": {}}).encode()),
        ):
            with self.assertRaises(aparat.AparatError) as caught:
                client.upload_config()
        self.assertIn("upload server", str(caught.exception))


class SettingsTests(unittest.TestCase):
    def test_the_client_reads_the_channel_defaults_from_settings(self):
        settings = BotSettings(
            bot_token="123:TESTTOKENABCDEFGHIJKLMN",
            aparat_token="jwt",
            aparat_category="16",
            aparat_tags=("linux", "oss"),
            aparat_label="LocalLab",
            aparat_watermark="0",
            aparat_video_pass="1",
            aparat_timeout=30,
            aparat_chunk_bytes=1024,
        )
        client = aparat.client_from_settings(settings)
        self.assertEqual("16", client.credentials.category)
        self.assertEqual(("linux", "oss"), client.credentials.tags)
        self.assertEqual("LocalLab", client.credentials.display)
        self.assertEqual("0", client.credentials.watermark)
        self.assertEqual("1", client.credentials.video_pass)
        self.assertEqual(1024, client.chunk_bytes)

    def test_an_empty_settings_object_still_builds_a_client(self):
        client = aparat.client_from_settings(
            BotSettings(bot_token="123:TESTTOKENABCDEFGHIJKLMN")
        )
        self.assertFalse(client.credentials.configured)
        self.assertEqual(aparat.DEFAULT_CATEGORY, client.credentials.category)
        self.assertEqual("Aparat", client.credentials.display)

    def test_a_console_placeholder_is_not_a_session(self):
        # `copy(...)` answers with the text "undefined" when the console is
        # used without reading the value, and the printed value arrives with
        # its quotes; neither is a session.
        for value in ("undefined", "UNDEFINED", " null ", '"undefined"'):
            with self.subTest(value=value):
                credentials = aparat.AparatCredentials(token=value)
                self.assertEqual("", credentials.token)
                self.assertFalse(credentials.configured)

    def test_a_quoted_session_is_unwrapped(self):
        credentials = aparat.AparatCredentials(
            token='"eyJhbGciOiJIUzI1NiJ9.payload.signature"'
        )
        self.assertEqual("eyJhbGciOiJIUzI1NiJ9.payload.signature", credentials.token)
        self.assertTrue(credentials.configured)
        headers = aparat.AparatClient(credentials).headers()
        self.assertEqual(
            "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
            headers["Authorization"],
        )


if __name__ == "__main__":  # pragma: no cover - unittest entry point
    unittest.main()
