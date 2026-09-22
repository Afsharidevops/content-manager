"""Render a validated timeline document to one MP4 with ffmpeg.

The renderer is deliberately deterministic: every scene becomes its own
segment file (background, animation, caption), and the segments are joined
with ffmpeg ``xfade`` transitions before the narration track and the brand
mark are muxed in. No browser, no network, no model calls: given the same
timeline and assets, the output is identical, which is what makes the video
pipeline controllable by architecture instead of by UI automation.

Scene backgrounds come from three places:

* an uploaded image or video asset (``asset_type`` image/video),
* a generated text card when the scene carries only narration (the card is
  painted with Pillow and shaped for right-to-left scripts when libraqm is
  available in the image),
* a plain gradient card for ``solid`` scenes.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from media_studio import branding as branding_mod
from media_studio.timeline import (
    TimelineError,
    XFADE_NAMES,
    contains_rtl,
)
from media_studio.video_edit import _overlay_position

#: A "cut" is rendered as a very short crossfade: one filter path for every
#: transition keeps the timing math uniform and the difference is invisible.
CUT_SECONDS = 0.04

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

#: Gradient stops used for generated cards, keyed by scene index modulo the
#: palette length so consecutive text scenes do not look identical.
_CARD_PALETTES = (
    ((11, 18, 38), (24, 58, 110), (32, 211, 238)),
    ((16, 12, 40), (76, 29, 149), (236, 72, 153)),
    ((8, 25, 34), (13, 78, 92), (45, 212, 191)),
    ((26, 14, 10), (120, 53, 15), (250, 204, 21)),
)


def find_text_font(bold: bool = False) -> str | None:
    """Return the best available text font, preferring Arabic coverage."""
    candidates = _FONT_CANDIDATES
    if bold:
        candidates = tuple(path for path in _FONT_CANDIDATES if "Bold" in path) + _FONT_CANDIDATES
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _raqm_available() -> bool:
    try:
        from PIL import features

        return bool(features.check("raqm"))
    except Exception:  # noqa: BLE001 - Pillow always ships features in practice
        return False


def _load_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    path = find_text_font(bold=bold)
    try:
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except OSError:
        return ImageFont.load_default()


def _text_width(draw: Any, text: str, font: Any) -> float:
    try:
        return float(draw.textlength(text, font=font))
    except Exception:  # noqa: BLE001 - bitmap fallback fonts
        return float(len(text) * max(6, getattr(font, "size", 12)) * 0.6)


def wrap_text(draw: Any, text: str, font: Any, max_width: float) -> list[str]:
    """Wrap one paragraph to the given pixel width, preserving words."""
    lines: list[str] = []
    for paragraph in str(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _draw_bidi(draw: Any, position: tuple[float, float], text: str, font: Any, fill: Any) -> None:
    """Draw one line, shaping RTL scripts when Pillow supports raqm."""
    if contains_rtl(text) and _raqm_available():
        try:
            draw.text(position, text, font=font, fill=fill, direction="rtl", language="fa")
            return
        except (ValueError, TypeError):
            pass
    draw.text(position, text, font=font, fill=fill)


def _gradient(size: tuple[int, int], stops: tuple[tuple[int, int, int], ...]):
    from PIL import Image, ImageDraw

    width, height = size
    base = Image.new("RGB", (1, height))
    canvas = Image.new("RGB", size)
    draw = ImageDraw.Draw(base)
    count = len(stops)
    for y in range(height):
        ratio = y / max(1, height - 1)
        scaled = ratio * (count - 1)
        low = int(scaled)
        high = min(count - 1, low + 1)
        blend = scaled - low
        color = tuple(
            round(stops[low][channel] * (1 - blend) + stops[high][channel] * blend)
            for channel in range(3)
        )
        draw.point((0, y), fill=color)
    stretched = base.resize((width, height))
    canvas.paste(stretched, (0, 0))
    return canvas


def _scene_palette(index: int):
    return _CARD_PALETTES[index % len(_CARD_PALETTES)]


def _render_card(
    scene: dict,
    size: tuple[int, int],
    path: str,
    *,
    index: int,
) -> None:
    """Paint one gradient card with the scene narration for text scenes."""
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    ground, mid, accent = _scene_palette(index)
    canvas = _gradient((width, height), (ground, mid)).convert("RGBA")
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    radius = int(min(width, height) * 0.45)
    glow_draw.ellipse(
        (
            width * 0.62 - radius,
            height * 0.16 - radius // 2,
            width * 0.62 + radius,
            height * 0.16 + radius // 2,
        ),
        fill=(*accent, 66),
    )
    glow_draw.ellipse(
        (
            width * 0.12 - radius // 2,
            height * 0.88 - radius // 2,
            width * 0.12 + radius // 2,
            height * 0.88 + radius // 2,
        ),
        fill=(*accent, 44),
    )
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(radius // 6)))

    text = str(scene.get("narration") or scene.get("visual") or "").strip()
    if text:
        title_size = max(28, round(height * 0.052))
        body_size = max(22, round(height * 0.036))
        title_font = _load_font(title_size, bold=True)
        body_font = _load_font(body_size)
        draw = ImageDraw.Draw(canvas)
        margin = round(width * 0.09)
        max_width = width - margin * 2
        heading = str(scene.get("visual") or "").strip()
        if heading and heading != text:
            lines = wrap_text(draw, heading, title_font, max_width)
        else:
            lines = wrap_text(draw, text, body_font, max_width)
        line_gap = round(body_size * 0.55)
        heights = []
        for line in lines:
            box = draw.textbbox((0, 0), line or " ", font=body_font)
            heights.append(box[3] - box[1] + line_gap)
        total = sum(heights)
        cursor = (height - total) / 2
        for line, line_height in zip(lines, heights):
            if not line:
                cursor += line_height
                continue
            width_px = _text_width(draw, line, body_font)
            x = (width - width_px) / 2
            _draw_bidi(draw, (x + 2, cursor + 2), line, body_font, (0, 0, 0, 130))
            _draw_bidi(draw, (x, cursor), line, body_font, (255, 255, 255, 244))
            cursor += line_height

    try:
        canvas.convert("RGB").save(path, format="PNG")
    except (OSError, ValueError) as error:
        raise TimelineError(
            f"the scene card could not be written: {error}",
            field="scenes",
        ) from error


def _render_caption(
    scene: dict,
    size: tuple[int, int],
    path: str,
) -> bool:
    """Paint the caption band for image and video scenes; False when empty."""
    from PIL import Image, ImageDraw

    text = str(scene.get("narration") or "").strip()
    if not text:
        return False
    width, height = size
    band_height = max(round(height * 0.22), 200)
    canvas = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    scrim = Image.new("RGBA", (width, band_height), (0, 0, 0, 0))
    scrim_draw = ImageDraw.Draw(scrim)
    for row in range(band_height):
        alpha = round(196 * (row / max(1, band_height - 1)) ** 1.4)
        scrim_draw.line(((0, row), (width, row)), fill=(4, 7, 18, alpha))
    canvas.alpha_composite(scrim)

    font = _load_font(max(24, round(height * 0.034)), bold=False)
    draw = ImageDraw.Draw(canvas)
    margin = round(width * 0.075)
    max_width = width - margin * 2
    lines = wrap_text(draw, text, font, max_width)[:4]
    line_height = round(font.size * 1.35) if hasattr(font, "size") else 40
    total = line_height * len(lines)
    cursor = band_height - total - round(band_height * 0.18)
    for line in lines:
        if not line:
            cursor += line_height
            continue
        width_px = _text_width(draw, line, font)
        x = (width - width_px) / 2
        _draw_bidi(draw, (x + 2, cursor + 2), line, font, (0, 0, 0, 150))
        _draw_bidi(draw, (x, cursor), line, font, (255, 255, 255, 240))
        cursor += line_height
    canvas.save(path, format="PNG")
    return True


def _run_ffmpeg(
    arguments: list[str],
    *,
    step: str,
    timeout_seconds: int,
) -> None:
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_seconds)),
        )
    except subprocess.TimeoutExpired as error:
        raise TimelineError(
            f"ffmpeg timed out during {step}",
            field=step,
            hint="Shorten the timeline or raise MEDIA_STUDIO_TIMELINE_TIMEOUT_SECONDS.",
        ) from error
    except OSError as error:
        raise TimelineError(f"ffmpeg could not be started: {error}", field=step) from error
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "").strip().splitlines()[-8:])
        raise TimelineError(
            f"ffmpeg failed during {step}: {tail or 'no stderr output'}",
            field=step,
            hint="Open the job log for the full ffmpeg output.",
        )


def _video_filter(size: tuple[int, int], fps: int) -> str:
    width, height = size
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},format=yuv420p,setsar=1"
    )


def _still_filter(size: tuple[int, int], fps: int, animation: str, frames: int) -> str:
    """Build the filter chain for one still image scene.

    The still is upscaled before ``zoompan`` so the moving crop keeps detail,
    and downsampled to the target size by ``zoompan`` itself.
    """
    width, height = size
    wide = max(width, round(width * 1.5)) // 2 * 2
    tall = max(height, round(height * 1.5)) // 2 * 2
    if animation == "none":
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},format=yuv420p,setsar=1"
        )
    base = f"scale={wide}:{tall}:force_original_aspect_ratio=increase,crop={wide}:{tall}"
    if animation == "zoom-in":
        zoompan = (
            f"zoompan=z='min(zoom+0.0009,1.12)':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
        )
    elif animation == "zoom-out":
        zoompan = (
            f"zoompan=z='if(lte(zoom,1.0),1.12,max(1.001,zoom-0.0009))':d={frames}"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
        )
    elif animation == "pan-left":
        zoompan = (
            f"zoompan=z='1.08':d={frames}"
            f":x='iw/2-(iw/zoom/2)+((iw/zoom/2)*on/{max(1, frames)})':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={fps}"
        )
    elif animation == "pan-right":
        zoompan = (
            f"zoompan=z='1.08':d={frames}"
            f":x='iw/2-(iw/zoom/2)-((iw/zoom/2)*on/{max(1, frames)})':y='ih/2-(ih/zoom/2)'"
            f":s={width}x{height}:fps={fps}"
        )
    else:
        zoompan = ""
    return f"{base},{zoompan},format=yuv420p,setsar=1"


def _segment_arguments(
    *,
    ffmpeg: str,
    scene: dict,
    background: str,
    caption: str | None,
    size: tuple[int, int],
    fps: int,
    destination: str,
) -> list[str]:
    duration = float(scene["duration"])
    frames = max(2, round(duration * fps))
    is_still = scene.get("asset_type") != "video"
    if is_still:
        chain = _still_filter(size, fps, str(scene.get("animation") or "none"), frames)
        inputs = ["-loop", "1", "-framerate", str(fps), "-i", background]
    else:
        chain = _video_filter(size, fps)
        inputs = ["-i", background]
    arguments = [ffmpeg, "-hide_banner", "-nostdin", "-y", *inputs]
    if caption:
        arguments += ["-i", caption]
        filter_complex = (
            f"[0:v]{chain}[base];[base][1:v]overlay=0:H-h:format=auto,format=yuv420p[out]"
        )
    else:
        filter_complex = f"[0:v]{chain}[out]"
    arguments += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-t",
        f"{duration:.3f}",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        destination,
    ]
    return arguments


def _transition_seconds(scene: dict) -> float:
    transition = str(scene.get("transition") or "cut")
    if transition == "cut" or transition not in XFADE_NAMES:
        return CUT_SECONDS
    return 0.5


def _assemble_arguments(
    *,
    ffmpeg: str,
    segments: list[str],
    scenes: list[dict],
    size: tuple[int, int],
    fps: int,
    destination: str,
    audio: str = "",
    audio_volume: float = 1.0,
    brand_png: str = "",
    brand_position: str = "bottom-right",
    brand_margin: int = 0,
) -> list[str]:
    width, height = size
    arguments = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
    for segment in segments:
        arguments += ["-i", segment]
    next_index = len(segments)
    audio_index = -1
    if audio:
        audio_index = next_index
        next_index += 1
        arguments += ["-i", audio]
    brand_index = -1
    if brand_png:
        brand_index = next_index
        next_index += 1
        arguments += ["-i", brand_png]

    chains: list[str] = []
    current = "[0:v]"
    elapsed = float(scenes[0]["duration"])
    for index in range(1, len(segments)):
        transition = str(scenes[index].get("transition") or "cut")
        name = XFADE_NAMES.get(transition, "fade")
        seconds = _transition_seconds(scenes[index])
        offset = max(0.0, elapsed - seconds)
        target = f"[x{index}]"
        chains.append(
            f"{current}[{index}:v]xfade=transition={name}:duration={seconds:.3f}:offset={offset:.3f}{target}"
        )
        elapsed = elapsed + float(scenes[index]["duration"]) - seconds
        current = target
    final = current
    if brand_index >= 0:
        margin = max(0, int(brand_margin or 0))
        chains.append(
            f"{final}[{brand_index}:v]overlay={_overlay_position(brand_position, margin)}:format=auto[vout]"
        )
        final = "[vout]"
    filter_complex = ";".join(chains) if chains else f"[0:v]null[vout]"
    if not chains:
        final = "[vout]"
    arguments += ["-filter_complex", filter_complex, "-map", final]
    if audio_index >= 0:
        arguments += ["-map", f"{audio_index}:a:0", "-af", f"apad,volume={audio_volume:.2f}"]
    arguments += [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-s",
        f"{width}x{height}",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
    ]
    if audio_index >= 0:
        arguments += ["-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest"]
    arguments.append(destination)
    return arguments


def render_timeline(
    timeline: dict,
    destination: str,
    *,
    work_dir: str,
    ffmpeg: str,
    log: Callable[[str], None],
    resolve_asset: Callable[[dict], str | None],
    timeout_seconds: int = 1800,
) -> dict:
    """Render one normalized timeline to ``destination`` and return a summary.

    ``resolve_asset`` receives the scene or audio mapping and must return a
    local file path, or ``None`` when the asset cannot be found; the renderer
    then falls back to a generated card for that scene.
    """
    scenes = list(timeline.get("scenes") or [])
    meta = dict(timeline.get("meta") or {})
    width, height = (int(part) for part in str(meta.get("resolution") or "1080x1920").split("x"))
    fps = int(meta.get("fps") or 30)
    size = (width, height)
    work_root = os.path.join(work_dir, "timeline")
    os.makedirs(work_root, exist_ok=True)
    subtitle = bool(meta.get("subtitle", True))

    segments: list[str] = []
    for index, scene in enumerate(scenes):
        background = ""
        asset_type = str(scene.get("asset_type") or "text")
        if asset_type in {"image", "video"}:
            resolved = resolve_asset(scene)
            if resolved:
                background = resolved
                log(f"scene {index + 1}: using asset {os.path.basename(resolved)}")
            else:
                log(f"scene {index + 1}: asset not found; falling back to a generated card")
        if not background:
            background = os.path.join(work_root, f"card-{index + 1:03d}.png")
            _render_card(scene, size, background, index=index)
            asset_type = "text"
        caption = ""
        if subtitle and asset_type != "text":
            caption_path = os.path.join(work_root, f"caption-{index + 1:03d}.png")
            if _render_caption(scene, size, caption_path):
                caption = caption_path
        segment_path = os.path.join(work_root, f"segment-{index + 1:03d}.mp4")
        _run_ffmpeg(
            _segment_arguments(
                ffmpeg=ffmpeg,
                scene=scene,
                background=background,
                caption=caption or None,
                size=size,
                fps=fps,
                destination=segment_path,
            ),
            step=f"scene {index + 1} of {len(scenes)}",
            timeout_seconds=timeout_seconds,
        )
        segments.append(segment_path)
        log(f"scene {index + 1}/{len(scenes)} rendered ({float(scene['duration']):.1f}s)")

    audio_path = ""
    audio_volume = 1.0
    raw_audio = timeline.get("audio") or {}
    if raw_audio:
        resolved_audio = resolve_asset(raw_audio)
        if resolved_audio:
            audio_path = resolved_audio
            audio_volume = float(raw_audio.get("volume") or 1.0)
            log(f"narration track: {os.path.basename(resolved_audio)}")
        else:
            log("narration track was requested but the file is missing; rendering silent")

    brand_png = ""
    brand = dict(meta.get("brand") or {})
    brand_label = str(brand.get("label") or "").strip()
    if brand_label:
        brand_path = os.path.join(work_root, "brand.png")
        if branding_mod.render_chip_file(
            brand_path,
            brand_label,
            max_side=max(36, round(height * 0.052)),
            style=str(brand.get("style") or "aurora"),
        ):
            brand_png = brand_path
        else:
            log("brand mark could not be rendered; continuing without it")

    _run_ffmpeg(
        _assemble_arguments(
            ffmpeg=ffmpeg,
            segments=segments,
            scenes=scenes,
            size=size,
            fps=fps,
            destination=destination,
            audio=audio_path,
            audio_volume=audio_volume,
            brand_png=brand_png,
            brand_position=str(brand.get("position") or "bottom-right"),
            brand_margin=max(12, round(min(size) * 0.035)),
        ),
        step="assemble",
        timeout_seconds=timeout_seconds,
    )
    for segment in segments:
        try:
            os.remove(segment)
        except OSError:
            pass
    summary = {
        "scenes": len(scenes),
        "duration_seconds": float((timeline.get("totals") or {}).get("duration_seconds") or 0),
        "resolution": f"{width}x{height}",
        "fps": fps,
        "audio": bool(audio_path),
        "brand": bool(brand_png),
        "size_bytes": os.path.getsize(destination) if os.path.isfile(destination) else 0,
    }
    log(
        "render complete: "
        + json.dumps(summary, ensure_ascii=False)
    )
    return summary
