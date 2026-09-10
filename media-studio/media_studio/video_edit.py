"""Deterministic video preparation for operator uploads.

The operator can record a clip and send it to the bot; Media Studio then
normalises it before publishing instead of handing the phone export to
Telegram or Instagram unchanged. The edit is local and offline:

* re-encode to H.264 / AAC in an MP4 container with ``faststart`` so every
  player and publisher accepts the file,
* cap the long side (default 1920 px) while keeping the aspect ratio and
  even pixel dimensions,
* optionally trim to a maximum duration,
* optionally bake the configured brand mark into the bottom corner,
* drop container metadata (rotation, GPS, device tags).

A static ffmpeg from the ``imageio-ffmpeg`` wheel is used when no system
binary is available, so the container stays slim. Nothing here talks to the
network.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from media_studio import branding as branding_mod

DEFAULT_MAX_SIDE = 1920
DEFAULT_TIMEOUT_SECONDS = 900
_DIMENSIONS = re.compile(r"(\d{2,5})x(\d{2,5})")


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


def target_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """The output size the scale filter produces, for a known input size."""
    side = int(max_side or 0)
    longest = max(int(width or 0), int(height or 0))
    if side <= 0 or longest <= side:
        return max(2, width - width % 2), max(2, height - height % 2)
    ratio = side / longest
    scaled_w = max(2, int(width * ratio))
    scaled_h = max(2, int(height * ratio))
    return scaled_w - scaled_w % 2, scaled_h - scaled_h % 2


def probe_dimensions(ffmpeg_binary: str, path: str, *, timeout: int = 60) -> tuple[int, int]:
    """Read the pixel size of one clip without ffprobe.

    ``ffmpeg -i`` prints the stream summary and exits with an error code; the
    size is parsed from that summary so no extra binary is required.
    """
    arguments = [ffmpeg_binary, "-hide_banner", "-nostdin", "-i", str(path)]
    try:
        completed = subprocess.run(  # noqa: S603 - operator-configured binary
            arguments,
            capture_output=True,
            timeout=max(int(timeout or 60), 1),
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoEditError("the clip could not be inspected in time") from exc
    except OSError as exc:
        raise VideoEditError(f"ffmpeg could not be started: {exc}") from exc
    detail = (completed.stderr or b"").decode("utf-8", "replace")
    for line in detail.splitlines():
        if "Video:" not in line:
            continue
        for match in _DIMENSIONS.finditer(line):
            width, height = int(match.group(1)), int(match.group(2))
            if 16 <= width <= 16384 and 16 <= height <= 16384:
                return width, height
    raise VideoEditError("the clip dimensions could not be read")


def _overlay_position(position: str, margin: int) -> str:
    """Translate one corner name into an ffmpeg overlay expression."""
    gap = max(0, int(margin or 0))
    return {
        "bottom-right": f"W-w-{gap}:H-h-{gap}",
        "bottom-left": f"{gap}:H-h-{gap}",
        "top-right": f"W-w-{gap}:{gap}",
        "top-left": f"{gap}:{gap}",
    }.get(str(position or "").strip(), f"W-w-{gap}:H-h-{gap}")


def edit_arguments(
    source: str,
    destination: str,
    *,
    ffmpeg: str = "ffmpeg",
    max_side: int = DEFAULT_MAX_SIDE,
    max_seconds: int = 0,
    overlay: str = "",
    overlay_position: str = "bottom-right",
    overlay_margin: int = 0,
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
    if overlay:
        # A second input keeps the brand PNG pixel-exact instead of being
        # re-encoded by drawtext; the alpha channel of the PNG is preserved.
        arguments = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(source),
            "-i",
            str(overlay),
            "-filter_complex",
            f"[0:v]{scale},format=yuv420p[base];"
            f"[base][1:v]overlay={_overlay_position(overlay_position, overlay_margin)}"
            ":format=auto[v]",
            "-map",
            "[v]",
            "-map",
            "0:a:0?",
        ]
    else:
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
        ]
    arguments += [
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


def brand_overlay_for(
    source: str,
    destination: str,
    *,
    ffmpeg_binary: str,
    max_side: int,
    label: str,
    style: str = "aurora",
    position: str = "bottom-right",
) -> tuple[str, int]:
    """Render the brand PNG sized for this clip.

    Returns the written path and the corner margin in pixels; an empty path
    means the mark could not be prepared and the clip is encoded without it.
    """
    width, height = probe_dimensions(ffmpeg_binary, source)
    _, out_height = target_size(width, height, max_side)
    chip_height = max(24, round(out_height * 0.055))
    overlay_path = str(
        Path(destination).with_name(f".{Path(destination).stem}-brand.png")
    )
    if not branding_mod.render_chip_file(
        overlay_path,
        label,
        max_side=chip_height,
        style=style,
    ):
        return "", 0
    margin = max(8, round(out_height * 0.03))
    return overlay_path, margin


def edit_video(
    source: str,
    destination: str,
    *,
    ffmpeg: str = "",
    max_side: int = DEFAULT_MAX_SIDE,
    max_seconds: int = 0,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    log: Callable[[str], None] | None = None,
    brand_label: str = "",
    brand_style: str = "aurora",
    brand_position: str = "bottom-right",
) -> str:
    """Prepare one clip and return the destination path."""
    source_path = Path(source)
    if not source_path.is_file():
        raise VideoEditError(f"the uploaded clip is missing: {source_path.name}")
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    binary = find_ffmpeg(ffmpeg)
    overlay = ""
    margin = 0
    label = str(brand_label or "").strip()
    if label:
        try:
            overlay, margin = brand_overlay_for(
                str(source_path),
                str(destination_path),
                ffmpeg_binary=binary,
                max_side=int(max_side or 0),
                label=label,
                style=brand_style,
                position=brand_position,
            )
        except VideoEditError as error:
            # Branding is optional: a clip is still better than no clip.
            if log is not None:
                log(f"brand mark skipped: {error}")
            overlay = ""
        if overlay and log is not None:
            log("brand mark: applying the configured label to the prepared clip")
    arguments = edit_arguments(
        str(source_path),
        str(destination_path),
        ffmpeg=binary,
        max_side=max_side,
        max_seconds=max_seconds,
        overlay=overlay,
        overlay_position=brand_position,
        overlay_margin=margin,
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
    finally:
        if overlay:
            try:
                os.unlink(overlay)
            except OSError:
                pass
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
