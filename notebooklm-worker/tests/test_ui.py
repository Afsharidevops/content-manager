"""Tests for the normalized UI labels and style helpers."""

from __future__ import annotations

import unittest

from app import ui


class NormalizeLabelTests(unittest.TestCase):
    def test_basic_normalization(self):
        self.assertEqual(ui.normalize_label("Persian"), ui.normalize_label("persian"))
    
    def test_persian_arabic_normalization(self):
        for variant in ("انیمه", "انيمه"):
            self.assertEqual(ui.normalize_label(variant), ui.normalize_label("انیمه"))
    
    def test_whitespace_collapse(self):
        self.assertEqual(ui.normalize_label("  Generate  now "), ui.normalize_label("generate now"))


class MatchesLabelTests(unittest.TestCase):
    def test_label_match(self):
        self.assertTrue(ui.matches_label("دانلود", ("Download", "دانلود", "بارگیری")))
    
    def test_no_match(self):
        self.assertFalse(ui.matches_label("Upload", ("Download",)))
    
    def test_empty_value(self):
        self.assertFalse(ui.matches_label("", ("anything",)))


class StyleAliasesTests(unittest.TestCase):
    def test_anime_aliases(self):
        aliases = ui.style_aliases("anime")
        normalized = {ui.normalize_label(a) for a in aliases}
        for variant in ("anime", "انیمه"):
            self.assertIn(ui.normalize_label(variant), normalized)
    
    def test_dynamic_style(self):
        aliases = ui.style_aliases("Cinematic")
        self.assertIn(ui.normalize_label("Cinematic"), {ui.normalize_label(a) for a in aliases})


class StyleMatchesTests(unittest.TestCase):
    def test_anime_match(self):
        self.assertTrue(ui.style_matches("anime", "Anime"))
        self.assertTrue(ui.style_matches("anime", "انیمه"))
    
    def test_no_match(self):
        self.assertFalse(ui.style_matches("anime", "Classic"))


class SafeSlugTests(unittest.TestCase):
    def test_basic_slug(self):
        self.assertEqual(ui.safe_style_slug("Anime"), "anime")
    
    def test_persian_slug(self):
        slug = ui.safe_style_slug("انیمه")
        self.assertTrue(len(slug) > 0)
    
    def test_empty_slug(self):
        slug = ui.safe_style_slug("")
        self.assertTrue(slug.startswith("style-"))


class LabelsAPITests(unittest.TestCase):
    def test_key_labels_exist(self):
        for key in ("insert", "video_overview", "persian", "explainer", "generate_now", "download", "more"):
            labels = ui.labels(key)
            self.assertTrue(len(labels) >= 1, f"labels({key!r}) should have at least 1 entry")
            self.assertTrue(any(isinstance(label, str) and len(label) > 0 for label in labels))


if __name__ == "__main__":
    unittest.main()
