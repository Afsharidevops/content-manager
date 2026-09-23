"""API image driver: OpenAI-compatible /images/generations with no browser.

Works with any gateway that implements the OpenAI images API (including
router-style providers), so Media Studio stays usable without a Google
subscription or a browser session.
"""

from __future__ import annotations

import os

from media_studio.drivers.base import Driver, DriverError, RunContext
from media_studio.image_api import ImageApiError, ext_from_bytes, generate_images


def _ext_from_bytes(raw: bytes, fallback: str = "png") -> str:
    """Backwards-compatible alias for the shared magic-byte sniffer."""
    return ext_from_bytes(raw, fallback)


def _failure_hint(status: int, detail: str) -> str:
    """Explain the common ways the image endpoint is misconfigured."""
    note = ""
    if status == 404:
        note = (
            "The endpoint has no images API. Point MEDIA_STUDIO_WRITER_BASE_URL "
            "at an OpenAI-compatible image API - the Smart Router serves chat "
            "completions only - and set MEDIA_STUDIO_WRITER_MODEL to the image "
            "model that endpoint offers."
        )
    elif status in {401, 403}:
        note = (
            "The endpoint refused the credentials. Store the image API key in "
            "MEDIA_STUDIO_WRITER_API_KEY."
        )
    if note:
        return f"{note} Provider response: {detail}"
    return f"Provider response: {detail}"


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
        count = int(ctx.params.get("count", 1))
        count = max(1, min(count, 4))
        size = str(ctx.params.get("size") or getattr(settings, "image_size", "1024x1024"))
        timeout = getattr(settings, "job_timeout_seconds", 900) or 300
        try:
            images = generate_images(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt=ctx.prompt,
                count=count,
                size=size,
                timeout=int(timeout),
            )
        except ImageApiError as error:
            if error.status:
                raise DriverError(
                    f"Image API returned HTTP {error.status} for model {model or '<unset>'}.",
                    step="request",
                    hint=_failure_hint(error.status, error.detail),
                ) from error
            raise DriverError(
                f"{error}.",
                step="request",
                hint="Check network access and that the base URL includes /v1 when required.",
            ) from error
        artifacts: list[tuple[str, str]] = []
        for index, content in enumerate(images, start=1):
            filename = f"image_{index}.{ext_from_bytes(content)}"
            with open(os.path.join(ctx.work_dir, filename), "wb") as handle:
                handle.write(content)
            artifacts.append((filename, "image"))
            ctx.log(f"api-image: saved {filename} ({len(content)} bytes).")
        if not artifacts:
            raise DriverError("Image API returned no decodable image.", step="response")
        return artifacts
