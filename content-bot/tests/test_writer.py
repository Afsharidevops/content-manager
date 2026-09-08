"""Writer unit tests for parsing, fallback, and revision behavior."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from content_bot.writer import Writer, WriterError


class StubWriter(Writer):
    def __init__(self, replies):
        super().__init__("http://writer.test/v1")
        self.replies = list(replies)

    def _chat(self, messages):
        if not self.replies:
            raise WriterError("no stub replies left")
        return self.replies.pop(0)


class WriterTest(unittest.TestCase):
    def test_chat_raises_on_empty_content(self):
        writer = Writer("http://writer.test")
        with mock.patch(
            "content_bot.writer.request_json",
            return_value={"choices": [{"message": {"content": "   "}}]},
        ):
            with self.assertRaisesRegex(WriterError, "empty content"):
                writer._chat([{"role": "user", "content": "hi"}])

    def test_chat_raises_when_content_is_null(self):
        writer = Writer("http://writer.test")
        with mock.patch(
            "content_bot.writer.request_json",
            return_value={"choices": [{"message": {"content": None}}]},
        ):
            with self.assertRaisesRegex(WriterError, "no content"):
                writer._chat([{"role": "user", "content": "hi"}])

    def test_chat_sends_configured_token_budget_and_reasoning_effort(self):
        writer = Writer(
            "http://writer.test/v1",
            api_key="sk-test",
            model="free",
            max_tokens=3000,
            reasoning_effort="low",
        )
        with mock.patch(
            "content_bot.writer.request_json",
            return_value={"choices": [{"message": {"content": "ok"}}]},
        ) as request:
            writer._chat([{"role": "user", "content": "hi"}])
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["max_tokens"], 3000)
        self.assertEqual(payload["reasoning_effort"], "low")

    def test_revise_returns_shortened_post(self):
        writer = StubWriter(
            [
                json.dumps(
                    {"title": "Same title", "body": "A much shorter body."},
                    ensure_ascii=False,
                )
            ]
        )
        result = writer.revise_post(
            title="Same title",
            body="A very long body that should be shortened by the writer model.",
            feedback="Make it shorter",
            source_url="https://example.com/post",
        )
        self.assertEqual(result["title"], "Same title")
        self.assertEqual(result["body"], "A much shorter body.\n\nhttps://example.com/post")
        self.assertEqual(result["source_url"], "https://example.com/post")

    def test_revise_raises_when_output_is_unchanged(self):
        writer = StubWriter(
            [
                json.dumps(
                    {"title": "Title", "body": "Current body."},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"title": "Title", "body": "Current body."},
                    ensure_ascii=False,
                ),
            ]
        )
        with self.assertRaisesRegex(WriterError, "no changes"):
            writer.revise_post(
                title="Title",
                body="Current body.",
                feedback="Make it better",
                source_url="",
            )

    def test_revise_retries_once_on_empty_then_succeeds(self):
        writer = StubWriter(
            [
                "",
                json.dumps(
                    {"title": "New title", "body": "Updated body."},
                    ensure_ascii=False,
                ),
            ]
        )
        result = writer.revise_post(
            title="Old title",
            body="Old body.",
            feedback="Reword it",
            source_url="https://example.com/post",
        )
        self.assertEqual(result["title"], "New title")
        self.assertIn("Updated body.", result["body"])

    def test_revise_raises_after_two_unusable_outputs(self):
        writer = StubWriter(["", ""])
        with self.assertRaisesRegex(WriterError, "no usable JSON"):
            writer.revise_post(
                title="Title",
                body="Body.",
                feedback="Shorten it",
                source_url="",
            )


if __name__ == "__main__":
    unittest.main()
