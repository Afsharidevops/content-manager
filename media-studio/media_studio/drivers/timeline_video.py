"""Timeline render driver: turn a timeline document into one MP4.

The job carries the timeline in ``params.timeline`` (object or JSON string).
Every scene asset resolves through the same allow-list as operator uploads:
an ``upload_id`` points into the uploads directory, and an absolute
``asset_path`` must live under the uploads directory or the job work
directory. Nothing else is readable, so a timeline can never pull arbitrary
files from the host into a video.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from media_studio.drivers.base import Driver, DriverError, RunContext
from media_studio.timeline import TimelineError, normalize_timeline, timeline_from_params
from media_studio.timeline_render import render_timeline
from media_studio.video_edit import VideoEditError, find_ffmpeg

_UPLOAD_ID = re.compile(r"^[0-9a-f]{16,64}$")
OUTPUT_NAME = "timeline-video.mp4"


def upload_root(settings) -> Path:
    """Return the directory that holds operator uploads."""
    return Path(str(getattr(settings, "data_dir", "/data") or "/data")) / "uploads"


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
        meta = timeline["meta"]
        totals = timeline["totals"]
        ctx.log(
            "timeline: "
            f"{totals['scenes']} scene(s), {totals['duration_seconds']:.1f}s, "
            f"{meta['resolution']} @ {meta['fps']}fps"
        )
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
            )
        except TimelineError as error:
            raise DriverError(error.describe(), step="render") from error
        ctx.log(f"timeline render summary: {summary}")
        return [(OUTPUT_NAME, "video")]
