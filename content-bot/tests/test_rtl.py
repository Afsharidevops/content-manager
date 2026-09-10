"""Unit tests for the shared bidirectional-text helpers."""

from __future__ import annotations

import unittest

from content_bot import rtl


class RtlHelperTests(unittest.TestCase):
    def test_latin_first_line_with_persian_needs_a_mark(self):
        self.assertTrue(rtl.needs_rtl_mark("Hercules ابزار خوبی است"))
        self.assertFalse(rtl.needs_rtl_mark("ساخت اپلیکیشن با Hercules"))
        self.assertFalse(rtl.needs_rtl_mark("Only English words here."))
        self.assertFalse(rtl.needs_rtl_mark(""))

    def test_mark_lines_keeps_blank_lines_and_skips_english(self):
        marked = rtl.mark_lines("Hercules ابزار است\n\nOnly English")
        self.assertEqual(marked.splitlines()[0], f"{rtl.RTL_MARK}Hercules ابزار است")
        self.assertEqual(marked.splitlines()[1], "")
        self.assertEqual(marked.splitlines()[2], "Only English")

    def test_caption_head_is_marked_once(self):
        head = rtl.mark_caption_head("ساخت اپلیکیشن با Hercules")
        self.assertEqual(head, f"{rtl.RTL_MARK}ساخت اپلیکیشن با Hercules")
        self.assertEqual(rtl.mark_caption_head(head), head)
        self.assertEqual(rtl.mark_caption_head("English title"), "English title")


if __name__ == "__main__":
    unittest.main()
