"""Tests for the Telegram API client payloads and error handling."""

from __future__ import annotations

import unittest

from content_bot.telegram import TelegramApi, TelegramError, approval_keyboard


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        method = url.rsplit("/", 1)[-1]
        if method == "getUpdates":
            return {"ok": True, "result": []}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": 42}}
        return {"ok": True, "result": True}


class TelegramApiTest(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.api = TelegramApi("123:TESTTOKENABCDEFGHIJKLMN", "https://api.telegram.org")
        self.api._transport = self.transport

    def test_send_message_builds_url_and_keyboard(self):
        result = self.api.send_message(
            12345,
            "Hello",
            reply_markup=approval_keyboard("draft-1"),
        )
        url, payload = self.transport.calls[-1]
        self.assertTrue(url.endswith("/sendMessage"))
        self.assertEqual(payload["chat_id"], 12345)
        self.assertEqual(payload["text"], "Hello")
        buttons = payload["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([button["text"] for button in buttons], ["Approve", "Reject"])
        self.assertEqual(buttons[0]["callback_data"], "approve:draft-1")
        self.assertEqual(result["message_id"], 42)

    def test_get_updates_passes_offset(self):
        self.api.get_updates(offset=7, timeout=25)
        url, payload = self.transport.calls[-1]
        self.assertTrue(url.endswith("/getUpdates"))
        self.assertEqual(payload, {"offset": 7, "timeout": 25})

    def test_error_response_raises_telegram_error(self):
        def failing(url, payload):
            return {"ok": False, "description": "Unauthorized"}

        self.api._transport = failing
        with self.assertRaises(TelegramError):
            self.api.get_me()


if __name__ == "__main__":
    unittest.main()
