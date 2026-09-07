"""Minimal Telegram Bot API long-polling client (standard library only)."""

from __future__ import annotations

from content_bot.http import HttpError, request_json


class TelegramError(RuntimeError):
    pass


class TelegramApi:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org"):
        self.root = f"{api_base.rstrip('/')}/bot{token}"

    def _transport(self, url: str, payload: dict | None) -> object:
        return request_json(url, payload=payload, timeout=35)

    def _call(self, method: str, params: dict | None = None) -> object:
        try:
            data = self._transport(f"{self.root}/{method}", params)
        except HttpError as error:
            raise TelegramError(f"Telegram {method} HTTP {error.status}") from error
        if not isinstance(data, dict) or data.get("ok") is not True:
            description = data.get("description") if isinstance(data, dict) else str(data)
            raise TelegramError(f"Telegram {method} failed: {description}")
        return data.get("result")

    def get_me(self) -> dict:
        result = self._call("getMe")
        return result if isinstance(result, dict) else {}

    def delete_webhook(self) -> bool:
        result = self._call("deleteWebhook")
        return result is True

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list:
        params: dict = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        result = self._call("getUpdates", params)
        return result if isinstance(result, list) else []

    def send_message(self, chat_id, text: str, reply_markup: dict | None = None) -> dict:
        params = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        result = self._call("sendMessage", params)
        return result if isinstance(result, dict) else {}

    def edit_message_text(
        self,
        chat_id,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
    ) -> object:
        params = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return self._call("editMessageText", params)

    def answer_callback_query(self, query_id: str, text: str | None = None) -> bool:
        params: dict = {"callback_query_id": query_id}
        if text is not None:
            params["text"] = text
        result = self._call("answerCallbackQuery", params)
        return result is True


def approval_keyboard(draft_id: str) -> dict:
    """Inline keyboard for one draft proposal."""
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"approve:{draft_id}"},
                {"text": "Reject", "callback_data": f"reject:{draft_id}"},
            ]
        ]
    }
