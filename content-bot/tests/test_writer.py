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

    def test_video_script_requests_low_reasoning_and_larger_budget(self):
        writer = Writer("http://writer.test/v1", model="auto", max_tokens=1600)
        reply = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "segments": [
                                    {"say": "خط اول", "visual": "Shot one"},
                                    {"say": "خط دوم", "visual": "Shot two"},
                                ]
                            },
                            ensure_ascii=True,
                        )
                    }
                }
            ]
        }
        with mock.patch(
            "content_bot.writer.request_json", return_value=reply
        ) as request:
            beats = writer.video_script(title="t", body="b", segments=2)
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["max_tokens"], 2600)
        self.assertEqual(payload["reasoning_effort"], "low")
        self.assertEqual([beat["say"] for beat in beats], ["خط اول", "خط دوم"])

    def test_video_script_keeps_explicit_budget_and_reasoning_effort(self):
        writer = Writer(
            "http://writer.test/v1",
            max_tokens=4000,
            reasoning_effort="medium",
        )
        reply = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"segments": [{"say": "یک", "visual": "One"}]},
                            ensure_ascii=True,
                        )
                    }
                }
            ]
        }
        with mock.patch(
            "content_bot.writer.request_json", return_value=reply
        ) as request:
            writer.video_script(title="t", body="b", segments=1)
        payload = request.call_args.kwargs["payload"]
        self.assertEqual(payload["max_tokens"], 4000)
        self.assertEqual(payload["reasoning_effort"], "medium")

    def test_video_script_rejects_incomplete_reply(self):
        writer = Writer("http://writer.test/v1")
        reply = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"segments": [{"say": "یک", "visual": "One"}]},
                            ensure_ascii=True,
                        )
                    }
                }
            ]
        }
        with mock.patch("content_bot.writer.request_json", return_value=reply):
            with self.assertRaisesRegex(WriterError, "incomplete"):
                writer.video_script(title="t", body="b", segments=3)

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


class WriterParseTest(unittest.TestCase):
    def test_plain_json_object(self):
        payload = json.dumps({"title": "عنوان", "body": "متن"})
        self.assertEqual(Writer._parse_json_object(payload), {"title": "عنوان", "body": "متن"})

    def test_fenced_json_is_parsed(self):
        payload = '```json\n{"title": "تیتر", "body": "بدنه"}\n```'
        self.assertEqual(Writer._parse_json_object(payload), {"title": "تیتر", "body": "بدنه"})

    def test_trailing_text_after_json_is_ignored(self):
        payload = '{"title": "تیتر", "body": "بدنه"}\nاین هم یک توضیح اضافه است.'
        self.assertEqual(Writer._parse_json_object(payload), {"title": "تیتر", "body": "بدنه"})

    def test_nested_braces_inside_string_do_not_break_parsing(self):
        payload = '{"title": "a {b} c", "body": "یک {متن} با بریس"}'
        self.assertEqual(
            Writer._parse_json_object(payload),
            {"title": "a {b} c", "body": "یک {متن} با بریس"},
        )

    def test_invalid_content_returns_none(self):
        self.assertIsNone(Writer._parse_json_object("no json here"))


class WriterBrokenJsonTest(unittest.TestCase):
    def test_parse_post_extracts_fields_from_broken_wrapper(self):
        payload = (
            '{"ok": true, "answer": \'json: {"title": "تیتر درست", '
            '"body": "بدنه با \\\\n جدا شده"}\'}'
        )
        post = Writer._parse_post(payload)
        self.assertIsNotNone(post)
        self.assertEqual(post["title"], "تیتر درست")
        self.assertIn("بدنه با", post["body"])

    def test_regex_fields_returns_none_without_body(self):
        self.assertIsNone(Writer._regex_fields('{"title": "فقط تیتر"}'))


class WriterLiteralNewlineTest(unittest.TestCase):
    """Replies that put literal control characters inside JSON strings."""

    def test_parse_post_accepts_literal_newlines_inside_strings(self):
        content = (
            "\u200f"
            '{"title": "تیتر سالم", "body": "پاراگراف اول\n\nپاراگراف دوم {با بریس}"}'
        )
        post = Writer._parse_post(content)
        self.assertIsNotNone(post)
        self.assertEqual(post["title"], "تیتر سالم")
        self.assertIn("پاراگراف اول", post["body"])
        self.assertIn("پاراگراف دوم", post["body"])

    def test_regex_fallback_keeps_persian_with_literal_newlines(self):
        broken = '{"title": "تیتر", "body": "خط اول\nخط دوم"'
        fields = Writer._regex_fields(broken)
        self.assertIsNotNone(fields)
        self.assertEqual(fields["title"], "تیتر")
        self.assertIn("خط اول", fields["body"])


class WriterSourceDedupeTest(unittest.TestCase):
    def test_dedupe_keeps_single_final_url(self):
        url = "https://example.com/post"
        body = f"متن اول\n\n{url}\n\n{url}"
        cleaned = Writer._dedupe_source_url(body, url)
        self.assertEqual(cleaned.count(url), 1)
        self.assertTrue(cleaned.strip().endswith(url))

    def test_missing_url_is_appended(self):
        body = Writer._dedupe_source_url("بدون لینک", "https://example.com/x")
        self.assertTrue(body.endswith("https://example.com/x"))

    def test_with_source_url_normalizes_duplicates(self):
        url = "https://example.com/x"
        post = Writer._with_source_url({"title": "تیتر", "body": f"{url}\nمتن\n{url}"}, url)
        self.assertEqual(post["body"].count(url), 1)
        self.assertTrue(post["body"].strip().endswith(url))


class WriterEncodingTest(unittest.TestCase):
    """Writer replies in legacy single-byte mojibake are repaired or refused."""

    def _generate(self, content: str) -> dict:
        writer = StubWriter([content])
        return writer.generate_post(
            {"title": "Source title", "url": "https://example.com/x", "text": "src"}
        )

    def test_latin1_mojibake_body_is_repaired(self):
        clean = "اگر با Kubernetes کار میکنید، Argo Workflows یک ابزار متنباز است."
        mojibake = clean.encode("utf-8").decode("latin-1")
        payload = json.dumps(
            {"title": "معرفی ابزار", "body": mojibake},
            ensure_ascii=True,
        )
        post = self._generate(payload)
        self.assertEqual(post["title"], "معرفی ابزار")
        self.assertIn("اگر با Kubernetes کار میکنید", post["body"])
        self.assertNotIn("Ø§Ú¯Ø±", post["body"])

    def test_cp1252_mojibake_body_is_repaired(self):
        clean = "میکنید و جایی رسیدهاید که باید چند کار را موازی اجرا کنید."
        mojibake = clean.encode("utf-8").decode("cp1252")
        payload = json.dumps({"title": "تیتر", "body": mojibake}, ensure_ascii=True)
        post = self._generate(payload)
        self.assertIn("میکنید", post["body"])
        self.assertNotIn("Û", post["body"])

    def test_clean_persian_and_english_replies_are_untouched(self):
        persian = json.dumps(
            {"title": "تیتر فارسی", "body": "متن فارسی ساده."},
            ensure_ascii=True,
        )
        post = self._generate(persian)
        self.assertEqual(post["title"], "تیتر فارسی")
        self.assertTrue(post["body"].startswith("متن فارسی ساده."))
        english = json.dumps(
            {"title": "Plain title", "body": "Plain English body."},
            ensure_ascii=True,
        )
        post = self._generate(english)
        self.assertEqual(post["title"], "Plain title")
        self.assertTrue(post["body"].startswith("Plain English body."))

    def test_unrecoverable_garbled_reply_raises(self):
        garbled = "Ø§Ú¯Ø± Ø¨Ø§ \ufffd \u00d8\u00a7\u00da\u00af\u00d8\u00b1"
        payload = json.dumps({"title": garbled[:20], "body": garbled}, ensure_ascii=False)
        with self.assertRaisesRegex(WriterError, "garbled"):
            self._generate(payload)

    def test_parse_post_repairs_garbled_body_directly(self):
        clean = "ابزار Argo Workflows برای Kubernetes طراحی شده است."
        mojibake = clean.encode("utf-8").decode("latin-1")
        content = '{"title": "معرفی ابزار", "body": "%s"}' % mojibake.replace('"', '\\"')
        post = Writer._parse_post(content)
        self.assertIsNotNone(post)
        self.assertIn("ابزار Argo Workflows", post["body"])
