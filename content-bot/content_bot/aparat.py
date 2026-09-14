"""Aparat video publishing through the Aparat web upload API.

Aparat has no self-service OAuth registration for video uploads, so the bot
speaks the same API the Aparat uploader itself uses. The operator signs in once
in a browser and hands the session over (the ``jwt`` value the site keeps in
local storage, or the whole ``Cookie`` header), and every publish then runs in
four steps:

1. ``GET  /api/fa/v1/video/upload/upload_config`` asks for an upload server.
2. ``POST /api/fa/v1/video/upload/upload_url`` reserves one resumable upload
   and returns its upload id and token.
3. the video travels to the upload server in chunks (``POST /upload`` with an
   ``X-Token`` header, then ``POST /chunksdone``).
4. ``POST /api/fa/v1/video/upload/upload/uploadId/<id>`` submits the title,
   description, tags, and category that turn the upload into a video.

A chunk that the upload server refuses is sent again with a growing backoff
before the upload is reported as failed, and the metadata call of step 4
mirrors the Aparat web uploader field by field (its ``duration`` and
``thumbnail`` are optional). Everything here is video-only: Aparat has no text
or image post type, so the bot only offers the platform when a video is
attached to the draft.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlencode

from content_bot.http import request_bytes, request_multipart

log = logging.getLogger("content_bot.aparat")

DEFAULT_API_BASE = "https://www.aparat.com"
API_PREFIX = "/api/fa/v1"
DEFAULT_CATEGORY = "10"
DEFAULT_CHUNK_BYTES = 3 * 1024 * 1024
DEFAULT_TIMEOUT = 120
# One failed chunk is retried this many times before the upload is reported as
# failed; the delay doubles after every attempt and stops at the maximum.
DEFAULT_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 8.0
RETRY_STATUSES = frozenset({408, 425, 429})
TITLE_MAX = 100
DESCRIPTION_MAX = 4000
TAG_LIMIT = 5
TAG_MINIMUM = 3
# Filler tags, used only when a draft and the configuration together carry
# fewer than TAG_MINIMUM tags and Aparat would reject the upload.
DEFAULT_TAGS = ("technology", "video", "tutorial")
# `copy(...)` answers with the text "undefined" when the console is used
# without reading the value first, and a printed value keeps its quotes when
# it is pasted back. Both are dropped so a stored placeholder reads as "no
# session" instead of a puzzling 401.
PLACEHOLDER_SESSIONS = frozenset({"undefined", "null", "none", "nan"})
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Categories published by /etc/api/categories; a policy or environment value
# wins over the default.
CATEGORIES = {
    "2": "Comedy",
    "3": "Education and learning",
    "4": "Games and entertainment",
    "5": "Movies, series, and documentaries",
    "6": "Religious",
    "7": "Music",
    "8": "News",
    "9": "Law and politics",
    "10": "Technology and computers",
    "11": "Sports",
    "12": "Accidents",
    "13": "Travel",
    "14": "Animals",
    "15": "Miscellaneous",
    "16": "Business",
    "17": "Culture and art",
    "18": "Cartoon and animation",
    "20": "Style and fashion",
    "21": "Health and fitness",
    "22": "Video games",
    "24": "Cars and vehicles",
    "25": "Kids and family",
    "26": "Home and life",
    "27": "Environment",
    "28": "Finance and economy",
    "29": "Society",
    "30": "Basic sciences",
    "31": "Agriculture",
}


def clean_session(value: str) -> str:
    """Return one stored session value without quotes or placeholders."""
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return "" if text.lower() in PLACEHOLDER_SESSIONS else text


class AparatError(RuntimeError):
    """Raised when Aparat refuses one step of an upload."""


def error_detail(body: bytes) -> str:
    """Return the human-readable part of one Aparat error response."""
    try:
        payload = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return " ".join(body.decode("utf-8", "replace").split())[:200]
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("detail") or first.get("title") or "").strip()
        return str(first).strip()
    if isinstance(errors, dict):
        for name, value in errors.items():
            if isinstance(value, list) and value:
                return f"{name}: {value[0]}".strip()
            if value:
                return f"{name}: {value}".strip()
    return str(payload.get("message") or payload.get("detail") or "").strip()


def find_hash(payload) -> str:
    """Return the first video hash inside an Aparat upload response, if any."""
    wanted = ("videohash", "hash_id", "hash", "uid", "videouid")

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key or "").lower() in wanted and isinstance(value, (str, int)):
                    text = str(value).strip()
                    if text:
                        return text
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = walk(item)
                if found:
                    return found
        return ""

    return walk(payload)


def video_url(hash_value: str) -> str:
    """Return the public watch URL for one video hash."""
    value = str(hash_value or "").strip()
    return f"https://www.aparat.com/v/{value}" if value else ""


def clean_tags(values) -> list[str]:
    """Return up to ``TAG_LIMIT`` unique Aparat tags without their hashes."""
    tags: list[str] = []
    for value in values or ():
        tag = " ".join(str(value or "").split()).lstrip("#").strip()
        tag = tag.replace("-", " ").strip()
        if not tag or tag in tags:
            continue
        tags.append(tag)
        if len(tags) >= TAG_LIMIT:
            break
    return tags


def split_title(text: str, *, title: str = "", limit: int = TITLE_MAX) -> tuple[str, str]:
    """Return the Aparat title and description for one caption.

    The draft title wins; without one the first non-empty line of the caption
    becomes the title. The title is cut to the Aparat limit and the whole
    caption is returned unchanged as the description so nothing is lost.
    """
    body = str(text or "").strip()
    candidate = " ".join(str(title or "").split())
    if not candidate:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                candidate = " ".join(stripped.split())
                break
    if len(candidate) > limit:
        candidate = candidate[: max(limit - 3, 1)].rstrip() + "..."
    return candidate, body


def flag_enabled(value) -> bool:
    """Return True for the truthy flag spellings the stack accepts."""
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def flag_number(value) -> int:
    """Return one flag value as the ``0``/``1`` Aparat stores."""
    return 1 if flag_enabled(value) else 0


def normalize_duration(value) -> float | int:
    """Return the seconds of one duration value, or ``0`` when it is unknown.

    A caller can pass the seconds as a number or as the text a policy file
    holds (``"10"``). A missing, unreadable, or non-positive value reads as
    ``0`` so the metadata call leaves the field out and Aparat derives the
    duration from the file it just received.
    """
    if isinstance(value, bool) or value is None:
        return 0
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        return 0
    return int(seconds) if float(seconds).is_integer() else round(seconds, 3)


def thumbnail_data_url(image, filename: str = "") -> str:
    """Return the ``data:`` URL Aparat stores as the thumbnail of a video.

    ``image`` is the raw bytes of an image file (``filename`` then decides the
    media type, JPEG by default) or an already encoded ``data:image/...``
    string, which passes through unchanged. An empty or unusable value returns
    an empty string, and the metadata call then lets Aparat pick a frame.
    """
    if isinstance(image, str):
        text = image.strip()
        if text.startswith("data:image/"):
            return text
        if text:
            log.warning("aparat thumbnail ignored: not a data: image URL")
        return ""
    blob = bytes(image or b"")
    if not blob:
        return ""
    media_type = mimetypes.guess_type(str(filename or ""))[0] or ""
    if not media_type.startswith("image/"):
        media_type = "image/jpeg"
    return f"data:{media_type};base64,{base64.b64encode(blob).decode('ascii')}"


@dataclass(frozen=True)
class AparatCredentials:
    """One Aparat channel: the browser session plus the channel defaults."""

    token: str = ""
    cookie: str = ""
    api_base: str = DEFAULT_API_BASE
    category: str = DEFAULT_CATEGORY
    tags: tuple[str, ...] = ()
    watermark: str = "1"
    video_pass: str = "0"
    comment: str = "yes"
    kids_friendly: bool = False
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "token", clean_session(self.token))
        object.__setattr__(self, "cookie", clean_session(self.cookie))

    @property
    def configured(self) -> bool:
        """True when a browser session is stored."""
        return bool(self.token or self.cookie)

    @property
    def display(self) -> str:
        return self.label or "Aparat"


@dataclass
class UploadResult:
    """What one finished Aparat upload reports back."""

    upload_id: str = ""
    video: str = ""
    hash: str = ""
    response: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return video_url(self.hash)


class AparatClient:
    """Small client for the Aparat upload endpoints."""

    def __init__(
        self,
        credentials: AparatCredentials,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        retries: int = DEFAULT_RETRIES,
        agent: str = USER_AGENT,
        sleep=time.sleep,
    ):
        self.credentials = credentials
        self.timeout = max(int(timeout or DEFAULT_TIMEOUT), 10)
        self.chunk_bytes = max(int(chunk_bytes or DEFAULT_CHUNK_BYTES), 1)
        self.retries = max(int(DEFAULT_RETRIES if retries is None else retries), 0)
        self.agent = agent
        self.sleep = sleep or time.sleep
        self.api_base = str(credentials.api_base or DEFAULT_API_BASE).rstrip("/")

    # ------------------------------------------------------------- sessions

    def headers(self, *, json_body: bool = False, token: str = "") -> dict:
        """Return the request headers for one Aparat call."""
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fa,en;q=0.8",
            "User-Agent": self.agent,
            "Origin": self.api_base,
            "Referer": f"{self.api_base}/upload",
        }
        if json_body:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if self.credentials.token:
            headers["Authorization"] = f"Bearer {self.credentials.token}"
        if self.credentials.cookie:
            headers["Cookie"] = self.credentials.cookie
        if token:
            headers["X-Token"] = token
        return headers

    def metadata_headers(self) -> dict:
        """Return the headers the Aparat uploader sends with the metadata call."""
        headers = self.headers(json_body=True)
        headers.update(
            {
                "isNext": "true",
                "jsonType": "simple",
                "domain": "aparat",
                "currentUrl": f"{self.api_base}/reactupload",
                "isRedesign": "true",
            }
        )
        return headers

    def _url(self, path: str) -> str:
        return f"{self.api_base}{API_PREFIX}{path}"

    def _fail(self, step: str, status: int, body: bytes) -> "AparatError":
        detail = error_detail(body)
        if status in {401, 403} and not self.credentials.configured:
            return AparatError("Aparat session is not configured")
        if status in {401, 403}:
            note = (
                "Aparat refused the stored session; sign in again and refresh "
                "CONTENT_APARAT_TOKEN or CONTENT_APARAT_COOKIE"
            )
        else:
            note = f"Aparat {step} failed (HTTP {status})"
        return AparatError(f"{note}: {detail}" if detail else note)

    def _json(
        self,
        step: str,
        method: str,
        url: str,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        """Call one JSON endpoint and return the parsed body."""
        try:
            status, body = request_bytes(
                url,
                method=method,
                headers=headers
                if headers is not None
                else self.headers(json_body=payload is not None),
                payload=payload,
                timeout=self.timeout,
                max_bytes=1_000_000,
            )
        except ConnectionError as error:
            log.warning("aparat %s could not be reached: %s", step, error)
            raise AparatError(f"Aparat {step} could not be reached: {error}") from error
        if status >= 400:
            log.warning("aparat %s failed with HTTP %s", step, status)
            raise self._fail(step, status, body)
        log.debug("aparat %s answered HTTP %s (%d bytes)", step, status, len(body or b""))
        try:
            parsed = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError as error:
            raise AparatError(f"Aparat {step} returned no JSON") from error
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _retryable(status: int) -> bool:
        """True when one failed upload attempt is worth repeating."""
        return status == 0 or status >= 500 or status in RETRY_STATUSES

    def _retry_pause(self, attempt: int) -> float:
        """Return the backoff before retry number ``attempt``."""
        return min(RETRY_DELAY_SECONDS * (2 ** (attempt - 1)), RETRY_MAX_DELAY_SECONDS)

    def _with_retries(self, step: str, call):
        """Run one upload-server call, retrying the attempts worth repeating.

        ``call`` returns ``(status, body)`` and may raise ``ConnectionError``.
        A refused chunk is uploaded again with an exponential backoff, while a
        rejection that repeats itself (a refused session, a bad request) fails
        on the first answer.
        """
        attempts = self.retries + 1
        last = ""
        for attempt in range(1, attempts + 1):
            try:
                status, body = call()
            except ConnectionError as error:
                status, body = 0, b""
                last = f"Aparat {step} could not be reached: {error}"
            else:
                if status < 400:
                    return status, body
                detail = error_detail(body)
                last = f"Aparat {step} failed (HTTP {status})"
                if detail:
                    last = f"{last}: {detail}"
                if not self._retryable(status):
                    raise AparatError(last)
            if attempt >= attempts:
                raise AparatError(last)
            pause = self._retry_pause(attempt)
            log.warning(
                "aparat %s failed, retrying in %.0fs (attempt %d/%d): %s",
                step,
                pause,
                attempt,
                attempts,
                last,
            )
            self.sleep(pause)
        raise AparatError(last)

    # ------------------------------------------------------------- upload

    def upload_config(self) -> dict:
        """Return the upload configuration, including the upload server."""
        payload = self._json(
            "upload config", "GET", self._url("/video/upload/upload_config")
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("server"):
            raise AparatError(
                "Aparat did not return an upload server; the session is not a "
                "signed-in account"
            )
        return data

    def reserve(self, upload_id: str, server: str) -> dict:
        """Reserve one resumable upload and return its token and id."""
        payload = self._json(
            "upload reservation",
            "POST",
            self._url("/video/upload/upload_url"),
            {
                "uploadIds": [upload_id],
                "upload_base_url": server,
                "upload_cnt": 1,
            },
        )
        rows = payload.get("data")
        attributes = {}
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            attributes = rows[0].get("attributes") or {}
        if not isinstance(attributes, dict) or not attributes.get("token"):
            raise AparatError("Aparat did not reserve an upload slot")
        merged = dict(attributes)
        merged.setdefault("uploadId", upload_id)
        return merged

    def send_file(
        self,
        data: bytes,
        *,
        filename: str,
        server: str,
        token: str,
        upload_id: str,
        progress=None,
    ) -> None:
        """Push one video to the upload server in sequential chunks."""
        name = str(filename or "video.mp4")
        total = len(data or b"")
        if total <= 0:
            raise AparatError("The video file is empty")
        chunk_size = min(self.chunk_bytes, max(total, 1))
        parts = max(1, (total + chunk_size - 1) // chunk_size)
        content_type = mimetypes.guess_type(name)[0] or "video/mp4"
        target = f"{server.rstrip('/')}/upload"
        log.info(
            "aparat upload %s: %s, %d bytes in %d chunk(s) of %d bytes",
            upload_id,
            name,
            total,
            parts,
            chunk_size,
        )
        for index in range(parts):
            offset = index * chunk_size
            blob = data[offset : offset + chunk_size]
            fields = {
                "qqchunksize": len(blob),
                "qqtotalfilesize": total,
                "qqtype": content_type,
                "qquuid": upload_id,
                "qqfilename": name,
                "qqfilepath": name,
                "qqtotalparts": parts,
                "qqpartindex": index,
                "qqpartbyteoffset": offset,
            }
            self._with_retries(
                f"chunk {index + 1}/{parts}",
                lambda: request_multipart(
                    target,
                    fields=fields,
                    file_field="qqfile",
                    filename=name,
                    file_bytes=blob,
                    headers=self.headers(token=token),
                    timeout=self.timeout,
                    max_bytes=1_000_000,
                ),
            )
            log.debug(
                "aparat chunk %d/%d sent (%d bytes at offset %d)",
                index + 1,
                parts,
                len(blob),
                offset,
            )
            if progress is not None:
                progress(index + 1, parts)
        try:
            request_bytes(
                f"{server.rstrip('/')}/file/{upload_id}",
                headers=self.headers(token=token),
                timeout=self.timeout,
                max_bytes=200_000,
            )
        except ConnectionError:
            pass
        payload = {
            "qquuid": upload_id,
            "qqfilename": name,
            "qqtotalfilesize": total,
            "qqtotalparts": parts,
        }
        self._with_retries(
            "upload close",
            lambda: request_bytes(
                f"{server.rstrip('/')}/chunksdone",
                headers=self.headers(token=token),
                raw_body=urlencode(payload).encode("utf-8"),
                content_type="application/x-www-form-urlencoded",
                timeout=self.timeout,
                max_bytes=1_000_000,
            ),
        )
        log.info("aparat upload %s closed", upload_id)

    def submit(
        self,
        *,
        upload_id: str,
        title: str,
        description: str,
        tags: list[str],
        category: str = "",
        video_pass: str = "",
        duration="",
        thumbnail: str = "",
    ) -> dict:
        """Send the metadata that publishes the finished upload.

        The body and the headers mirror the request the Aparat uploader itself
        makes, so a change on the site side can be compared field by field.
        ``duration`` and ``thumbnail`` are optional: without them Aparat reads
        both from the file it just received.
        """
        credentials = self.credentials
        payload = {
            "video_pass": flag_number(video_pass or credentials.video_pass),
            "watermark": str(credentials.watermark),
            "watermark_bool": flag_enabled(credentials.watermark),
            "category": str(category or credentials.category or DEFAULT_CATEGORY),
            "comment": str(credentials.comment or "yes"),
            "kids_friendly": bool(credentials.kids_friendly),
            "title": title,
            "descr": description,
            "tags": "-".join(tags),
            "subtitle": [],
            "publish_date": None,
        }
        seconds = normalize_duration(duration)
        if seconds:
            payload["duration"] = seconds
        if thumbnail:
            payload["thumbnail"] = thumbnail
        response = self._json(
            "metadata",
            "POST",
            self._url(f"/video/upload/upload/uploadId/{upload_id}"),
            payload,
            headers=self.metadata_headers(),
        )
        if response.get("errors"):
            detail = error_detail(json.dumps(response, ensure_ascii=False).encode("utf-8"))
            log.warning("aparat metadata refused for %s: %s", upload_id, detail)
            raise AparatError(
                "Aparat refused the upload metadata"
                + (f": {detail}" if detail else "")
            )
        log.debug(
            "aparat metadata accepted for %s: keys=%s",
            upload_id,
            sorted(str(key) for key in response),
        )
        return response

    def publish(
        self,
        data: bytes,
        *,
        filename: str,
        title: str,
        description: str,
        tags=(),
        category: str = "",
        duration="",
        thumbnail=b"",
        thumbnail_filename: str = "",
        progress=None,
    ) -> UploadResult:
        """Upload one video and publish it on the channel.

        The arguments are the whole upload contract: ``data`` is the file
        itself with ``filename`` beside it, ``title``/``description``/``tags``
        and ``category`` become the metadata, and ``duration`` (seconds) plus
        ``thumbnail`` (image bytes or a ``data:image/...`` URL) are attached
        when the caller has them. ``progress(done, total)`` is called after
        every uploaded chunk.
        """
        if not self.credentials.configured:
            raise AparatError("Aparat session is not configured")
        video = uuid.uuid4().hex
        thumbnail_url = thumbnail_data_url(thumbnail, thumbnail_filename)
        server = str(self.upload_config().get("server") or "").rstrip("/")
        reserved = self.reserve(video, server)
        token = str(reserved.get("token") or "")
        upload_id = str(reserved.get("uploadId") or video)
        self.send_file(
            data,
            filename=filename,
            server=server,
            token=token,
            upload_id=video,
            progress=progress,
        )
        response = self.submit(
            upload_id=upload_id,
            title=title,
            description=description,
            tags=list(tags),
            category=category,
            duration=duration,
            thumbnail=thumbnail_url,
        )
        result = UploadResult(
            upload_id=upload_id,
            video=video,
            hash=find_hash(response),
            response=response,
        )
        log.info(
            "aparat published %s: hash=%s url=%s",
            upload_id,
            result.hash or "-",
            result.url or "-",
        )
        return result

    def probe(self) -> str:
        """Return the upload server of a working session, for the console."""
        return str(self.upload_config().get("server") or "")


def client_from_settings(settings) -> AparatClient:
    """Build the Aparat client out of the bot settings."""
    credentials = AparatCredentials(
        token=getattr(settings, "aparat_token", ""),
        cookie=getattr(settings, "aparat_cookie", ""),
        api_base=getattr(settings, "aparat_api_base", "") or DEFAULT_API_BASE,
        category=getattr(settings, "aparat_category", "") or DEFAULT_CATEGORY,
        tags=tuple(getattr(settings, "aparat_tags", ()) or ()),
        watermark=getattr(settings, "aparat_watermark", "1"),
        video_pass=getattr(settings, "aparat_video_pass", "0"),
        label=getattr(settings, "aparat_label", ""),
    )
    return AparatClient(
        credentials,
        timeout=int(getattr(settings, "aparat_timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT),
        chunk_bytes=int(
            getattr(settings, "aparat_chunk_bytes", DEFAULT_CHUNK_BYTES) or DEFAULT_CHUNK_BYTES
        ),
    )
