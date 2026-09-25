"""Tests for video module standalone helpers that do not need a Playwright page.

Functions that interact with Playwright locators are tested with mocks.
Actual Playwright integration is validated via the runner automation tests.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

from app import video as v
from app import ui


class ParseCountTests(unittest.TestCase):
    def test_english_count(self):
        self.assertEqual(v._parse_count("3 sources selected"), 3)

    def test_persian_text(self):
        self.assertEqual(v._parse_count("۱۰ منبع انتخاب شده"), 10)

    def test_persian_arabic_numerals(self):
        for numeral in ("۰", "١", "٢"):
            text = f"{numeral} منبع"
            self.assertGreaterEqual(v._parse_count(text), 0)

    def test_missing_count(self):
        self.assertEqual(v._parse_count("No sources"), 0)

    def test_empty(self):
        self.assertEqual(v._parse_count(""), 0)


class CardSelectedTests(unittest.TestCase):
    def test_aria_selected_true(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(side_effect=lambda attr: {
            "aria-selected": "true",
            "class": "card selected-item",
            "aria-pressed": "false",
            "aria-checked": "false",
        }.get(attr, ""))
        card.text_content.return_value = "Card"
        self.assertTrue(v._card_selected(card))

    def test_aria_selected_false(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(side_effect=lambda attr: {
            "aria-selected": "false",
            "class": "card",
            "aria-pressed": "false",
            "aria-checked": "false",
        }.get(attr, ""))
        card.text_content.return_value = "Card"
        self.assertFalse(v._card_selected(card))

    def test_aria_pressed_true(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(side_effect=lambda attr: {
            "aria-pressed": "true",
            "class": "card",
            "aria-selected": "false",
            "aria-checked": "false",
        }.get(attr, ""))
        card.text_content.return_value = "Card"
        # aria-selected takes precedence over aria-pressed in our implementation
        self.assertFalse(v._card_selected(card))


class RegexHelperTests(unittest.TestCase):
    def test_basic_regex_creation(self):
        pattern = v._regex(("Download", "دانلود", "بارگیری"))
        for word in ("Download", "download", "دانلود", "بارگیری"):
            self.assertIsNotNone(pattern.search(word))

    def test_no_match(self):
        pattern = v._regex(("Anime",))
        self.assertIsNone(pattern.search("Classic"))


class VideoCardIdentityTests(unittest.TestCase):
    def test_data_artifact_id(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(side_effect=lambda attr: "vid-123" if attr == "data-artifact-id" else "")
        card.text_content.return_value = ""
        identity = v._card_identity(card, 0)
        self.assertEqual(identity, "data-artifact-id:vid-123")

    def test_fallback_id(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(return_value="")
        card.text_content.return_value = "مرور ویدیویی"
        identity = v._card_identity(card, 1)
        self.assertTrue(identity.startswith("fallback:"))

    def test_text_precedence(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(side_effect=lambda attr: "my-test-id" if attr == "data-testid" else "")
        card.text_content.return_value = ""
        identity = v._card_identity(card, 2)
        self.assertTrue(identity.startswith("data-testid:"))


class CardSignatureTests(unittest.TestCase):
    def test_signature_changes_with_busy(self):
        card = mock.Mock()
        card.locator.return_value.count.return_value = 1
        card.text_content.return_value = "Generating..."
        card.get_attribute.return_value = "card-class"
        sig1 = v._card_signature(card)
        card.locator.return_value.count.return_value = 0
        sig2 = v._card_signature(card)
        self.assertNotEqual(sig1, sig2)


class SlotTests(unittest.TestCase):
    """Integration checks that VideoStyle and GenerationTracker work together."""

    def test_video_style_creation(self):
        vs = v.VideoStyle(label="Anime", key="anime", slug="anime")
        self.assertEqual(vs.label, "Anime")
        self.assertEqual(vs.key, "anime")
        self.assertEqual(vs.slug, "anime")
        self.assertTrue(vs)

    def test_generation_tracker_roundtrip(self):
        tracker = v.GenerationTracker(
            before={"card-1:abc123": "sig1"},
            style_label="Anime",
            started_at=1000.0,
            card_identity="card-1:abc123",
        )
        self.assertEqual(tracker.style_label, "Anime")
        self.assertEqual(tracker.card_identity, "card-1:abc123")


class HelperFunctionExistenceTests(unittest.TestCase):
    def test_required_functions_exist(self):
        for name in (
            "discover_styles_from_dialog",
            "discover_video_styles",
            "start_video_overview",
            "wait_for_video",
            "download_video",
            "video_ready",
            "snapshot_video_cards",
        ):
            self.assertTrue(hasattr(v, name), f"video module missing {name!r}")

    def test_dangerous_selectors_not_imported(self):
        """Verify button:has(svg) is not used anywhere in video.py"""
        with open(os.path.join(os.path.dirname(v.__file__), "video.py"), encoding="utf-8") as handle:
            content = handle.read()
        self.assertNotIn("button:has(svg)", content)
        self.assertNotIn("role='combobox']:not([role='dialog'])", content)
        self.assertNotIn('"button:has(svg)"', content)


if __name__ == "__main__":
    unittest.main()
    def test_whole_worker_has_no_dangerous_button_has_svg_selector(self):
        root = os.path.dirname(os.path.dirname(v.__file__))
        needle = "button:has" + "(svg)"
        offenders = []
        for dirpath, _dirnames, filenames in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                full_path = os.path.join(dirpath, filename)
                with open(full_path, encoding="utf-8") as handle:
                    if needle in handle.read():
                        offenders.append(os.path.relpath(full_path, root))
        self.assertEqual([], offenders)


class AuditVideoHelperTests(unittest.TestCase):
    def test_video_card_content_uses_nested_aria_description(self):
        described = mock.Mock()
        described.is_visible.return_value = True
        described.get_attribute.side_effect = lambda attribute: {
            "aria-description": "مرور ویدیویی",
            "aria-label": "",
        }.get(attribute, "")
        described_locator = mock.Mock()
        described_locator.count.return_value = 1
        described_locator.nth.return_value = described

        card = mock.Mock()
        card.text_content.return_value = "videocam 5:21 توضیح‌دهنده"
        card.get_attribute.return_value = ""
        card.locator.return_value = described_locator

        self.assertIn(
            ui.normalize_label("مرور ویدیویی"),
            v._video_card_content(card),
        )

    def test_card_label_strips_material_check_prefix_without_space(self):
        card = mock.Mock()
        card.get_attribute.return_value = ""
        card.text_content.return_value = "checkانیمه"
        self.assertEqual("انیمه", v._card_label(card))

    def test_language_can_be_verified_from_dialog_text(self):
        page = mock.Mock()
        dialog = mock.Mock()
        dialog.get_by_role.return_value.count.return_value = 0
        dialog.locator.return_value.count.return_value = 0
        dialog.text_content.return_value = " ".join(
            (ui.labels("language")[1], ui.labels("persian")[1])
        )
        v._select_video_language(page, dialog)
        self.assertNotIn(mock.call("combobox"), dialog.get_by_role.mock_calls)

    def test_style_control_button_is_not_a_style_card(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(side_effect=lambda attr: {
            "aria-label": "Next",
            "role": "button",
            "class": "carousel-next",
            "data-value": "",
            "data-option": "",
        }.get(attr, ""))
        card.text_content.return_value = "Next"
        self.assertFalse(v._is_style_card(card))

    def test_real_style_radio_is_style_card(self):
        card = mock.Mock()
        card.get_attribute = mock.Mock(side_effect=lambda attr: {
            "aria-label": "Anime",
            "role": "radio",
            "class": "style-card",
            "data-value": "anime",
            "data-option": "",
        }.get(attr, ""))
        card.text_content.return_value = "Anime"
        self.assertTrue(v._is_style_card(card))

    def test_collision_filename_pattern(self):
        self.assertEqual(ui.safe_style_slug("Anime"), "anime")
        self.assertEqual(ui.safe_style_slug("Anime"), ui.safe_style_slug(" anime "))
