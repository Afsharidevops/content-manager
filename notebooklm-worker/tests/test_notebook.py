"""Notebook editor audit tests."""

from __future__ import annotations

import unittest

from app.config import Settings
from app.notebook import CREATE_MODAL_SUBMIT_SELECTORS, NotebookEditor


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
