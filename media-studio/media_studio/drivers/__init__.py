"""Driver registry. Importing this module never loads Playwright."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DriverMeta:
    name: str
    label: str
    group: str  # "api" (HTTP only) or "google" (needs a signed-in Google session)
    target_url: str = ""
    needs_browser: bool = False


DRIVERS: dict[str, DriverMeta] = {
    "api-image": DriverMeta(
        name="api-image",
        label="Image generation through an OpenAI-compatible API",
        group="api",
    ),
    "video-edit": DriverMeta(
        name="video-edit",
        label="Normalise an operator-uploaded video with ffmpeg",
        group="api",
    ),
    "flow-video": DriverMeta(
        name="flow-video",
        label="Google Flow video generation (Flow account session)",
        group="google",
        target_url="https://flow.google.com/",
        needs_browser=True,
    ),
    "gemini-image": DriverMeta(
        name="gemini-image",
        label="Gemini image generation in the Gemini web app",
        group="google",
        target_url="https://gemini.google.com/app",
        needs_browser=True,
    ),
}

PROBE = "probe"


def meta_for(name: str) -> DriverMeta | None:
    return DRIVERS.get(name)
