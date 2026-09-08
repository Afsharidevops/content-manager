"""API image driver: OpenAI-compatible /images/generations with no browser.

Works with any gateway that implements the OpenAI images API (including
router-style providers), so Media Studio stays usable without a Google
subscription or a browser session.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from media_studio.drivers.base import Driver, DriverError, RunContext


def _ext_from_bytes(raw: bytes, fallback: str = "png") -> str:
    if raw[:3] == b"\xff\xd8\xff":
        return "jpg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[:2] == b"BM":
        return "bmp"
    return fallback


class ApiImageDriver(Driver):
    name = "api-image"
    label = "Image generation through an OpenAI-compatible API"
    group = "api"

    def run(self, ctx: RunContext) -> list[tuple[str, str]]:
        settings = ctx.settings
        base_url = (getattr(settings, "writer_base_url", "") or "").rstrip("/")
        api_key = getattr(settings, "writer_api_key", "") or ""
        model = getattr(settings, "writer_model", "") or ""
        if not base_url:
            raise DriverError(
                "No writer API endpoint is configured.",
                step="config",
                hint="Set MEDIA_STUDIO_WRITER_BASE_URL (or reuse CONTENT_WRITER_BASE_URL) before submitting api-image jobs.",
            )
        endpoint = f"{base_url}/images/generations"
        count = int(ctx.params.get("count", 1))
        count = max(1, min(count, 4))
        size = str(ctx.params.get("size") or getattr(settings, "image_size", "1024x1024"))
        payload: dict = {
            "model": model,
            "prompt": ctx.prompt,
            "n": count,
            "size": size,
            "response_format": "b64_json",
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        timeout = getattr(settings, "job_timeout_seconds", 900) or 300
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is operator-configured
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except OSError:
                pass
            raise DriverError(
                f"Image API returned HTTP {exc.code} for model {model or '<unset>'}.",
                step="request",
                hint=f"Provider response: {detail}",
            ) from exc
        except urllib.error.URLError as exc:
            raise DriverError(
                f"Image API is unreachable at {endpoint}: {exc.reason}.",
                step="request",
                hint="Check network access and that the base URL includes /v1 when required.",
            ) from exc
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
            items = body.get("data") or []
        except ValueError as exc:
            raise DriverError("Image API returned non-JSON output.", step="response") from exc
        if not items:
            raise DriverError(
                "Image API returned no images.",
                step="response",
                hint=f"Provider response: {raw.decode('utf-8', 'replace')[:300]}",
            )
        artifacts: list[tuple[str, str]] = []
        for index, item in enumerate(items, start=1):
            encoded = item.get("b64_json") if isinstance(item, dict) else None
            url = item.get("url") if isinstance(item, dict) else None
            if encoded:
                try:
                    content = base64.b64decode(encoded)
                except ValueError as exc:
                    raise DriverError(f"Image {index} is not valid base64.", step="response") from exc
            elif url:
                content = self._download(url, api_key, timeout)
            else:
                continue
            filename = f"image_{index}.{_ext_from_bytes(content)}"
            with open(os.path.join(ctx.work_dir, filename), "wb") as handle:
                handle.write(content)
            artifacts.append((filename, "image"))
            ctx.log(f"api-image: saved {filename} ({len(content)} bytes).")
        if not artifacts:
            raise DriverError("Image API returned no decodable image.", step="response")
        return artifacts

    @staticmethod
    def _download(url: str, api_key: str, timeout: int) -> bytes:
        headers = {}
        if api_key and url.startswith("/"):
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - provider-returned URL
            return response.read()
