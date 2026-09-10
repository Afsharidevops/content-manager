"""Optional brand chip overlay for generated and edited artifacts.

Media Studio draws the configured brand label into one corner of every raster
artifact produced by an image driver, and bakes the same mark into the video
that the ``video-edit`` driver prepares from an operator upload.

Two styles are available: ``aurora`` (default) paints the label into a pill
filled with the LocalLab gradient and finished with a soft halo, a diagonal
highlight, a hairline border, and a hexagon mark; ``chip`` keeps the original
plain translucent rectangle. A branding failure never fails a media job.
"""

from __future__ import annotations

import logging
import os

LOGGER = logging.getLogger("media_studio.branding")

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

_POSITION_ANCHORS = {
    "top-left": (0.0, 0.0),
    "top-right": (1.0, 0.0),
    "bottom-left": (0.0, 1.0),
    "bottom-right": (1.0, 1.0),
}

# LocalLab palette: dark navy ground with a cyan to magenta sweep.
NAVY = (9, 14, 30)
GRADIENT_STOPS = (
    (34, 211, 238),
    (59, 130, 246),
    (139, 92, 246),
    (236, 72, 153),
)
GLOW = (139, 92, 246)
STYLES = ("aurora", "chip")


def find_font_path(extra_candidates: tuple[str, ...] = ()) -> str | None:
    """Return the first available TrueType font path, or ``None``."""
    for path in (*extra_candidates, *_FONT_CANDIDATES):
        if path and os.path.isfile(path):
            return path
    return None


def _load_font(font_path: str | None, size: int):
    from PIL import ImageFont

    for candidate in ((font_path or ""), find_font_path() or ""):
        if not candidate:
            continue
        try:
            return ImageFont.truetype(candidate, size), candidate
        except OSError:
            continue
    return ImageFont.load_default(), ""


def _mix(first, second, ratio: float) -> tuple[int, int, int]:
    ratio = max(0.0, min(1.0, ratio))
    return tuple(
        round(start + (end - start) * ratio)
        for start, end in zip(first, second)
    )


def _gradient_color(ratio: float) -> tuple[int, int, int]:
    stops = GRADIENT_STOPS
    if ratio <= 0:
        return stops[0]
    if ratio >= 1:
        return stops[-1]
    span = 1.0 / (len(stops) - 1)
    index = min(int(ratio / span), len(stops) - 2)
    return _mix(stops[index], stops[index + 1], (ratio - index * span) / span)


def _gradient_layer(size: tuple[int, int], alpha: int):
    from PIL import Image

    width, height = size
    strip = Image.new("RGB", (1, max(1, height)))
    for y in range(max(1, height)):
        strip.putpixel((0, y), _gradient_color(y / max(1, height - 1)))
    layer = strip.resize((width, height)).convert("RGBA")
    layer.putalpha(alpha)
    return layer


def _rounded_mask(size: tuple[int, int], radius: int):
    from PIL import Image, ImageDraw

    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=max(0, radius),
        fill=255,
    )
    return mask


def _highlight_layer(size: tuple[int, int]):
    """A soft diagonal band that reads as a light sweep across the pill."""
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    offset = width * 0.55
    draw.polygon(
        [
            (offset, height),
            (offset + width * 0.16, height),
            (offset + width * 0.34, 0),
            (offset + width * 0.18, 0),
        ],
        fill=(255, 255, 255, 34),
    )
    return layer.filter(ImageFilter.GaussianBlur(max(1, height * 0.06)))


def _hexagon_mark(draw, center, radius: int) -> None:
    import math

    points = [
        (
            center[0] + radius * math.cos(math.radians(60 * step - 30)),
            center[1] + radius * math.sin(math.radians(60 * step - 30)),
        )
        for step in range(6)
    ]
    draw.polygon(points, fill=(255, 255, 255, 236))
    inner = [
        (
            center[0] + radius * 0.45 * math.cos(math.radians(60 * step - 30)),
            center[1] + radius * 0.45 * math.sin(math.radians(60 * step - 30)),
        )
        for step in range(6)
    ]
    draw.polygon(inner, fill=(*NAVY, 255))


def render_chip(
    label: str,
    *,
    font_path: str | None = None,
    height: int = 72,
    style: str = "aurora",
):
    """Render the brand mark and return ``(image, pad)``.

    The pill occupies the inset box ``(pad, pad, width - pad, height - pad)``;
    the surrounding padding holds the halo, so callers position the image by
    the pill corner they want and subtract ``pad``.
    """
    from PIL import Image, ImageDraw, ImageFilter

    text = str(label or "").strip()
    if not text:
        return None, 0
    chosen = str(style or "").strip().lower()
    if chosen not in STYLES:
        chosen = "aurora"
    pill_height = max(28, int(height or 72))
    font_size = max(14, round(pill_height * 0.46))
    font, _ = _load_font(font_path, font_size)
    pad_x = max(12, round(pill_height * 0.34))
    pad_y = max(6, round(pill_height * 0.26))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    box = probe.textbbox((0, 0), text, font=font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    mark_radius = round(pill_height * 0.26) if chosen == "aurora" else 0
    mark_gap = round(pill_height * 0.16) if mark_radius else 0
    pill_width = text_width + pad_x * 2 + (mark_radius * 2 + mark_gap if mark_radius else 0)
    if chosen == "aurora":
        pill_height = max(pill_height, text_height + pad_y * 2 + mark_radius)
    radius = max(4, round(pill_height * 0.42))
    pill_size = (pill_width, pill_height)
    mask = _rounded_mask(pill_size, radius)

    body = Image.new("RGBA", pill_size, (0, 0, 0, 0))
    base_alpha = 214 if chosen == "aurora" else 165
    base = Image.new("RGBA", pill_size, (*NAVY, base_alpha))
    body.alpha_composite(base)
    if chosen == "aurora":
        body.alpha_composite(_gradient_layer(pill_size, 132))
        body.alpha_composite(_highlight_layer(pill_size))
    body.putalpha(
        Image.composite(
            body.getchannel("A"),
            Image.new("L", pill_size, 0),
            mask,
        )
    )

    # The halo is blurred on its own oversized canvas so the glow fades to
    # zero instead of being clipped into a rectangle at the chip border.
    blur = max(3, round(pill_height * 0.26)) if chosen == "aurora" else 0
    pad = blur + round(pill_height * 0.16) if blur else 0
    canvas = (pill_width + pad * 2, pill_height + pad * 2)
    chip = Image.new("RGBA", canvas, (0, 0, 0, 0))
    if blur:
        spread = blur * 2
        halo_mask = Image.new("L", (pill_width + spread * 2, pill_height + spread * 2), 0)
        halo_mask.paste(_rounded_mask(pill_size, radius), (spread, spread))
        halo_mask = halo_mask.filter(ImageFilter.GaussianBlur(blur))
        halo = Image.new("RGBA", (pill_width + spread * 2, pill_height + spread * 2), (0, 0, 0, 0))
        halo.paste(Image.new("RGBA", halo.size, (*GLOW, 118)), (0, 0), halo_mask)
        chip.alpha_composite(halo, (pad - spread, pad - spread))
    chip.alpha_composite(body, (pad, pad))

    draw = ImageDraw.Draw(chip)
    if chosen == "aurora":
        draw.rounded_rectangle(
            (pad, pad, pad + pill_width - 1, pad + pill_height - 1),
            radius=radius,
            outline=(255, 255, 255, 66),
            width=max(1, round(pill_height * 0.022)),
        )
    text_x = pad + pad_x
    if mark_radius:
        _hexagon_mark(
            draw,
            (pad + pad_x + mark_radius, pad + pill_height / 2),
            mark_radius,
        )
        text_x += mark_radius * 2 + mark_gap
    text_y = pad + (pill_height - text_height) / 2 - box[1]
    draw.text((text_x + 1, text_y + 2), text, font=font, fill=(0, 0, 0, 110))
    draw.text(
        (text_x, text_y),
        text,
        font=font,
        fill=(255, 255, 255, 246) if chosen == "aurora" else (255, 255, 255, 240),
    )
    return chip, pad


def render_chip_file(
    path: str,
    label: str,
    *,
    max_side: int = 0,
    style: str = "aurora",
) -> bool:
    """Write the brand mark as a transparent PNG for the video overlay."""
    height = int(max_side or 0)
    chip, _ = render_chip(label, height=height or 72, style=style)
    if chip is None:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        chip.save(path, format="PNG")
    except (OSError, ValueError) as error:
        LOGGER.warning("brand chip could not be written to %s: %s", path, error)
        return False
    return True


def apply_brand_overlay(
    image_path: str,
    label: str,
    *,
    position: str = "bottom-right",
    font_path: str | None = None,
    style: str = "aurora",
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
        from PIL import Image
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
    chip, pad = render_chip(
        text,
        font_path=font_path,
        height=max(24, round(64 * scale)),
        style=style,
    )
    if chip is None:
        return False
    chip_width = chip.width - pad * 2
    chip_height = chip.height - pad * 2
    margin = max(8, round(24 * scale))
    if chip_width > width or chip_height > height:
        LOGGER.warning("brand overlay skipped for %s: image smaller than the chip", image_path)
        return False
    anchor_x, anchor_y = _POSITION_ANCHORS[position]
    left = round(width * anchor_x - chip_width - margin) if anchor_x else round(margin)
    top = round(height * anchor_y - chip_height - margin) if anchor_y else round(margin)
    left = max(0, min(left, width - chip_width))
    top = max(0, min(top, height - chip_height))
    rgba.alpha_composite(chip, (left - pad, top - pad))
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
