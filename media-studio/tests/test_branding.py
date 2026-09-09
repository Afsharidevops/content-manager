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

from media_studio.branding import apply_brand_overlay

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
