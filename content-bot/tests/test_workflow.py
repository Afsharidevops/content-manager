"""Tests for editorial workflow helpers."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from content_bot.workflow import (
    evaluate_single,
    load_policy,
    prepare_daily,
    rejection_label,
    select_with_category_mix,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = ROOT / "content" / "config"
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def hours_ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


def make_item(title: str, url: str, hours: float = 1):
    return {
        "title": title,
        "url": url,
        "published_at": hours_ago(hours),
        "summary": f"Summary for {title}",
        "category": "",
        "tags": [],
    }


class WorkflowTest(unittest.TestCase):
    def test_policy_loads_from_working_copy(self):
        policy = load_policy(POLICY_DIR)
        self.assertEqual(policy["pipeline"]["timezone"], "Asia/Tehran")
        self.assertEqual(sum(policy["scoring"]["weights"].values()), 100)

    def test_evaluate_single_keeps_technical_and_rejects_political(self):
        policy = load_policy(POLICY_DIR)
        technical = make_item("Run Kubernetes on bare metal", "https://k8s.example.com/a")
        political = make_item("Senate advances AI safety bill", "https://news.example.com/b")
        kept, rejection = evaluate_single(political, policy, now=NOW)
        self.assertIsNone(kept)
        self.assertEqual(rejection["reason"], "topic_blocklist")
        self.assertIn("political_terms", rejection["detail"])
        kept, rejection = evaluate_single(technical, policy, now=NOW)
        self.assertIsNotNone(kept)
        self.assertIsNone(rejection)
        self.assertIn("canonical_url", kept)

    def test_prepare_daily_deduplicates_filters_and_scores(self):
        policy = load_policy(POLICY_DIR)
        good = make_item("Docker multi-stage build guide", "https://docker.example.com/a")
        duplicate = make_item("Docker multi-stage build guide", "https://docker.example.com/a")
        stale = make_item("Old announcement", "https://old.example.com/x", hours=200)
        political = make_item("Election results overview", "https://news.example.com/e")
        prepared = prepare_daily([good, duplicate, stale, political], policy, now=NOW)
        self.assertEqual(len(prepared["candidates"]), 1)
        self.assertEqual(prepared["candidates"][0]["url"], "https://docker.example.com/a")
        self.assertGreaterEqual(prepared["candidates"][0]["score"], 0)
        self.assertEqual(len(prepared["dropped"]), 1)
        self.assertEqual(len(prepared["rejected"]), 2)

    def test_category_mix_respects_streak(self):
        items = [
            {"category": "ai_tools", "title": "a"},
            {"category": "ai_tools", "title": "b"},
            {"category": "ai_tools", "title": "c"},
            {"category": "mcp", "title": "d"},
        ]
        picked, skipped = select_with_category_mix(items, [], max_streak=1)
        self.assertEqual([item["title"] for item in picked], ["a", "d"])
        self.assertEqual([item["title"] for item in skipped], ["b", "c"])

    def test_rejection_label_renders_detail(self):
        label = rejection_label({"reason": "topic_blocklist", "detail": "political_terms:election"})
        self.assertIn("political_terms:election", label)


if __name__ == "__main__":
    unittest.main()
