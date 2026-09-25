"""Minimal Telegram Bot API long-polling client (standard library only)."""




from __future__ import annotations

import json

from content_bot.http import HttpError as _HttpError
from content_bot.http import request_multipart_many
from content_bot.http import request_bytes, request_multipart
from content_bot.http import HttpError, request_json


#: Video destination presets: aspect/resolution/size per platform.
VIDEO_DESTINATIONS: dict[str, dict] = {
    "reel": {
        "label": "🎬 IG Reel / Shorts",
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "scene_image_size": "1024x1792",
        "fps": 30,
        "scene_image_max": 10,
    },
    "youtube": {
        "label": "▶️ YouTube / Aparat",
        "aspect_ratio": "16:9",
        "resolution": "1920x1080",
        "scene_image_size": "1792x1024",
        "fps": 30,
        "scene_image_max": 16,
    },
    "telegram": {
        "label": "🖥 Telegram video",
        "aspect_ratio": "16:9",
        "resolution": "1280x720",
        "scene_image_size": "1792x1024",
        "fps": 25,
        "scene_image_max": 8,
    },
    "instagram_post": {
        "label": "🖼 IG Post (4:5)",
        "aspect_ratio": "4:5",
        "resolution": "1080x1350",
        "scene_image_size": "1024x1792",
        "fps": 30,
        "scene_image_max": 8,
    },
}


def video_destination_keyboard(draft_id: str) -> dict:
    """Choose where the video will be published."""
    rows = []
    for key, dest in VIDEO_DESTINATIONS.items():
        rows.append(
            [
                {
                    "text": dest["label"],
                    "callback_data": f"media:vid_dest_{key}:{draft_id}",
                }
            ]
        )
    rows.append([{"text": "❌ Cancel", "callback_data": f"media:none:{draft_id}"}])
    return {"inline_keyboard": rows}


class TelegramError(RuntimeError):
    pass

class TelegramNetworkError(TelegramError):
    """A transient network failure during a Telegram API method.

    Raised from :meth:`TelegramApi._call` so that methods wrapping
    ``except TelegramError`` also capture it, while
    :meth:`~content_bot.bot.ContentBot._startup_identity` can distinguish it
    from a rejected token and retry.
    """


def _api_error_text(method: str, status: int, body: bytes) -> str:
    """Render a Bot API HTTP error together with Telegram's description."""
    description = ""
    try:
        payload = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        description = str(payload.get("description") or "").strip()
    text = f"Telegram {method} HTTP {status}"
    return f"{text}: {description}" if description else text


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
            raise TelegramError(
                _api_error_text(method, error.status, error.body)
            ) from error
        except (ConnectionError, TimeoutError, OSError) as error:
            raise TelegramNetworkError(
                f"Telegram {method} network error: {error}"
            ) from error
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
            raise TelegramError(
                _api_error_text(method, error.status, error.body)
            ) from error
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

    def send_document(
        self,
        chat_id,
        filename: str,
        file_bytes: bytes,
        *,
        caption: str = "",
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> dict:
        """Send one file untouched, for a full-quality manual hand-off."""
        fields = {"chat_id": chat_id, "caption": caption}
        if parse_mode is not None:
            fields["parse_mode"] = parse_mode
        if reply_markup is not None:
            fields["reply_markup"] = json.dumps(reply_markup)
        result = self._upload(
            "sendDocument",
            fields,
            file_field="document",
            filename=filename,
            file_bytes=file_bytes,
        )
        return result if isinstance(result, dict) else {}

    def send_video_by_id(
        self,
        chat_id,
        file_id: str,
        *,
        caption: str = "",
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
        as_document: bool = False,
    ) -> dict:
        """Send a video that already lives on Telegram without downloading it.

        Files above the bot download limit (20 MB) arrive as a ``file_id``
        only; sending that id back is the supported way to forward them. A
        file id is bound to the method that produced it, so a clip sent as a
        document is re-sent with ``sendDocument``.
        """
        method = "sendDocument" if as_document else "sendVideo"
        field = "document" if as_document else "video"
        params: dict = {"chat_id": chat_id, field: file_id}
        if caption:
            params["caption"] = caption
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup)
        result = self._call(method, params, timeout=120)
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
        disable_web_page_preview: bool | None = None,
    ) -> dict:
        params = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode is not None:
            params["parse_mode"] = parse_mode
        if disable_web_page_preview is not None:
            params["disable_web_page_preview"] = disable_web_page_preview
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

def platform_target_rows(targets: list[tuple[str, str]] | None) -> list[list[dict]]:
    """Render explicit publication targets as two-button rows."""
    rows: list[list[dict]] = []
    row: list[dict] = []
    for label, callback_data in targets or []:
        row.append({"text": label, "callback_data": callback_data})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def approval_keyboard(
    draft_id: str,
    *,
    platforms: bool = False,
    manual_package: bool = False,
    platform_targets: list[tuple[str, str]] | None = None,
) -> dict:
    """Inline keyboard for one draft proposal."""
    rows = [
        [
            {"text": "Approve to Telegram", "callback_data": f"approve:{draft_id}"},
            {"text": "Reject", "callback_data": f"reject:{draft_id}"},
        ]
    ]
    target_rows = platform_target_rows(platform_targets)
    if target_rows:
        rows.extend(target_rows)
    else:
        if manual_package:
            rows.append(instagram_package_row(draft_id))
        if platforms:
            rows.append(platform_choice_row(draft_id))
    return {"inline_keyboard": rows}


def media_choice_keyboard(draft_id: str, *, notebooklm: bool = False) -> dict:
    """Ask how the draft should get media."""
    rows = [
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
                "text": "📤 My video",
                "callback_data": f"media:user_video:{draft_id}",
            },
            {
                "text": "🎬 AI video",
                "callback_data": f"media:ai_video:{draft_id}",
            },
        ],
        [
            {
                "text": "📝 Get a prompt",
                "callback_data": f"media:get_prompt:{draft_id}",
            },
        ],
    ]
    if notebooklm:
        rows.append(
            [
                {
                    "text": "🎬 NotebookLM video",
                    "callback_data": f"media:nlm:{draft_id}",
                },
            ]
        )
    return {"inline_keyboard": rows}

def ai_video_plan_keyboard(draft_id: str) -> dict:
    """Review controls for one generated storyboard/timeline plan."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ Render video",
                    "callback_data": f"media:ai_render:{draft_id}",
                },
                {
                    "text": "🔁 Regenerate plan",
                    "callback_data": f"media:ai_video:{draft_id}",
                },
            ],
            [
                {
                    "text": "📥 Copy prompt",
                    "callback_data": f"media:ai_prompt:{draft_id}",
                }
            ],
            [{"text": "❌ Cancel", "callback_data": f"media:none:{draft_id}"}],
        ]
    }

#: The three video profiles the NotebookLM worker ships.
NOTEBOOKLM_PROFILES = (
    ("Technical for developers", "technical_fa", "nlm_tech"),
    ("Educational for everyone", "educational_fa", "nlm_edu"),
    ("Short news overview", "news_fa", "nlm_news"),
)

#: Duration options shown after profile selection. The key is the
#: ``duration_profile`` value sent to the worker; the sub is the callback
#: suffix for Telegram inline keyboards.
NOTEBOOKLM_DURATION_OPTIONS = (
    ("~1 minute", "1min"),
    ("~3 minutes", "3min"),
    ("~5 minutes", "5min"),
)

#: Visual style options shown after the profile is chosen. These match the
#: actual card labels inside NotebookLM's Visual Style carousel. The first
#: value is the Telegram keyboard label; the second is the ``video_style``
#: string sent to the worker.
NOTEBOOKLM_STYLES = (
    ("Default (auto)", "auto"),
    ("Classic / \u06a9\u0644\u0627\u0633\u06cc\u06a9", "classic"),
    ("Whiteboard / \u062a\u062e\u062a\u0647\u200c\u0633\u0641\u06cc\u062f", "whiteboard"),
    ("Kawaii / \u06a9\u0627\u0648\u0627\u06cc\u06cc", "kawaii"),
    ("Anime / \u0627\u0646\u06cc\u0645\u0647", "anime"),
    ("Watercolor / \u0622\u0628\u200c\u0631\u0646\u06af", "watercolor"),
    ("Retro print / \u0686\u0627\u067e \u0633\u0628\u06a9 \u0642\u062f\u06cc\u0645", "retro_print"),
    ("Heritage / \u0645\u06cc\u0631\u0627\u062b", "heritage"),
    ("Paper craft / \u06a9\u0627\u0631\u062f\u0633\u062a\u06cc \u06a9\u0627\u063a\u0630\u06cc", "paper_craft"),
)



def notebooklm_profile_keyboard(draft_id: str) -> dict:
    """Pick the NotebookLM video profile before the job starts."""
    rows = [
        [{"text": label, "callback_data": f"media:{sub}:{draft_id}"}]
        for label, _value, sub in NOTEBOOKLM_PROFILES
    ]
    rows.append([{"text": "Cancel video", "callback_data": f"media:none:{draft_id}"}])
    return {"inline_keyboard": rows}

def notebooklm_duration_keyboard(draft_id: str, profile_sub: str) -> dict:
    """Pick the video length after the profile has been chosen."""
    rows = [
        [
            {
                "text": label,
                "callback_data": f"media:nlm_dur_{key}:{draft_id}",
            }
        ]
        for label, key in NOTEBOOKLM_DURATION_OPTIONS
    ]
    cancel = {"text": "Default length", "callback_data": f"media:nlm_dur_:{draft_id}"}
    rows.append([cancel])
    rows.append([{"text": "Cancel video", "callback_data": f"media:none:{draft_id}"}])
    return {"inline_keyboard": rows}


def notebooklm_style_keyboard(draft_id: str) -> dict:
    """Pick a visual style after the profile, before the duration."""
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for label, style_key in NOTEBOOKLM_STYLES:
        row.append(
            {
                "text": label,
                "callback_data": f"media:nlm_style_{style_key}:{draft_id}",
            }
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            {
                "text": "\u270f\ufe0f Custom style\u2026",
                "callback_data": f"media:nlm_style_custom:{draft_id}",
            }
        ]
    )
    rows.append(
        [{"text": "Cancel video", "callback_data": f"media:none:{draft_id}"}]
    )
    return {"inline_keyboard": rows}

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
    """One row exposing the platform picker."""
    return [
        {"text": "Choose another platform", "callback_data": f"platforms:{draft_id}"}
    ]


def packages_keyboard(draft_id: str) -> dict:
    """Keep publication targets reachable from the post-publish message."""
    return {"inline_keyboard": [platform_choice_row(draft_id)]}


def platforms_keyboard(draft_id: str, profiles: list[tuple[str, str]]) -> dict:
    """Chooser for one explicit publication target."""
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


def instagram_package_row(draft_id: str) -> list[dict]:
    """Manual-upload hand-off offered as an explicit Instagram target."""
    return [
        {"text": "Instagram", "callback_data": f"post_package:{draft_id}"}
    ]


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
    platforms: bool = False,
    manual_package: bool = False,
    platform_targets: list[tuple[str, str]] | None = None,
) -> dict:
    """Approve/Reject plus text-only fallback for an operator-uploaded file."""
    rows = [
        [
            {"text": "Approve to Telegram", "callback_data": f"approve:{draft_id}"},
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
    target_rows = platform_target_rows(platform_targets)
    if target_rows:
        rows.extend(target_rows)
    else:
        if manual_package:
            rows.append(instagram_package_row(draft_id))
        if platforms:
            rows.append(platform_choice_row(draft_id))
        elif instagram:
            rows.append(instagram_approval_row(draft_id))
    return {"inline_keyboard": rows}


def video_edit_keyboard(draft_id: str) -> dict:
    """Ask what to do with an operator-recorded video before publishing.

    The operator can send a clip they recorded themselves; the bot asks
    whether Media Studio should prepare it first or the file should publish
    unchanged. Both answers only decide the media; the draft still needs the
    usual Approve.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "Edit it", "callback_data": f"media:video_edit:{draft_id}"},
                {"text": "Publish as-is", "callback_data": f"media:video_keep:{draft_id}"},
            ]
        ]
    }


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


def media_preview_keyboard(
    draft_id: str,
    *,
    instagram: bool = False,
    platforms: bool = False,
    manual_package: bool = False,
    platform_targets: list[tuple[str, str]] | None = None,
) -> dict:
    """Approve/Reject plus media actions on one generated preview message."""
    rows = [
        [
            {"text": "Approve to Telegram", "callback_data": f"approve:{draft_id}"},
            {"text": "Reject", "callback_data": f"reject:{draft_id}"},
        ],
        [
            {"text": "New attempt", "callback_data": f"media:retry:{draft_id}"},
            {"text": "Text only", "callback_data": f"media:none:{draft_id}"},
        ],
    ]
    target_rows = platform_target_rows(platform_targets)
    if target_rows:
        rows.extend(target_rows)
    else:
        if manual_package:
            rows.append(instagram_package_row(draft_id))
        if platforms:
            rows.append(platform_choice_row(draft_id))
        elif instagram:
            rows.append(instagram_approval_row(draft_id))
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
