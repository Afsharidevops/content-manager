"""Deterministic video preparation for operator uploads.

The operator can record a clip and send it to the bot; Media Studio then
normalises it before publishing instead of handing the phone export to
Telegram or Instagram unchanged. The edit is local and offline:

* re-encode to H.264 / AAC in an MP4 container with ``faststart`` so every
  player and publisher accepts the file,
* cap the long side (default 1920 px) while keeping the aspect ratio and
  even pixel dimensions,
* optionally trim to a maximum duration,
* drop container metadata (rotation, GPS, device tags).

A static ffmpeg from the ``imageio-ffmpeg`` wheel is used when no system
binary is available, so the container stays slim. Nothing here talks to the
network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

DEFAULT_MAX_SIDE = 1920
DEFAULT_TIMEOUT_SECONDS = 900


class VideoEditError(RuntimeError):
    """Raised when ffmpeg is missing or the clip cannot be prepared."""


def find_ffmpeg(explicit: str = "") -> str:
    """Return the ffmpeg binary to use, or raise VideoEditError."""
    candidates = (
        explicit,
        os.environ.get("MEDIA_STUDIO_FFMPEG", ""),
        shutil.which("ffmpeg") or "",
    )
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    try:  # pragma: no cover - exercised only without a system binary
        import imageio_ffmpeg
    except ImportError as exc:
        raise VideoEditError(
            "ffmpeg is not available; install imageio-ffmpeg or set "
            "MEDIA_STUDIO_FFMPEG."
        ) from exc
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        raise VideoEditError(f"ffmpeg could not be located: {exc}") from exc


def edit_arguments(
    source: str,
    destination: str,
    *,
    ffmpeg: str = "ffmpeg",
    max_side: int = DEFAULT_MAX_SIDE,
    max_seconds: int = 0,
) -> list[str]:
    """Build the ffmpeg command that prepares one uploaded clip."""
    side = int(max_side or 0)
    if side > 0:
        scale = (
            f"scale='min({side},iw)':'min({side},ih)':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
    else:
        scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    arguments = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        f"{scale},format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-metadata:s:v",
        "rotate=0",
    ]
    seconds = int(max_seconds or 0)
    if seconds > 0:
        arguments += ["-t", str(seconds)]
    arguments.append(str(destination))
    return arguments


def edit_video(
    source: str,
    destination: str,
    *,
    ffmpeg: str = "",
    max_side: int = DEFAULT_MAX_SIDE,
    max_seconds: int = 0,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    log: Callable[[str], None] | None = None,
) -> str:
    """Prepare one clip and return the destination path."""
    source_path = Path(source)
    if not source_path.is_file():
        raise VideoEditError(f"the uploaded clip is missing: {source_path.name}")
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    binary = find_ffmpeg(ffmpeg)
    arguments = edit_arguments(
        str(source_path),
        str(destination_path),
        ffmpeg=binary,
        max_side=max_side,
        max_seconds=max_seconds,
    )
    if log is not None:
        log(
            f"ffmpeg: normalising {source_path.name} "
            f"(max_side={int(max_side or 0)}, max_seconds={int(max_seconds or 0)})"
        )
    try:
        completed = subprocess.run(  # noqa: S603 - operator-configured binary
            arguments,
            capture_output=True,
            timeout=max(int(timeout or DEFAULT_TIMEOUT_SECONDS), 1),
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoEditError("the clip took too long to prepare") from exc
    except OSError as exc:
        raise VideoEditError(f"ffmpeg could not be started: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        tail = detail[-400:] if detail else "no error output"
        raise VideoEditError(f"ffmpeg failed with exit code {completed.returncode}: {tail}")
    if not destination_path.is_file() or destination_path.stat().st_size == 0:
        raise VideoEditError("ffmpeg produced no output file")
    if log is not None:
        size_mb = destination_path.stat().st_size / (1024 * 1024)
        log(f"ffmpeg: prepared {destination_path.name} ({size_mb:.1f} MiB)")
    return str(destination_path)
