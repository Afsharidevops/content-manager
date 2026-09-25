"""Notebook editor audit tests."""

from __future__ import annotations

import unittest

from app.config import Settings
from app.notebook import (
    CREATE_MODAL_SUBMIT_SELECTORS,
    NotebookEditor,
    _is_studio_collapse_control,
)


class BrokenPage:
    def evaluate(self, *_args, **_kwargs):
        raise RuntimeError("page died")


class SourceCountAuditTests(unittest.TestCase):
    def test_source_count_exception_is_zero_not_negative(self):
        editor = NotebookEditor(BrokenPage(), Settings())
        self.assertEqual(0, editor.source_count())

class CreateModalSelectorTests(unittest.TestCase):
    def test_submit_selectors_do_not_match_fast_research(self):
        self.assertFalse(
            any("search" in selector.casefold() for selector in CREATE_MODAL_SUBMIT_SELECTORS)
        )


class StudioPanelTests(unittest.TestCase):
    def test_collapse_label_is_not_clicked(self):
        label = "\u062c\u0645\u0639 \u06a9\u0631\u062f\u0646 \u067e\u0627\u0646\u0644 \u0627\u0633\u062a\u0648\u062f\u06cc\u0648"
        self.assertTrue(_is_studio_collapse_control(label))

    def test_expand_label_is_not_treated_as_collapse(self):
        self.assertFalse(_is_studio_collapse_control("Show Studio panel"))
