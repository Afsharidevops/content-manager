"""Timeline render driver: turn a timeline document into one MP4.

The job carries the timeline in ``params.timeline`` (object or JSON string).
Every scene asset resolves through the same allow-list as operator uploads:
an ``upload_id`` points into the uploads directory, and an absolute
``asset_path`` must live under the uploads directory or the job work
directory. Nothing else is readable, so a timeline can never pull arbitrary
files from the host into a video.

Scenes that carry no asset can be illustrated automatically: the driver asks
the configured image endpoint for one background per scene, writes it into
the job work directory, and hands the local path to the renderer. Generation
is bounded (scene cap, one retry-free attempt per scene) and every failure
falls back to the generated text card, so a flaky image provider can slow a
render down but never break it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from media_studio.drivers.base import Driver, DriverError, RunContext
from media_studio.image_api import (
    DEFAULT_SCENE_SIZE,
    ImageApiError,
    ext_from_bytes,
    generate_images,
)
from media_studio.timeline import TimelineError, normalize_timeline, timeline_from_params
from media_studio.timeline_render import render_timeline
from media_studio.video_edit import VideoEditError, find_ffmpeg

_UPLOAD_ID = re.compile(r"^[0-9a-f]{16,64}$")
OUTPUT_NAME = "timeline-video.mp4"
SCENE_IMAGE_DIR = "scene-images"

#: Style contract for generated scene backgrounds. The renderer paints the
#: narration on top, so the image must stay clean, dark, and free of text.
_SCENE_STYLE = (
    "cinematic editorial illustration for a vertical social video background, "
    "dark moody palette, soft rim lighting, shallow depth of field, "
    "clear negative space in the lower third for subtitles, "
    "no text, no watermark, no logo, no letters"
)


def upload_root(settings) -> Path:
    """Return the directory that holds operator uploads."""
    return Path(str(getattr(settings, "data_dir", "/data") or "/data")) / "uploads"


def _flag(params: dict[str, Any], name: str, default: bool) -> bool:
    """Read one boolean job parameter, accepting the usual string spellings."""
    if name not in (params or {}):
        return default
    raw = params.get(name)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def scene_image_prompt(scene: dict[str, Any], meta: dict[str, Any]) -> str:
    """Build the image prompt for one scene from its visual direction."""
    subject = str(scene.get("visual") or "").strip()
    if not subject:
        subject = str(scene.get("narration") or "").strip()
    title = str((meta or {}).get("title") or "").strip()
    parts = [part for part in (subject, f"context: {title}" if title else "") if part]
    return f"{'. '.join(parts)}. {_SCENE_STYLE}"[:900]


def _within(candidate: Path, roots: list[Path]) -> bool:
    resolved = os.path.realpath(str(candidate))
    for root in roots:
        base = os.path.realpath(str(root))
        if resolved == base or resolved.startswith(base + os.sep):
            return True
    return False


class TimelineVideoDriver(Driver):
    name = "timeline-video"
    label = "Render a timeline document (scenes, narration, transitions) to MP4"
    group = "api"

    @staticmethod
    def _illustrate(timeline: dict[str, Any], ctx: RunContext) -> int:
        """Generate one AI background per bare scene; return how many landed.

        Only scenes without an operator asset are touched, and the scene keeps
        its text card whenever the endpoint refuses, so this step is additive.
        """
        settings = ctx.settings
        base_url = str(getattr(settings, "writer_base_url", "") or "").rstrip("/")
        model = str(getattr(settings, "writer_model", "") or "")
        if not base_url or not model:
            ctx.log(
                "scene images: no image endpoint configured; "
                "set MEDIA_STUDIO_WRITER_BASE_URL and MEDIA_STUDIO_WRITER_MODEL"
            )
            return 0
        api_key = str(getattr(settings, "writer_api_key", "") or "")
        size = str(
            ctx.params.get("scene_image_size")
            or getattr(settings, "scene_image_size", "")
            or DEFAULT_SCENE_SIZE
        )
        try:
            limit = int(ctx.params.get("scene_image_max") or getattr(settings, "scene_image_max", 8))
        except (TypeError, ValueError):
            limit = 8
        limit = max(0, min(limit, 40))
        if not limit:
            return 0
        timeout = max(30, int(getattr(settings, "job_timeout_seconds", 900) or 900) // 3)
        directory = os.path.join(ctx.work_dir, SCENE_IMAGE_DIR)
        os.makedirs(directory, exist_ok=True)
        meta = timeline.get("meta") or {}
        made = 0
        for scene in timeline.get("scenes") or []:
            if made >= limit:
                ctx.log(f"scene images: reached the cap of {limit}; the rest stay text cards")
                break
            if scene.get("asset_type") not in {"text", "auto"}:
                continue
            if scene.get("upload_id") or scene.get("asset_path"):
                continue
            prompt = scene_image_prompt(scene, meta)
            if not prompt.strip():
                continue
            scene_id = int(scene.get("id") or made + 1)
            try:
                images = generate_images(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    prompt=prompt,
                    count=1,
                    size=size,
                    timeout=timeout,
                )
            except ImageApiError as error:
                detail = f": {error.detail}" if error.detail else ""
                ctx.log(f"scene {scene_id}: image generation failed ({error}){detail}")
                continue
            content = images[0]
            path = os.path.join(directory, f"scene-{scene_id:03d}.{ext_from_bytes(content)}")
            try:
                with open(path, "wb") as handle:
                    handle.write(content)
            except OSError as error:
                ctx.log(f"scene {scene_id}: image could not be stored ({error})")
                continue
            scene["asset_path"] = path
            scene["asset_type"] = "image"
            if not scene.get("animation") or scene.get("animation") == "none":
                scene["animation"] = "zoom-in" if scene_id % 2 else "pan-left"
            made += 1
            ctx.log(f"scene {scene_id}: illustrated with a generated image ({len(content)} bytes)")
        return made

    @staticmethod
    def _resolve(mapping: dict[str, Any], uploads: Path, roots: list[Path]) -> str | None:
        raw_id = str(mapping.get("upload_id") or "").strip()
        if raw_id and _UPLOAD_ID.fullmatch(raw_id):
            directory = uploads / raw_id
            wanted = str(mapping.get("file_name") or "").strip()
            if directory.is_dir():
                entries = [entry for entry in sorted(directory.iterdir()) if entry.is_file()]
                if wanted:
                    named = [entry for entry in entries if entry.name == wanted]
                    if named:
                        return str(named[0])
                if entries:
                    return str(entries[0])
        raw_path = str(mapping.get("asset_path") or "").strip()
        if raw_path:
            candidate = Path(raw_path)
            if candidate.is_absolute() and _within(candidate, roots) and candidate.is_file():
                return str(candidate)
        return None

    def run(self, ctx: RunContext) -> list[tuple[str, str]]:
        try:
            raw = timeline_from_params(ctx.params)
        except TimelineError as error:
            raise DriverError(error.describe(), step="timeline") from error
        if raw is None:
            raise DriverError(
                "The job carries no timeline.",
                step="config",
                hint=(
                    'Submit {"driver": "timeline-video", "params": {"timeline": {...}}} '
                    "or pass the document as a JSON string."
                ),
            )
        try:
            timeline = normalize_timeline(raw)
        except TimelineError as error:
            raise DriverError(error.describe(), step="validate") from error
        try:
            ffmpeg = find_ffmpeg(str(getattr(ctx.settings, "ffmpeg_binary", "") or ""))
        except VideoEditError as error:
            raise DriverError(
                str(error),
                step="ffmpeg",
                hint="Install imageio-ffmpeg in the Media Studio image or set MEDIA_STUDIO_FFMPEG.",
            ) from error

        uploads = upload_root(ctx.settings)
        roots = [uploads, Path(ctx.work_dir)]
        timeout = int(getattr(ctx.settings, "timeline_timeout_seconds", 1800) or 1800)
        brand_label = str(getattr(ctx.settings, "brand_label", "") or "").strip()
        meta = timeline["meta"]
        totals = timeline["totals"]
        ctx.log(
            "timeline: "
            f"{totals['scenes']} scene(s), {totals['duration_seconds']:.1f}s, "
            f"{meta['resolution']} @ {meta['fps']}fps"
        )
        if _flag(
            ctx.params,
            "scene_images",
            bool(getattr(ctx.settings, "scene_image_enabled", True)),
        ):
            illustrated = self._illustrate(timeline, ctx)
            ctx.log(f"scene images: {illustrated} of {totals['scenes']} scene(s) illustrated")
        destination = os.path.join(ctx.work_dir, OUTPUT_NAME)
        try:
            summary = render_timeline(
                timeline,
                destination,
                work_dir=ctx.work_dir,
                ffmpeg=ffmpeg,
                log=ctx.log,
                resolve_asset=lambda mapping: self._resolve(mapping, uploads, roots),
                timeout_seconds=timeout,
                brand_label=brand_label,
            )
        except TimelineError as error:
            raise DriverError(error.describe(), step="render") from error
        ctx.log(f"timeline render summary: {summary}")
        return [(OUTPUT_NAME, "video")]
