"""LinkedIn REST API publisher.

Posts are created with ``POST /rest/posts``: the payload carries the author
URN (a person or an organization) and the commentary text. Images are uploaded
in two steps first - ``POST /rest/images?action=initializeUpload`` returns an
upload URL and the image URN, the binary goes to that URL, and the post then
references the image URN.

Every call is versioned through the ``LinkedIn-Version`` header, retried on
the transient status codes (429 and 5xx), and reported as a
:class:`LinkedInError` with the provider message when it finally fails.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Mapping

from content_bot.http import request_response

log = logging.getLogger("content_bot")

API_BASE = "https://api.linkedin.com"
DEFAULT_API_VERSION = "202601"
DEFAULT_PROTOCOL_VERSION = "2.0.0"
DEFAULT_TIMEOUT = 60
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2.0
COMMENTARY_MAX = 3000
RETRY_STATUS = frozenset({0, 429, 500, 502, 503, 504})


class LinkedInError(RuntimeError):
    """Raised when LinkedIn refuses a post or the upload cannot complete."""


def _clean(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def build_commentary(title: str, body: str, source_url: str = "") -> str:
    """Build one LinkedIn post text from a draft.

    LinkedIn has no title field, so the title becomes the first line. The
    source link is added once at the end and the whole text is trimmed to the
    platform limit without cutting a word in half.
    """
    title = _clean(title)
    body = _clean(body)
    source_url = str(source_url or "").strip()
    parts: list[str] = []
    if title:
        parts.append(title)
    if body:
        parts.append(body)
    text = "\n\n".join(part for part in parts if part)
    if source_url and source_url not in text:
        text = f"{text}\n\n{source_url}" if text else source_url
    if len(text) <= COMMENTARY_MAX:
        return text
    cut = text[:COMMENTARY_MAX]
    boundary = cut.rfind("\n\n")
    if boundary >= COMMENTARY_MAX // 2:
        cut = cut[:boundary]
    else:
        space = cut.rfind(" ")
        if space > 0:
            cut = cut[:space]
    return cut.rstrip()


def _decode(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class LinkedInPublisher:
    """Create LinkedIn posts for one account at a time."""

    def __init__(
        self,
        *,
        api_base: str = API_BASE,
        api_version: str = DEFAULT_API_VERSION,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        sleep=time.sleep,
    ):
        self.api_base = str(api_base or API_BASE).rstrip("/") or API_BASE
        self.api_version = str(api_version or DEFAULT_API_VERSION)
        self.protocol_version = str(protocol_version or DEFAULT_PROTOCOL_VERSION)
        self.timeout = max(5, int(timeout))
        self.retries = max(1, int(retries))
        self.backoff = max(0.0, float(backoff))
        self.sleep = sleep

    def headers(self, account, content_type: str = "application/json") -> dict:
        """Return the versioned REST headers for one account."""
        headers = {
            "Authorization": f"Bearer {account.access_token}",
            "LinkedIn-Version": self.api_version,
            "X-Restli-Protocol-Version": self.protocol_version,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @staticmethod
    def _error_note(status: int, body: bytes) -> str:
        """One readable line out of a LinkedIn error response."""
        payload = _decode(body)
        message = str(payload.get("message") or "").strip()
        detail = str(payload.get("error_description") or payload.get("serviceErrorCode") or "").strip()
        if status == 0:
            return f"network error: {message or 'request failed'}"
        lines = [f"LinkedIn HTTP {status}"]
        if message:
            lines.append(message)
        if detail and detail != message:
            lines.append(str(detail))
        return ": ".join(lines)

    def _delay(self, attempt: int) -> float:
        """Exponential backoff: 2s, 4s, 8s ... by default."""
        return self.backoff ** max(1, attempt)

    def _call(
        self,
        method: str,
        url: str,
        *,
        account,
        payload: dict | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
    ):
        """Run one request with retries and return the response object."""
        last_note = ""
        for attempt in range(1, self.retries + 1):
            try:
                response = request_response(
                    url,
                    method=method,
                    headers=self.headers(account, content_type or "application/json"),
                    payload=payload,
                    raw_body=raw_body,
                    content_type=None if raw_body is not None else content_type,
                    timeout=self.timeout,
                )
            except ConnectionError as error:
                last_note = f"network error: {error}"
                response = None
            if response is not None:
                if 200 <= response.status < 300:
                    return response
                last_note = self._error_note(response.status, response.body)
                if response.status not in RETRY_STATUS:
                    raise LinkedInError(last_note)
            if attempt < self.retries:
                delay = self._delay(attempt)
                log.warning("linkedin retry %s/%s in %.1fs: %s", attempt, self.retries, delay, last_note)
                self.sleep(delay)
        raise LinkedInError(last_note or "LinkedIn request failed")

    def _post_payload(self, account, commentary: str, image_urn: str = "", alt_text: str = "") -> dict:
        payload = {
            "author": account.author,
            "commentary": commentary,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if image_urn:
            media = {"id": image_urn}
            if alt_text:
                media["altText"] = alt_text[:4086]
            payload["content"] = {"media": media}
        return payload

    @staticmethod
    def _post_urn(response) -> str:
        """Return the created post URN from the headers or the body."""
        headers: Mapping[str, str] = response.headers or {}
        for key, value in headers.items():
            if str(key).lower() == "x-restli-id" and value:
                return str(value)
        return str(_decode(response.body).get("id") or "")

    @staticmethod
    def _ensure_publishable(account) -> None:
        """Fail early when an account cannot be published to."""
        token = str(getattr(account, "access_token", "") or "")
        author = str(getattr(account, "author", "") or "")
        if token and author:
            return
        label = str(getattr(account, "display", "") or getattr(account, "target", "") or "account")
        raise LinkedInError(
            f"{label} is not usable: an access token and an author URN are required"
        )

    def publish_text(self, text: str, account) -> str:
        """Create one text post and return its URN."""
        self._ensure_publishable(account)
        commentary = _clean(text)
        if not commentary:
            raise LinkedInError("refusing to publish an empty post")
        response = self._call(
            "POST",
            f"{self.api_base}/rest/posts",
            account=account,
            payload=self._post_payload(account, commentary),
        )
        urn = self._post_urn(response)
        log.info("linkedin post created for %s: %s", getattr(account, "target", "account"), urn or "(no id)")
        return urn

    def publish_image(
        self, data: bytes, text: str, account, *, alt_text: str = ""
    ) -> str:
        """Upload one image, create the post around it, and return the URN."""
        self._ensure_publishable(account)
        commentary = _clean(text)
        if not data:
            raise LinkedInError("the image file is empty")
        upload_url, image_urn = self._initialize_image(account)
        self._call(
            "PUT",
            upload_url,
            account=account,
            raw_body=data,
            content_type="application/octet-stream",
        )
        response = self._call(
            "POST",
            f"{self.api_base}/rest/posts",
            account=account,
            payload=self._post_payload(account, commentary, image_urn, alt_text),
        )
        urn = self._post_urn(response)
        log.info(
            "linkedin image post created for %s: %s",
            getattr(account, "target", "account"),
            urn or "(no id)",
        )
        return urn

    def _initialize_image(self, account) -> tuple[str, str]:
        """Return ``(upload_url, image_urn)`` for one image upload."""
        response = self._call(
            "POST",
            f"{self.api_base}/rest/images?action=initializeUpload",
            account=account,
            payload={"initializeUploadRequest": {"owner": account.author}},
        )
        value = _decode(response.body).get("value") or {}
        upload_url = str(value.get("uploadUrl") or "")
        image_urn = str(value.get("image") or "")
        if not upload_url or not image_urn:
            raise LinkedInError("LinkedIn did not return an image upload URL")
        return upload_url, image_urn
