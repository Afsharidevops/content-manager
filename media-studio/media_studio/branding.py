"""Optional brand chip overlay for AI-generated raster artifacts.

Media Studio draws the configured brand label into a translucent rounded
chip in one corner of every raster artifact produced by an image driver.
Operator uploads never pass through Media Studio, so they are never altered.
"""

from __future__ import annotations

import logging
import os

LOGGER = logging.getLogger("media_studio.branding")

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)

_POSITION_ANCHORS = {
    "top-left": (0.0, 0.0),
    "top-right": (1.0, 0.0),
    "bottom-left": (0.0, 1.0),
    "bottom-right": (1.0, 1.0),
}


def find_font_path(extra_candidates: tuple[str, ...] = ()) -> str | None:
    """Return the first available TrueType font path, or ``None``."""
    for path in (*extra_candidates, *_FONT_CANDIDATES):
        if path and os.path.isfile(path):
            return path
    return None


def apply_brand_overlay(
    image_path: str,
    label: str,
    *,
    position: str = "bottom-right",
    font_path: str | None = None,
) -> bool:
    """Draw ``label`` into the chosen corner of the raster at ``image_path``.

    Returns True when a chip was drawn. A blank label, an unsupported corner,
    missing Pillow, or an unreadable file returns False without raising so a
    branding failure never fails a media job.
    """
    text = str(label or "").strip()
    if not text:
        return False
    if position not in _POSITION_ANCHORS:
        LOGGER.warning("brand overlay: unknown position %r; skipped", position)
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        LOGGER.warning("brand overlay skipped: Pillow is not installed")
        return False
    try:
        image = Image.open(image_path)
        original_format = (image.format or "PNG").upper()
        rgba = image.convert("RGBA")
    except OSError as error:
        LOGGER.warning("brand overlay skipped for %s: %s", image_path, error)
        return False

    width, height = rgba.size
    scale = max(0.5, min(width, height) / 1024.0)
    font_size = max(16, round(36 * scale))
    font_path = font_path or find_font_path()
    if font_path:
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(rgba)
    text_box = draw.textbbox((0, 0), text, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    pad_x = max(10, round(18 * scale))
    pad_y = max(6, round(10 * scale))
    margin = max(8, round(22 * scale))
    chip_width = text_width + pad_x * 2
    chip_height = text_height + pad_y * 2
    anchor_x, anchor_y = _POSITION_ANCHORS[position]
    left = round(width * anchor_x - chip_width - margin) if anchor_x else round(margin)
    top = round(height * anchor_y - chip_height - margin) if anchor_y else round(margin)
    left = max(0, min(left, width - chip_width))
    top = max(0, min(top, height - chip_height))
    if chip_width > width or chip_height > height:
        LOGGER.warning("brand overlay skipped for %s: image smaller than the chip", image_path)
        return False
    radius = max(4, round(chip_height * 0.4))
    draw.rounded_rectangle(
        (left, top, left + chip_width, top + chip_height),
        radius=radius,
        fill=(10, 10, 12, 165),
    )
    draw.text(
        (left + pad_x, top + pad_y - text_box[1]),
        text,
        font=font,
        fill=(255, 255, 255, 240),
    )
    output = rgba
    if original_format in {"JPEG", "JPG"}:
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[3])
        output = background
    try:
        output.save(image_path, format=original_format)
    except (OSError, ValueError) as error:
        try:
            output.save(image_path)
        except (OSError, ValueError):
            LOGGER.warning("brand overlay could not save %s: %s", image_path, error)
            return False
        LOGGER.warning("brand overlay could not save %s: %s", image_path, error)
        return False
    return True
