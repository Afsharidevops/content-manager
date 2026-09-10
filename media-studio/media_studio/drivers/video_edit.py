"""Video edit driver: prepare one operator-uploaded clip with ffmpeg.

The bot first uploads the raw clip to ``POST /uploads``; the job then only
carries the upload id, so a job can never read outside the uploads directory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from media_studio.drivers.base import Driver, DriverError, RunContext
from media_studio.video_edit import VideoEditError, edit_video

_UPLOAD_ID = re.compile(r"^[0-9a-f]{16,64}$")


def upload_root(settings) -> Path:
    """Return the directory that holds operator uploads."""
    return Path(str(getattr(settings, "data_dir", "/data") or "/data")) / "uploads"


def resolve_upload(settings, upload_id: str) -> Path:
    """Return the stored source file for one upload id."""
    raw = str(upload_id or "").strip()
    if not _UPLOAD_ID.fullmatch(raw):
        raise DriverError(
            "The job carries no usable upload id.",
            step="config",
            hint="Upload the clip to POST /uploads and pass the returned id as params.upload_id.",
        )
    directory = upload_root(settings) / raw
    if not directory.is_dir():
        raise DriverError(
            "The uploaded clip is no longer stored.",
            step="config",
            hint="Uploads are pruned after MEDIA_STUDIO_UPLOAD_TTL_SECONDS; upload the clip again.",
        )
    for entry in sorted(directory.iterdir()):
        if entry.is_file():
            return entry
    raise DriverError("The upload directory holds no file.", step="config")


class VideoEditDriver(Driver):
    name = "video-edit"
    label = "Normalise an operator-uploaded video with ffmpeg"
    group = "api"

    def run(self, ctx: RunContext) -> list[tuple[str, str]]:
        settings = ctx.settings
        source = resolve_upload(settings, str(ctx.params.get("upload_id") or ""))
        stem = Path(source.name).stem or "clip"
        name = f"edited-{stem}.mp4"
        destination = os.path.join(ctx.work_dir, name)
        max_side = ctx.params.get(
            "max_side", getattr(settings, "video_edit_max_side", 1920)
        )
        max_seconds = ctx.params.get(
            "max_seconds", getattr(settings, "video_edit_max_seconds", 0)
        )
        try:
            edit_video(
                str(source),
                destination,
                ffmpeg=str(getattr(settings, "ffmpeg_binary", "") or ""),
                max_side=int(max_side or 0),
                max_seconds=int(max_seconds or 0),
                timeout=int(getattr(settings, "video_edit_timeout_seconds", 900) or 900),
                log=ctx.log,
            )
        except VideoEditError as error:
            raise DriverError(
                str(error),
                step="edit",
                hint="Check the Media Studio ffmpeg settings and the uploaded clip.",
            ) from error
        return [(name, "video")]
