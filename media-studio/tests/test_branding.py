"""Tests for the optional brand chip overlay on raster artifacts."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest

try:
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only without Pillow
    Image = None

from media_studio.branding import apply_brand_overlay, render_chip, render_chip_file

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _white_png_bytes(width: int = 240, height: int = 160) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class BrandingOverlayTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="ms-brand-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, name: str = "image.png", raw: bytes | None = None) -> str:
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(raw if raw is not None else _white_png_bytes())
        return path

    def test_blank_label_returns_false(self):
        path = self._write()
        self.assertFalse(apply_brand_overlay(path, "   "))
        with open(path, "rb") as handle:
            self.assertTrue(handle.read().startswith(PNG_HEADER))

    def test_overlay_draws_chip_in_bottom_right_corner(self):
        path = self._write()
        self.assertTrue(apply_brand_overlay(path, "Locallab"))
        image = Image.open(path).convert("RGB")
        width, height = image.size
        corner = image.crop((width * 3 // 4, height * 3 // 4, width, height))
        self.assertTrue(any(pixel[0] < 120 for pixel in corner.getdata()))

    def test_position_is_configurable(self):
        path = self._write()
        self.assertTrue(apply_brand_overlay(path, "Locallab", position="top-left"))
        image = Image.open(path).convert("RGB")
        width, height = image.size
        top_left = image.crop((0, 0, width // 4, height // 4))
        bottom_right = image.crop((width * 3 // 4, height * 3 // 4, width, height))
        self.assertTrue(any(pixel[0] < 120 for pixel in top_left.getdata()))
        self.assertFalse(any(pixel[0] < 120 for pixel in bottom_right.getdata()))

    def test_unknown_position_returns_false(self):
        path = self._write()
        self.assertFalse(apply_brand_overlay(path, "Locallab", position="middle"))
        with open(path, "rb") as handle:
            self.assertTrue(handle.read().startswith(PNG_HEADER))

    def test_missing_or_corrupt_file_returns_false(self):
        missing = os.path.join(self.dir, "nope.png")
        self.assertFalse(apply_brand_overlay(missing, "Locallab"))
        path = self._write(name="broken.jpg", raw=b"not an image at all")
        self.assertFalse(apply_brand_overlay(path, "Locallab"))

    def test_overlay_keeps_jpeg_format(self):
        image = Image.new("RGB", (200, 140), "white")
        path = os.path.join(self.dir, "photo.jpg")
        image.save(path, format="JPEG", quality=92)
        self.assertTrue(apply_brand_overlay(path, "Locallab"))
        with Image.open(path) as opened:
            self.assertEqual(opened.format, "JPEG")


if __name__ == "__main__":
    unittest.main()


class BrandChipStyleTests(unittest.TestCase):
    """The aurora style is the LocalLab mark: gradient pill plus halo."""

    def test_aurora_chip_carries_the_gradient_and_a_fading_halo(self):
        chip, pad = render_chip("Locallab", height=72)
        self.assertIsNotNone(chip)
        self.assertGreater(pad, 0)
        alpha = chip.getchannel("A")
        self.assertEqual(alpha.getpixel((0, 0)), 0)
        self.assertGreater(alpha.getpixel((pad + 2, chip.height // 2)), 200)
        pixels = list(chip.convert("RGB").crop((pad, pad, chip.width - pad, chip.height - pad)).getdata())
        self.assertTrue(any(pixel[1] > 90 and pixel[2] > 110 and pixel[0] < 80 for pixel in pixels))
        self.assertTrue(any(pixel[0] > 100 and pixel[2] > 70 and pixel[1] < 80 for pixel in pixels))
        self.assertTrue(any(max(pixel) < 60 for pixel in pixels))

    def test_legacy_chip_style_stays_flat(self):
        chip, pad = render_chip("Locallab", height=72, style="chip")
        self.assertIsNotNone(chip)
        self.assertEqual(pad, 0)
        pixels = list(chip.convert("RGB").getdata())
        self.assertTrue(any(max(pixel) < 60 for pixel in pixels))
        self.assertFalse(any(pixel[1] > 90 and pixel[2] > 110 and pixel[0] < 80 for pixel in pixels))

    def test_unknown_style_falls_back_to_aurora(self):
        chip, pad = render_chip("Locallab", height=72, style="does-not-exist")
        self.assertGreater(pad, 0)

    def test_blank_label_produces_no_chip(self):
        chip, pad = render_chip("   ")
        self.assertIsNone(chip)
        self.assertEqual(pad, 0)

    def test_render_chip_file_writes_a_transparent_png(self):
        path = os.path.join(tempfile.mkdtemp(prefix="ms-chip-"))
        self.addCleanup(shutil.rmtree, path, True)
        target = os.path.join(path, "chip.png")
        self.assertTrue(render_chip_file(target, "Locallab", max_side=64))
        image = Image.open(target)
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getchannel("A").getpixel((0, 0)), 0)
