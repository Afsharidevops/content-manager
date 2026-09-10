"""Minimal Telegram Bot API long-polling client (standard library only)."""

from __future__ import annotations

import json

from content_bot.http import HttpError as _HttpError
from content_bot.http import request_multipart_many
from content_bot.http import request_bytes, request_multipart
from content_bot.http import HttpError, request_json


class TelegramError(RuntimeError):
    pass


class TelegramApi:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org"):
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.root = f"{self.api_base}/bot{token}"

    def _transport(
        self,
        url: str,
        payload: dict | None,
        *,
        timeout: int = 35,
    ) -> object:
        return request_json(url, payload=payload, timeout=timeout)

    def _call(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout: int = 35,
    ) -> object:
        try:
            data = self._transport(f"{self.root}/{method}", params, timeout=timeout)
        except HttpError as error:
            raise TelegramError(f"Telegram {method} HTTP {error.status}") from error
        if not isinstance(data, dict) or data.get("ok") is not True:
            description = data.get("description") if isinstance(data, dict) else str(data)
            raise TelegramError(f"Telegram {method} failed: {description}")
        return data.get("result")

    def _upload(
        self,
        method: str,
        fields: dict,
        *,
        file_field: str,
        filename: str,
        file_bytes: bytes,
    ) -> object:
        """Upload one file through a Telegram Bot API media method."""
        try:
            status, body = request_multipart(
                f"{self.root}/{method}",
                fields=fields,
                file_field=file_field,
                filename=filename,
                file_bytes=file_bytes,
                timeout=240,
            )
        except _HttpError as error:
            raise TelegramError(f"Telegram {method} HTTP {error.status}") from error
        except ConnectionError as error:
            raise TelegramError(f"Telegram {method} network error: {error}") from error
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as error:
            raise TelegramError(f"Telegram {method} returned invalid JSON") from error
        if not isinstance(data, dict) or data.get("ok") is not True:
            description = data.get("description") if isinstance(data, dict) else str(data)
            raise TelegramError(f"Telegram {method} failed: {description}")
        return data.get("result")

    def send_photo(
        self,
        chat_id,
        filename: str,
        file_bytes: bytes,
        *,
        caption: str = "",
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        """Send one photo with an optional HTML caption."""
        fields = {"chat_id": chat_id, "caption": caption}
        if parse_mode is not None:
            fields["parse_mode"] = parse_mode
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(reply_markup)
        result = self._upload(
            "sendPhoto",
            fields,
            file_field="photo",
            filename=filename,
            file_bytes=file_bytes,
        )
        return result if isinstance(result, dict) else {}

    def send_video(
        self,
        chat_id,
        filename: str,
        file_bytes: bytes,
        *,
        caption: str = "",
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        """Send one video with an optional HTML caption."""
        fields = {"chat_id": chat_id, "caption": caption}
        if parse_mode is not None:
            fields["parse_mode"] = parse_mode
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(reply_markup)
        result = self._upload(
            "sendVideo",
            fields,
            file_field="video",
            filename=filename,
            file_bytes=file_bytes,
        )
        return result if isinstance(result, dict) else {}

    def send_media_group(
        self,
        chat_id,
        files: list[tuple[str, bytes]],
        *,
        caption: str = "",
        parse_mode: str | None = None,
    ) -> dict:
        """Send several photos as one Telegram media group (album)."""
        media_items: list[dict] = []
        for index, (_filename, _bytes) in enumerate(files):
            item = {"type": "photo", "media": f"attach://photo{index}"}
            if index == 0:
                if caption:
                    item["caption"] = caption
                if parse_mode is not None:
                    item["parse_mode"] = parse_mode
            media_items.append(item)
        fields = {"chat_id": chat_id, "media": json.dumps(media_items)}
        uploads = [
            (f"photo{index}", filename, file_bytes)
            for index, (filename, file_bytes) in enumerate(files)
        ]
        try:
            status, body = request_multipart_many(
                f"{self.root}/sendMediaGroup",
                fields=fields,
                files=uploads,
                timeout=240,
            )
        except _HttpError as error:
            raise TelegramError(f"Telegram sendMediaGroup HTTP {error.status}") from error
        except ConnectionError as error:
            raise TelegramError(f"Telegram sendMediaGroup network error: {error}") from error
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as error:
            raise TelegramError(f"Telegram sendMediaGroup returned invalid JSON") from error
        if not isinstance(data, dict) or data.get("ok") is not True:
            description = data.get("description") if isinstance(data, dict) else str(data)
            raise TelegramError(f"Telegram sendMediaGroup failed: {description}")
        result = data.get("result")
        if isinstance(result, list) and result:
            return result[0] if isinstance(result[0], dict) else {}
        return {}

    def get_me(self) -> dict:
        result = self._call("getMe")
        return result if isinstance(result, dict) else {}

    def delete_webhook(self) -> bool:
        result = self._call("deleteWebhook")
        return result is True

    def set_my_commands(self, commands: list[dict], scope: dict | None = None) -> bool:
        payload = {"commands": commands}
        if scope is not None:
            payload["scope"] = scope
        result = self._call("setMyCommands", payload)
        return result is True

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list:
        params: dict = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        # The read timeout has to outlive the long poll: Telegram answers when
        # the poll expires, and a slow route adds several seconds on top.
        result = self._call("getUpdates", params, timeout=timeout + 25)
        return result if isinstance(result, list) else []

    def send_message(
        self,
        chat_id,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> dict:
        params = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        result = self._call("sendMessage", params)
        return result if isinstance(result, dict) else {}

    def edit_message_text(
        self,
        chat_id,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> object:
        params = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        return self._call("editMessageText", params)

    def edit_message_caption(
        self,
        chat_id,
        message_id: int,
        caption: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
    ) -> object:
        params = {"chat_id": chat_id, "message_id": message_id, "caption": caption}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        return self._call("editMessageCaption", params)

    def delete_message(self, chat_id, message_id: int) -> bool:
        result = self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        return result is True

    def answer_callback_query(self, query_id: str, text: str | None = None) -> bool:
        params: dict = {"callback_query_id": query_id}
        if text is not None:
            params["text"] = text
        result = self._call("answerCallbackQuery", params)
        return result is True

    def get_file(self, file_id: str) -> dict:
        """Resolve one Telegram file id to a download path."""
        result = self._call("getFile", {"file_id": file_id})
        return result if isinstance(result, dict) else {}

    def download_file(self, file_path: str, max_bytes: int = 25_000_000) -> bytes:
        """Download one Telegram file by its Bot API file path."""
        url = f"{self.api_base}/file/bot{self.token}/{file_path}"
        try:
            status, body = request_bytes(url, timeout=90, max_bytes=max_bytes)
        except ConnectionError as error:
            raise TelegramError(f"Telegram file download network error: {error}") from error
        if status >= 400:
            raise TelegramError(f"Telegram file download HTTP {status}")
        return body


def approval_keyboard(draft_id: str) -> dict:
    """Inline keyboard for one draft proposal."""
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"approve:{draft_id}"},
                {"text": "Reject", "callback_data": f"reject:{draft_id}"},
            ],
            platform_choice_row(draft_id),
        ]
    }


def media_choice_keyboard(draft_id: str) -> dict:
    """Ask how the draft should get media."""
    return {
        "inline_keyboard": [
            [
                {"text": "Text only", "callback_data": f"media:none:{draft_id}"},
                {"text": "AI image", "callback_data": f"media:image:{draft_id}"},
                {"text": "Send my image", "callback_data": f"media:user_image:{draft_id}"},
            ],
            [
                {
                    "text": "Send several images",
                    "callback_data": f"media:user_images:{draft_id}",
                },
            ],
            [
                {
                    "text": "My video (get a prompt)",
                    "callback_data": f"media:video_prompt:{draft_id}",
                },
            ],
        ]
    }


def video_style_keyboard(draft_id: str, *, character: bool) -> dict:
    """Ask whether a hand-made reel uses the saved character or pure AI shots."""
    rows = []
    if character:
        rows.append(
            [
                {
                    "text": "With my character",
                    "callback_data": f"media:vstyle_char:{draft_id}",
                }
            ]
        )
    rows.append(
        [
            {
                "text": "AI promo (no character)",
                "callback_data": f"media:vstyle_ai:{draft_id}",
            }
        ]
    )
    rows.append([{"text": "Cancel video", "callback_data": f"media:none:{draft_id}"}])
    return {"inline_keyboard": rows}


def video_prompt_duration_keyboard(draft_id: str) -> dict:
    """Pick the reel length before the segmented prompt package is built."""
    return {
        "inline_keyboard": [
            [
                {"text": "~10 seconds", "callback_data": f"media:script10:{draft_id}"},
                {"text": "Up to 30 seconds", "callback_data": f"media:script30:{draft_id}"},
            ],
            [{"text": "Cancel video", "callback_data": f"media:none:{draft_id}"}],
        ]
    }


def upload_wait_keyboard(draft_id: str) -> dict:
    """Shown while the bot waits for the operator to send a media file."""
    return {
        "inline_keyboard": [
            [
                {"text": "Cancel upload", "callback_data": f"media:cancel_upload:{draft_id}"},
            ]
        ]
    }


def platform_choice_row(draft_id: str) -> list[dict]:
    """Row that opens the manual-upload platform chooser for one draft."""
    return [
        {"text": "More platforms...", "callback_data": f"platforms:{draft_id}"}
    ]


def platforms_keyboard(draft_id: str, profiles: list[tuple[str, str]]) -> dict:
    """Chooser for platforms that receive a copy-ready upload package."""
    rows: list[list[dict]] = []
    row: list[dict] = []
    for key, label in profiles:
        row.append({"text": label, "callback_data": f"package:{key}:{draft_id}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return {"inline_keyboard": rows}


def instagram_approval_row(draft_id: str) -> list[dict]:
    """Publish targets for one media draft when Instagram is configured."""
    return [
        {
            "text": "Approve to Instagram",
            "callback_data": f"approve_ig:{draft_id}",
        },
        {
            "text": "Approve to Telegram + Instagram",
            "callback_data": f"approve_both:{draft_id}",
        },
    ]


def user_media_preview_keyboard(
    draft_id: str,
    *,
    instagram: bool = False,
    collecting: bool = False,
) -> dict:
    """Approve/Reject plus text-only fallback for an operator-uploaded file."""
    rows = [
        [
            {"text": "Approve", "callback_data": f"approve:{draft_id}"},
            {"text": "Reject", "callback_data": f"reject:{draft_id}"},
        ]
    ]
    actions = []
    if collecting:
        actions.append(
            {"text": "Done", "callback_data": f"media:done:{draft_id}"}
        )
    actions.append({"text": "Text only", "callback_data": f"media:none:{draft_id}"})
    rows.append(actions)
    if instagram:
        rows.append(instagram_approval_row(draft_id))
    rows.append(platform_choice_row(draft_id))
    return {"inline_keyboard": rows}


def media_duration_keyboard(draft_id: str) -> dict:
    """Pick an approximate duration for a generated video."""
    return {
        "inline_keyboard": [
            [
                {"text": "~10 seconds", "callback_data": f"media:video:{draft_id}"},
                {"text": "Up to 30 seconds", "callback_data": f"media:video30:{draft_id}"},
            ],
            [{"text": "Cancel video", "callback_data": f"media:none:{draft_id}"}],
        ]
    }


def media_retry_keyboard(draft_id: str) -> dict:
    """Offer another attempt after a failed media job."""
    return {
        "inline_keyboard": [
            [
                {"text": "Retry", "callback_data": f"media:retry:{draft_id}"},
                {"text": "Text only", "callback_data": f"media:none:{draft_id}"},
            ]
        ]
    }


def media_action_keyboard(draft_id: str) -> dict:
    """Actions available on one generated media preview message."""
    return {
        "inline_keyboard": [
            [
                {"text": "New attempt", "callback_data": f"media:retry:{draft_id}"},
                {"text": "Text only", "callback_data": f"media:none:{draft_id}"},
            ]
        ]
    }


def media_preview_keyboard(draft_id: str, *, instagram: bool = False) -> dict:
    """Approve/Reject plus media actions on one generated preview message."""
    rows = [
        [
            {"text": "Approve", "callback_data": f"approve:{draft_id}"},
            {"text": "Reject", "callback_data": f"reject:{draft_id}"},
        ],
        [
            {"text": "New attempt", "callback_data": f"media:retry:{draft_id}"},
            {"text": "Text only", "callback_data": f"media:none:{draft_id}"},
        ],
    ]
    if instagram:
        rows.append(instagram_approval_row(draft_id))
    rows.append(platform_choice_row(draft_id))
    return {"inline_keyboard": rows}


def discard_confirm_keyboard(draft_id: str) -> dict:
    """Second step before a draft is actually discarded."""
    return {
        "inline_keyboard": [
            [
                {"text": "Confirm discard", "callback_data": f"reject:{draft_id}"},
                {"text": "Cancel", "callback_data": f"cancel:{draft_id}"},
            ]
        ]
    }
