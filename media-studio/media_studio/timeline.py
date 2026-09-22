"""Timeline JSON: the render contract between planning agents and Media Studio.

A planning agent (Video Director) turns a script into a timeline document;
the renderer only ever consumes that document, never a raw script. The schema
stays small on purpose so it can be validated, stored in a job record, and
edited by hand in the operator panel:

    {
      "version": 1,
      "meta": {
        "title": "...",
        "aspect_ratio": "9:16",
        "resolution": "1080x1920",
        "fps": 30,
        "subtitle": true,
        "brand": {"label": "...", "position": "bottom-right", "style": "aurora"}
      },
      "audio": {"upload_id": "...", "file_name": "...", "volume": 1.0},
      "scenes": [
        {
          "id": 1,
          "duration": 4.5,
          "narration": "...",
          "visual": "...",
          "asset_type": "text",
          "upload_id": "...",
          "asset_path": "",
          "transition": "fade",
          "animation": "zoom-in",
          "emotion": "neutral"
        }
      ]
    }

``normalize_timeline`` validates and clamps every field, raises
``TimelineError`` with an operator-readable message, and returns a plain dict
with defaults filled in. Nothing in this module imports Playwright or PIL.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = 1

#: Aspect presets mapped to (width, height). 9:16 targets Shorts, Reels and
#: TikTok; 16:9 targets YouTube; 1:1 and 4:5 target feed posts.
ASPECT_PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}

DEFAULT_ASPECT = "9:16"
DEFAULT_FPS = 30
MIN_SCENE_SECONDS = 0.5
MAX_SCENE_SECONDS = 120.0
MAX_SCENES = 120
DEFAULT_TRANSITION_SECONDS = 0.5
MAX_EMOTIONS = 12

ASSET_TYPES = ("auto", "text", "solid", "image", "video")
ANIMATIONS = ("none", "zoom-in", "zoom-out", "pan-left", "pan-right")
TRANSITIONS = (
    "cut",
    "fade",
    "dissolve",
    "slideleft",
    "slideright",
    "wipeleft",
    "wipeup",
    "circleopen",
)
#: Timeline transition names mapped to ffmpeg xfade transition names.
XFADE_NAMES = {
    "fade": "fade",
    "dissolve": "dissolve",
    "slideleft": "slideleft",
    "slideright": "slideright",
    "wipeleft": "wipeleft",
    "wipeup": "wipeup",
    "circleopen": "circleopen",
}

_RTL_RE = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")
_RESOLUTION_RE = re.compile(r"^(\d{2,5})x(\d{2,5})$")


class TimelineError(ValueError):
    """Raised when a timeline document cannot be rendered."""

    def __init__(self, message: str, *, field: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.field = field
        self.hint = hint

    def describe(self) -> str:
        parts = []
        if self.field:
            parts.append(f"field={self.field}")
        parts.append(str(self))
        if self.hint:
            parts.append(f"hint: {self.hint}")
        return " | ".join(parts)


def contains_rtl(text: str) -> bool:
    """Return True when the text holds Arabic-script (RTL) characters."""
    return bool(_RTL_RE.search(str(text or "")))


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    return text[:limit]


def _pick(value: Any, choices: tuple[str, ...], default: str) -> str:
    chosen = str(value or "").strip().lower()
    return chosen if chosen in choices else default


def resolve_resolution(meta: dict) -> tuple[int, int]:
    """Return the render resolution from meta resolution or aspect ratio."""
    raw = str(meta.get("resolution") or "").strip().lower()
    match = _RESOLUTION_RE.fullmatch(raw)
    if match:
        width = _as_int(match.group(1), 0)
        height = _as_int(match.group(2), 0)
        if width >= 128 and height >= 128:
            return width, height
    aspect = str(meta.get("aspect_ratio") or DEFAULT_ASPECT).strip()
    return ASPECT_PRESETS.get(aspect, ASPECT_PRESETS[DEFAULT_ASPECT])


def normalize_timeline(raw: Any) -> dict:
    """Validate one timeline document and fill every default in.

    Raises ``TimelineError`` when the document cannot produce a video.
    """
    if not isinstance(raw, dict):
        raise TimelineError(
            "the timeline must be a JSON object",
            field="timeline",
            hint='Provide {"version": 1, "scenes": [...]}.',
        )
    version = _as_int(raw.get("version"), 0)
    if version and version > SCHEMA_VERSION:
        raise TimelineError(
            f"timeline version {version} is newer than this renderer supports ({SCHEMA_VERSION})",
            field="version",
            hint="Upgrade Media Studio or re-export the timeline with a supported version.",
        )
    raw_meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    aspect = str(raw_meta.get("aspect_ratio") or DEFAULT_ASPECT).strip()
    if aspect not in ASPECT_PRESETS and not _RESOLUTION_RE.fullmatch(
        str(raw_meta.get("resolution") or "").strip().lower()
    ):
        aspect = DEFAULT_ASPECT
    width, height = resolve_resolution({**raw_meta, "aspect_ratio": aspect})
    fps = _as_int(raw_meta.get("fps"), DEFAULT_FPS)
    if fps not in (24, 25, 30, 50, 60):
        fps = DEFAULT_FPS
    raw_brand = raw_meta.get("brand") if isinstance(raw_meta.get("brand"), dict) else {}
    meta = {
        "title": _clean_text(raw_meta.get("title"), 300),
        "aspect_ratio": aspect,
        "resolution": f"{width}x{height}",
        "fps": fps,
        "subtitle": bool(raw_meta.get("subtitle", True)),
        "brand": {
            "label": _clean_text(raw_brand.get("label"), 80),
            "position": _pick(
                raw_brand.get("position"),
                ("top-left", "top-right", "bottom-left", "bottom-right"),
                "bottom-right",
            ),
            "style": _pick(raw_brand.get("style"), ("aurora", "chip"), "aurora"),
        },
    }
    audio: dict = {}
    raw_audio = raw.get("audio") if isinstance(raw.get("audio"), dict) else None
    if raw_audio:
        audio = {
            "upload_id": _clean_text(raw_audio.get("upload_id"), 120),
            "asset_path": _clean_text(raw_audio.get("asset_path"), 600),
            "file_name": _clean_text(raw_audio.get("file_name"), 200),
            "volume": _clamp(_as_float(raw_audio.get("volume"), 1.0), 0.0, 4.0),
        }
    raw_scenes = raw.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise TimelineError(
            "the timeline carries no scenes",
            field="scenes",
            hint="A timeline needs at least one scene with a duration.",
        )
    if len(raw_scenes) > MAX_SCENES:
        raise TimelineError(
            f"the timeline has {len(raw_scenes)} scenes; the limit is {MAX_SCENES}",
            field="scenes",
            hint="Split the video into several render jobs.",
        )
    scenes: list[dict] = []
    for index, item in enumerate(raw_scenes, start=1):
        if not isinstance(item, dict):
            raise TimelineError(
                f"scene {index} is not a JSON object",
                field=f"scenes[{index - 1}]",
            )
        duration = _clamp(
            _as_float(item.get("duration"), 0.0),
            MIN_SCENE_SECONDS,
            MAX_SCENE_SECONDS,
        )
        narration = _clean_text(item.get("narration"), 4000)
        visual = _clean_text(item.get("visual"), 1000)
        asset_path = _clean_text(item.get("asset_path"), 600)
        upload_id = _clean_text(item.get("upload_id"), 120)
        asset_type = _pick(item.get("asset_type"), ASSET_TYPES, "auto")
        if asset_type == "auto":
            asset_type = "image" if (asset_path or upload_id) else "text"
        if asset_type in {"image", "video"} and not (asset_path or upload_id):
            asset_type = "text"
        if not narration and not visual and asset_type == "text":
            narration = f"Scene {index}"
        transition = _pick(item.get("transition"), TRANSITIONS, "fade")
        if index == 1:
            transition = "cut"
        scenes.append(
            {
                "id": _as_int(item.get("id"), index) or index,
                "duration": round(duration, 3),
                "narration": narration,
                "visual": visual,
                "asset_type": asset_type,
                "asset_path": asset_path,
                "upload_id": upload_id,
                "transition": transition,
                "animation": _pick(item.get("animation"), ANIMATIONS, "none"),
                "emotion": _clean_text(item.get("emotion"), 60)[:MAX_EMOTIONS * 5],
            }
        )
    total = sum(scene["duration"] for scene in scenes)
    return {
        "version": SCHEMA_VERSION,
        "meta": meta,
        "audio": audio,
        "scenes": scenes,
        "totals": {
            "scenes": len(scenes),
            "duration_seconds": round(total, 3),
            "resolution": meta["resolution"],
            "fps": meta["fps"],
        },
    }


def timeline_from_params(params: Any) -> Any:
    """Return the timeline carried by job params, accepting a JSON string."""
    if not isinstance(params, dict):
        return None
    timeline = params.get("timeline")
    if isinstance(timeline, str):
        import json

        try:
            return json.loads(timeline)
        except ValueError:
            raise TimelineError(
                "params.timeline is not valid JSON",
                field="params.timeline",
            ) from None
    return timeline
