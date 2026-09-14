"""Tests for the LinkedIn REST publisher and its post text helper."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from content_bot import linkedin
from content_bot.accounts import SocialAccount
from content_bot.http import HttpResponse


def account(
    name: str = "personal",
    kind: str = "person",
    urn: str = "urn:li:person:abc123",
    token: str = "token",
) -> SocialAccount:
    return SocialAccount(
        platform="linkedin",
        account=name,
        kind=kind,
        access_token=token,
        author=urn,
    )


def response(status: int = 201, body: dict | None = None, headers: dict | None = None):
    return HttpResponse(
        status=status,
        body=json.dumps(body or {}).encode("utf-8"),
        headers=headers or {},
    )


class CommentaryTests(unittest.TestCase):
    def test_title_body_and_source_link_are_joined(self):
        text = linkedin.build_commentary(
            "Layer caching", "Images reuse layers.", "https://example.com/a"
        )
        self.assertEqual(
            "Layer caching\n\nImages reuse layers.\n\nhttps://example.com/a", text
        )

    def test_source_link_is_not_duplicated(self):
        text = linkedin.build_commentary(
            "Title", "See https://example.com/a for the details.", "https://example.com/a"
        )
        self.assertEqual(1, text.count("https://example.com/a"))

    def test_long_text_is_trimmed_within_the_platform_limit(self):
        body = " ".join(["word"] * 2000)
        text = linkedin.build_commentary("Title", body)
        self.assertLessEqual(len(text), linkedin.COMMENTARY_MAX)
        self.assertFalse(text.endswith(" "))

    def test_empty_draft_produces_empty_text(self):
        self.assertEqual("", linkedin.build_commentary("", ""))


class PublisherTests(unittest.TestCase):
    def build(self, responses, **kwargs):
        self.calls = []

        def fake(url, **call):
            self.calls.append({"url": url, **call})
            result = responses[min(len(self.calls) - 1, len(responses) - 1)]
            if isinstance(result, Exception):
                raise result
            return result

        self.sleeps = []
        patcher = mock.patch("content_bot.linkedin.request_response", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return linkedin.LinkedInPublisher(
            retries=kwargs.pop("retries", 3),
            backoff=0.01,
            sleep=self.sleeps.append,
            **kwargs,
        )

    def test_text_post_sends_the_versioned_payload(self):
        publisher = self.build(
            [response(201, headers={"x-restli-id": "urn:li:share:777"})]
        )
        urn = publisher.publish_text("Hello LinkedIn", account())
        self.assertEqual("urn:li:share:777", urn)
        call = self.calls[0]
        self.assertTrue(call["url"].endswith("/rest/posts"))
        self.assertEqual("POST", call["method"])
        self.assertEqual("Bearer token", call["headers"]["Authorization"])
        self.assertEqual("202601", call["headers"]["LinkedIn-Version"])
        self.assertEqual("2.0.0", call["headers"]["X-Restli-Protocol-Version"])
        self.assertEqual("urn:li:person:abc123", call["payload"]["author"])
        self.assertEqual("Hello LinkedIn", call["payload"]["commentary"])
        self.assertEqual("PUBLIC", call["payload"]["visibility"])
        self.assertNotIn("content", call["payload"])

    def test_image_post_uploads_then_references_the_image_urn(self):
        publisher = self.build(
            [
                response(
                    200,
                    {
                        "value": {
                            "uploadUrl": "https://upload.example.com/1",
                            "image": "urn:li:image:555",
                        }
                    },
                ),
                response(201),
                response(201, headers={"x-restli-id": "urn:li:share:888"}),
            ]
        )
        urn = publisher.publish_image(
            b"jpeg-bytes", "Caption", account(), alt_text="photo.jpg"
        )
        self.assertEqual("urn:li:share:888", urn)
        self.assertIn("initializeUpload", self.calls[0]["url"])
        self.assertEqual(
            {"initializeUploadRequest": {"owner": "urn:li:person:abc123"}},
            self.calls[0]["payload"],
        )
        self.assertEqual("PUT", self.calls[1]["method"])
        self.assertEqual("https://upload.example.com/1", self.calls[1]["url"])
        self.assertEqual(b"jpeg-bytes", self.calls[1]["raw_body"])
        media = self.calls[2]["payload"]["content"]["media"]
        self.assertEqual("urn:li:image:555", media["id"])
        self.assertEqual("photo.jpg", media["altText"])

    def test_transient_errors_are_retried(self):
        publisher = self.build(
            [response(503), response(201, headers={"x-restli-id": "urn:li:share:9"})]
        )
        urn = publisher.publish_text("Retry me", account())
        self.assertEqual("urn:li:share:9", urn)
        self.assertEqual(2, len(self.calls))
        self.assertEqual([0.01], self.sleeps)

    def test_permanent_errors_are_not_retried(self):
        publisher = self.build([response(401, {"message": "Invalid access token"})])
        with self.assertRaises(linkedin.LinkedInError) as caught:
            publisher.publish_text("Nope", account())
        self.assertIn("Invalid access token", str(caught.exception))
        self.assertEqual(1, len(self.calls))

    def test_network_errors_are_retried_and_reported(self):
        publisher = self.build([ConnectionError("boom"), ConnectionError("boom")], retries=2)
        with self.assertRaises(linkedin.LinkedInError) as caught:
            publisher.publish_text("Nope", account())
        self.assertIn("network error", str(caught.exception))
        self.assertEqual(2, len(self.calls))

    def test_empty_text_is_refused(self):
        publisher = self.build([response(201)])
        with self.assertRaises(linkedin.LinkedInError):
            publisher.publish_text("   ", account())
        self.assertEqual([], self.calls)

    def test_unconfigured_account_is_refused(self):
        publisher = self.build([response(201)])
        with self.assertRaises(linkedin.LinkedInError) as caught:
            publisher.publish_text("Hello", account(urn=""))
        self.assertIn("not usable", str(caught.exception))
        self.assertEqual([], self.calls)

    def test_organization_account_uses_its_own_author(self):
        publisher = self.build([response(201)])
        publisher.publish_text(
            "Company news",
            account(name="locallab", kind="organization", urn="urn:li:organization:9"),
        )
        self.assertEqual("urn:li:organization:9", self.calls[0]["payload"]["author"])
