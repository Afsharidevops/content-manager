"""Tests for the atomic JSON state store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from content_bot.state import StateStore


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = StateStore(Path(self.tmp.name) / "state.json")

    def test_draft_lifecycle(self):
        self.store.add_draft("d1", {"title": "Draft", "message_id": 3})
        self.assertEqual(self.store.get_draft("d1")["title"], "Draft")
        self.store.drop_draft("d1")
        self.assertIsNone(self.store.get_draft("d1"))

    def test_published_history_and_day_counter(self):
        self.store.remember_published("hash-a", category="ai_tools", day="2026-09-07")
        self.store.remember_published("hash-b", category="", day="2026-09-07")
        self.assertTrue(self.store.is_known("hash-a"))
        self.assertEqual(self.store.load()["published_today"], 2)
        self.store.reset_day("2026-09-08")
        self.assertEqual(self.store.load()["published_today"], 0)
        self.assertEqual(self.store.load()["last_categories"], ["ai_tools"])

    def test_state_survives_reload(self):
        self.store.add_draft("d2", {"title": "Persisted"})
        reloaded = StateStore(Path(self.tmp.name) / "state.json")
        self.assertEqual(reloaded.get_draft("d2")["title"], "Persisted")


if __name__ == "__main__":
    unittest.main()
