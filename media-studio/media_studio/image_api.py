"""Shared client for the OpenAI-compatible image endpoint.

Both the ``api-image`` driver and the timeline renderer need the same call:
one ``POST {base}/images/generations`` with a prompt, decoded into bytes.
Keeping it in one module means a provider quirk is fixed once, and the scene
images inside a video come from exactly the same endpoint as a standalone
image job.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

#: Portrait size that matches the 9:16 render target closely enough to crop
#: without losing the subject. Providers reject arbitrary sizes, so the value
#: stays on the documented OpenAI ladder.
DEFAULT_SCENE_SIZE = "1024x1792"


class ImageApiError(RuntimeError):
    """Raised when the image endpoint cannot return a usable image."""

    def __init__(self, message: str, *, status: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


def ext_from_bytes(raw: bytes, fallback: str = "png") -> str:
    """Return the file extension implied by the magic bytes of one image."""
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:2] == b"BM":
        return "bmp"
    return fallback


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _download(url: str, api_key: str, timeout: int) -> bytes:
    headers = {}
    if api_key and url.startswith("/"):
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - provider-returned URL
        return response.read()


def generate_images(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    count: int = 1,
    size: str = DEFAULT_SCENE_SIZE,
    timeout: int = 300,
) -> list[bytes]:
    """Return the decoded images the endpoint produced for one prompt.

    Raises ``ImageApiError`` with the provider detail attached so callers can
    turn it into a driver error or downgrade to a generated card.
    """
    endpoint = f"{(base_url or '').rstrip('/')}/images/generations"
    payload = {
        "model": model,
        "prompt": prompt,
        "n": max(1, min(int(count or 1), 4)),
        "size": size,
        "response_format": "b64_json",
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers=_headers(api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is operator-configured
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except OSError:
            pass
        raise ImageApiError(
            f"image API returned HTTP {exc.code} for model {model or '<unset>'}",
            status=exc.code,
            detail=detail,
        ) from exc
    except urllib.error.URLError as exc:
        raise ImageApiError(
            f"image API is unreachable at {endpoint}: {exc.reason}",
            detail=str(exc.reason),
        ) from exc
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
        items = body.get("data") or []
    except ValueError as exc:
        raise ImageApiError("image API returned non-JSON output") from exc
    if not items:
        raise ImageApiError(
            "image API returned no images",
            detail=raw.decode("utf-8", "replace")[:300],
        )
    images: list[bytes] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        encoded = item.get("b64_json")
        url = item.get("url")
        if encoded:
            try:
                images.append(base64.b64decode(encoded))
            except ValueError as exc:
                raise ImageApiError(f"image {index} is not valid base64") from exc
        elif url:
            try:
                images.append(_download(str(url), api_key, timeout))
            except OSError as exc:
                raise ImageApiError(f"image {index} could not be downloaded: {exc}") from exc
    if not images:
        raise ImageApiError("image API returned no decodable image")
    return images
