"""Offline tests for the Media Studio HTTP client."""

from __future__ import annotations

import unittest

from content_bot.http import HttpError
from content_bot.mediastudio import MediaStudio, MediaStudioError


def _json_responder(payload):
    def respond(url, *, payload=None, headers=None, timeout=30):
        if payload is not None:
            return None
        return {"job": dict(payload or {})} if url.endswith("/jobs/") else {"job": {}}
    return None


class FakeJson:
    def __init__(self, submit_payload=None, job_payload=None):
        self.submit_payload = submit_payload or {"job": {"id": "j1"}}
        self.job_payload = job_payload or {"job": {"id": "j1", "status": "running", "artifacts": []}}

    def __call__(self, url, *, payload=None, headers=None, timeout=30):
        if payload is not None:
            self.submit_url = url
            return self.submit_payload
        self.job_url = url
        return self.job_payload


class MediaStudioTests(unittest.TestCase):
    def test_submit_and_lookup(self):
        json_fn = FakeJson()
        client = MediaStudio("http://ms:8850", "tok", request_json_fn=json_fn)
        job_id = client.submit("api-image", "prompt text", params={"size": "1024x1024"})
        self.assertEqual(job_id, "j1")
        self.assertTrue(json_fn.submit_url.endswith("/jobs"))
        record = client.job("j1")
        self.assertEqual(record["status"], "running")

    def test_submit_without_id_raises(self):
        client = MediaStudio("http://ms:8850", request_json_fn=FakeJson(submit_payload={"job": {}}))
        with self.assertRaises(MediaStudioError):
            client.submit("api-image", "prompt text")

    def test_submit_http_error_raises(self):
        def failing(url, *, payload=None, headers=None, timeout=30):
            raise HttpError(500, b"boom")

        client = MediaStudio("http://ms:8850", request_json_fn=failing)
        with self.assertRaises(MediaStudioError):
            client.submit("api-image", "prompt text")

    def test_download_bytes(self):
        client = MediaStudio(
            "http://ms:8850",
            request_bytes_fn=lambda url, **kwargs: (200, b"\x89PNG"),
        )
        self.assertEqual(client.download("j1", "image_0.png"), b"\x89PNG")

    def test_pick_artifact_prefers_media_kinds(self):
        job = {
            "artifacts": [
                {"name": "failure.json", "kind": "file"},
                {"name": "image_0.png", "kind": "image"},
            ]
        }
        self.assertEqual(MediaStudio.pick_artifact(job), ("image_0.png", "image"))
        self.assertIsNone(MediaStudio.pick_artifact({"artifacts": []}))


if __name__ == "__main__":
    unittest.main()


class MediaStudioBrandTests(unittest.TestCase):
    def test_brand_image_sends_raw_body_and_returns_branded_bytes(self):
        captured = {}

        def raw_responder(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return (200, b"branded-image")

        client = MediaStudio("http://ms:8850", "tok", request_bytes_fn=raw_responder)
        result = client.brand_image(b"raw-image", content_type="image/jpeg")
        self.assertEqual(result, b"branded-image")
        self.assertEqual(captured["raw_body"], b"raw-image")
        self.assertEqual(captured["headers"]["Content-Type"], "image/jpeg")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer tok")
        self.assertTrue(str(captured["url"]).endswith("/brand"))

    def test_brand_image_returns_original_when_body_empty(self):
        client = MediaStudio("http://ms:8850", request_bytes_fn=lambda url, **kwargs: (200, b"x"))
        self.assertEqual(client.brand_image(b""), b"")

    def test_brand_image_http_error_raises(self):
        client = MediaStudio(
            "http://ms:8850",
            request_bytes_fn=lambda url, **kwargs: (500, b"boom"),
        )
        with self.assertRaisesRegex(MediaStudioError, "HTTP 500"):
            client.brand_image(b"raw-image")

    def test_brand_image_network_error_raises(self):
        def offline(url, **kwargs):
            raise ConnectionError("unreachable")

        client = MediaStudio("http://ms:8850", request_bytes_fn=offline)
        with self.assertRaisesRegex(MediaStudioError, "network"):
            client.brand_image(b"raw-image")
