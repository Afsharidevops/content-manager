"""Automatic publishers for the audiences of the stack.

Telegram is the operator surface, so it has its own client. The channels in
this module reach audiences on messengers that speak a Telegram-shaped Bot
API (Bale), the EitaaYar gateway (Eitaa), the LinkedIn REST API, and the
Aparat upload API. Aparat carries videos only, so its adapter refuses text
and photo posts and keeps the copy-ready package in reach for those.
"""

from __future__ import annotations

import html
import json
import re
from urllib.parse import urlencode

from content_bot import aparat as aparat_mod
from content_bot import telegram as telegram_mod
from content_bot import linkedin as linkedin_mod
from content_bot.http import HttpError, request_bytes, request_json, request_multipart

_HTML_TAG = re.compile(r"<[^>]+>")


def _error_note(body: bytes) -> str:
    """Short, safe description out of one error response body."""
    try:
        payload = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return ""
    if isinstance(payload, dict):
        return str(payload.get("description") or "").strip()
    return ""


class ChannelError(RuntimeError):
    """Raised when a channel cannot publish one draft."""


def _plain_text(text: str) -> str:
    """Drop the HTML tags some chat APIs do not render."""
    return html.unescape(_HTML_TAG.sub("", str(text or ""))).strip()


class ChatChannel:
    """Shared interface for one automatically published messenger channel."""

    key = ""
    label = ""

    def __init__(self, key: str, label: str, token: str, chat_id: str, api_base: str):
        self.key = key
        self.label = label
        self.token = str(token or "")
        self.chat_id = str(chat_id or "")
        self.api_base = str(api_base or "").rstrip("/")

    @property
    def configured(self) -> bool:
        """True when the channel has both a token and a destination."""
        return bool(self.token and self.chat_id)

    def send_text(self, text: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def send_photo(self, filename: str, data: bytes, caption: str) -> None:
        raise NotImplementedError

    def send_video(
        self,
        filename: str,
        data: bytes,
        caption: str,
        *,
        meta: dict | None = None,
    ) -> None:
        """Publish one video; ``meta`` carries per-platform title and tags."""
        raise NotImplementedError

    def send_document(self, filename: str, data: bytes, caption: str) -> None:
        raise NotImplementedError

    def send_album(self, entries: list[tuple[str, bytes]], caption: str) -> bool:
        """Send several photos in one post; False when unsupported."""
        return False


class TelegramLikeChannel(ChatChannel):
    """A messenger that exposes the Telegram Bot API surface (Bale)."""

    def __init__(self, key: str, label: str, token: str, chat_id: str, api_base: str):
        super().__init__(key, label, token, chat_id, api_base)
        self.api = telegram_mod.TelegramApi(self.token, self.api_base)

    def _call(self, method: str, payload: dict) -> None:
        try:
            self.api._call(method, payload)  # noqa: SLF001 - shared client
        except telegram_mod.TelegramError as error:
            raise ChannelError(f"{self.label}: {error}") from error

    def send_text(self, text: str) -> None:
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    def send_photo(self, filename: str, data: bytes, caption: str) -> None:
        self._upload("sendPhoto", "photo", filename, data, caption)

    def send_video(
        self,
        filename: str,
        data: bytes,
        caption: str,
        *,
        meta: dict | None = None,
    ) -> None:
        self._upload("sendVideo", "video", filename, data, caption)

    def send_document(self, filename: str, data: bytes, caption: str) -> None:
        self._upload("sendDocument", "document", filename, data, caption)

    def _upload(
        self, method: str, field: str, filename: str, data: bytes, caption: str
    ) -> None:
        fields = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"}
        try:
            self.api._upload(method, fields, file_field=field, filename=filename, file_bytes=data)  # noqa: SLF001
        except telegram_mod.TelegramError as error:
            raise ChannelError(f"{self.label}: {error}") from error

    def send_album(self, entries: list[tuple[str, bytes]], caption: str) -> bool:
        try:
            self.api.send_media_group(
                self.chat_id, entries, caption=caption, parse_mode="HTML"
            )
        except telegram_mod.TelegramError as error:
            raise ChannelError(f"{self.label}: {error}") from error
        return True


class EitaaChannel(ChatChannel):
    """Eitaa through the EitaaYar gateway (sendMessage / sendFile)."""

    # EitaaYar multipart upload becomes unreliable above this size (~7 MiB).
    _MAX_UPLOAD_BYTES: int = 6_500_000

    def _post(self, method: str, payload: dict) -> dict:
        """Post one gateway call, retrying as form data when JSON is refused."""
        url = f"{self.api_base}/{self.token}/{method}"
        errors: list[str] = []
        for as_form in (False, True):
            try:
                if as_form:
                    status, body = request_bytes(
                        url,
                        raw_body=urlencode(payload).encode("utf-8"),
                        content_type="application/x-www-form-urlencoded",
                        timeout=60,
                    )
                    if status >= 400:
                        errors.append(f"HTTP {status}")
                        continue
                    result = json.loads(body.decode("utf-8", "replace") or "{}")
                else:
                    result = request_json(url, payload=payload, timeout=60)
            except HttpError as error:
                errors.append(f"HTTP {error.status}")
                continue
            except (ConnectionError, ValueError) as error:
                errors.append(str(error))
                continue
            if isinstance(result, dict) and result.get("ok") is False:
                errors.append(str(result.get("description") or "request rejected"))
                continue
            return result if isinstance(result, dict) else {}
        raise ChannelError(f"{self.label}: {errors[-1] if errors else 'publish failed'}")

    def send_text(self, text: str) -> None:
        self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": _plain_text(text),
                "disable_notification": False,
            },
        )

    def _send_file(self, filename: str, data: bytes, caption: str) -> None:
        if len(data) > self._MAX_UPLOAD_BYTES:
            raise ChannelError(
                f"{self.label}: video file is {len(data)} bytes, which exceeds the "
                f"{self._MAX_UPLOAD_BYTES}-byte upload limit of this gateway. "
                "Sending a text post instead of the video."
            )
        url = f"{self.api_base}/{self.token}/sendFile"
        last_error: Exception | None = None
        for attempt in range(3):
            import time as _time
            if attempt:
                _time.sleep(attempt * 5)
            try:
                status, body = request_multipart(
                    url,
                    fields={"chat_id": self.chat_id, "caption": _plain_text(caption)},
                    file_field="file",
                    filename=filename,
                    file_bytes=data,
                    timeout=180,
                )
            except ConnectionError as error:
                last_error = error
                continue
            if status >= 400:
                raise ChannelError(f"{self.label}: HTTP {status} {_error_note(body)}")
            return
        raise ChannelError(f"{self.label}: network error: {last_error}") from last_error

    def send_photo(self, filename: str, data: bytes, caption: str) -> None:
        self._send_file(filename, data, caption)

    def send_video(
        self,
        filename: str,
        data: bytes,
        caption: str,
        *,
        meta: dict | None = None,
    ) -> None:
        self._send_file(filename, data, caption)

    def send_document(self, filename: str, data: bytes, caption: str) -> None:
        self._send_file(filename, data, caption)


class AparatChannel(ChatChannel):
    """Aparat video publishing for one stored browser session.

    Aparat has no text or image post type, so the adapter refuses anything but
    a video and keeps the copy-ready package in reach for those drafts. One
    publish uploads the file in chunks and reports the watch URL when Aparat
    hands one back.
    """

    def __init__(self, credentials, *, timeout: int = 120, chunk_bytes: int = 3 * 1024 * 1024):
        super().__init__(
            "aparat",
            credentials.display,
            credentials.token or credentials.cookie,
            "",
            credentials.api_base,
        )
        self.credentials = credentials
        self.client = aparat_mod.AparatClient(
            credentials, timeout=timeout, chunk_bytes=chunk_bytes
        )
        self.last_remote_id = ""
        self.last_url = ""

    @property
    def configured(self) -> bool:
        """True when the deployment stored an Aparat browser session."""
        return bool(self.credentials.configured)

    def _needs_video(self) -> ChannelError:
        return ChannelError(
            "Aparat publishes videos only; attach a video to this draft first"
        )

    def send_text(self, text: str) -> None:
        raise self._needs_video()

    def send_photo(self, filename: str, data: bytes, caption: str) -> None:
        raise self._needs_video()

    def send_document(self, filename: str, data: bytes, caption: str) -> None:
        raise self._needs_video()

    def send_video(
        self,
        filename: str,
        data: bytes,
        caption: str,
        *,
        meta: dict | None = None,
    ) -> None:
        meta = dict(meta or {})
        title, description = aparat_mod.split_title(
            _plain_text(caption), title=str(meta.get("title") or "")
        )
        tags = aparat_mod.clean_tags(
            list(meta.get("tags") or []) + list(self.credentials.tags or ())
        )
        for fallback in aparat_mod.DEFAULT_TAGS:
            if len(tags) >= aparat_mod.TAG_MINIMUM:
                break
            if fallback not in tags:
                tags.append(fallback)
        try:
            result = self.client.publish(
                data,
                filename=filename,
                title=title,
                description=description,
                tags=tags,
                duration=meta.get("duration", ""),
                thumbnail=meta.get("thumbnail") or b"",
                thumbnail_filename=str(meta.get("thumbnail_filename") or ""),
            )
        except aparat_mod.AparatError as error:
            raise ChannelError(f"{self.label}: {error}") from error
        self.last_url = result.url
        self.last_remote_id = result.url or result.hash or result.upload_id


def build_channels(settings, accounts=None) -> dict[str, ChatChannel]:
    """Return every configured automatic channel keyed by its draft button.

    ``accounts`` holds the social accounts of the deployment; each configured
    LinkedIn account adds its own channel (``linkedin_personal``,
    ``linkedin_locallab``), so one draft can reach several destinations.
    """
    channels: dict[str, ChatChannel] = {}
    bale = TelegramLikeChannel(
        "bale", "Bale", settings.bale_token, settings.bale_chat_id, settings.bale_api_base
    )
    if bale.configured:
        channels[bale.key] = bale
    eitaa = EitaaChannel(
        "eitaa", "Eitaa", settings.eitaa_token, settings.eitaa_chat_id, settings.eitaa_api_base
    )
    if eitaa.configured:
        channels[eitaa.key] = eitaa
    aparat = AparatChannel(
        aparat_mod.AparatCredentials(
            token=settings.aparat_token,
            cookie=settings.aparat_cookie,
            api_base=settings.aparat_api_base,
            category=settings.aparat_category,
            tags=tuple(settings.aparat_tags or ()),
            watermark=settings.aparat_watermark,
            video_pass=settings.aparat_video_pass,
            label=settings.aparat_label,
        ),
        timeout=settings.aparat_timeout,
        chunk_bytes=settings.aparat_chunk_bytes,
    )
    if aparat.configured:
        channels[aparat.key] = aparat
    channels.update(build_linkedin_channels(settings, accounts))
    return channels


class LinkedInChannel(ChatChannel):
    """LinkedIn posts for one configured account (person or organization)."""

    def __init__(self, account, settings):
        super().__init__(
            account.target,
            account.display,
            account.access_token,
            account.author,
            settings.linkedin_api_base,
        )
        self.account = account
        self.publisher = linkedin_mod.LinkedInPublisher(
            api_base=settings.linkedin_api_base,
            api_version=settings.linkedin_api_version,
            timeout=settings.linkedin_timeout,
            retries=settings.linkedin_retries,
        )
        self.last_remote_id = ""

    @property
    def configured(self) -> bool:
        """True when the account has both a token and an author URN."""
        return bool(self.account.access_token and self.account.author)

    def _publish_text(self, text: str) -> None:
        commentary = linkedin_mod.build_commentary("", _plain_text(text))
        if not commentary:
            raise ChannelError(f"{self.label}: nothing to publish")
        try:
            self.last_remote_id = self.publisher.publish_text(commentary, self.account)
        except linkedin_mod.LinkedInError as error:
            raise ChannelError(f"{self.label}: {error}") from error

    def send_text(self, text: str) -> None:
        self._publish_text(text)

    def send_photo(self, filename: str, data: bytes, caption: str) -> None:
        commentary = linkedin_mod.build_commentary("", _plain_text(caption))
        try:
            self.last_remote_id = self.publisher.publish_image(
                data,
                commentary,
                self.account,
                alt_text=str(filename or ""),
            )
        except linkedin_mod.LinkedInError as error:
            raise ChannelError(f"{self.label}: {error}") from error

    def send_video(
        self,
        filename: str,
        data: bytes,
        caption: str,
        *,
        meta: dict | None = None,
    ) -> None:
        raise ChannelError(
            f"{self.label}: LinkedIn video uploads are not supported; "
            "publish the text and attach the video manually"
        )

    def send_document(self, filename: str, data: bytes, caption: str) -> None:
        raise ChannelError(
            f"{self.label}: LinkedIn document posts are not supported by this adapter"
        )


def build_linkedin_channels(settings, accounts) -> dict[str, LinkedInChannel]:
    """Return one channel per configured LinkedIn account."""
    channels: dict[str, LinkedInChannel] = {}
    for account in (accounts or {}).values():
        if getattr(account, "platform", "") != "linkedin":
            continue
        channel = LinkedInChannel(account, settings)
        if channel.configured:
            channels[channel.key] = channel
    return channels
