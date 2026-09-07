"""Unit tests for deterministic editorial candidate scoring."""

from __future__ import annotations

from copy import deepcopy

from content_pipeline.config import load_policy
from content_pipeline.score import freshness_score, rank_candidates, score_item

from testkit import NOW, hours_ago, iso, make_item


def _policy() -> dict:
    return deepcopy(load_policy())


def test_absent_timestamp_scores_neutral_freshness():
    item = make_item("No timestamp", "https://example.com/no-time")
    assert freshness_score(item, now=NOW) == 50.0


def test_fresh_item_scores_full_freshness_and_stale_item_scores_zero():
    policy = _policy()
    fresh = make_item("Fresh", "https://example.com/fresh", published_at=iso(hours_ago(1)))
    stale = make_item("Stale", "https://example.com/stale", published_at=iso(hours_ago(100)))
    window = int(policy["freshness_hours"])
    assert freshness_score(fresh, freshness_hours=window, now=NOW) > 90.0
    assert freshness_score(stale, freshness_hours=window, now=NOW) == 0.0


def test_untimestamped_item_scores_neutral_base():
    policy = _policy()
    item = make_item("Neutral signals", "https://example.com/neutral")
    assert score_item(item, policy, now=NOW) == 50


def test_configured_penalty_is_applied():
    policy = _policy()
    item = make_item(
        "Penalized item",
        "https://example.com/penalized",
        published_at=iso(hours_ago(1)),
        normalize=False,
    )
    item["penalties"] = ["no_primary_source"]
    penalty = int(policy["scoring"]["penalties"]["no_primary_source"])
    assert score_item(item, policy, now=NOW) == max(0, 57 + penalty)


def test_rank_candidates_sorts_by_score_and_caps_limit():
    policy = _policy()
    strong = make_item(
        "Strong candidate",
        "https://example.com/strong",
        published_at=iso(hours_ago(1)),
        normalize=False,
    )
    strong["signals"] = {"local_lab_relevance": 100, "practical_usefulness": 100}
    weak = make_item(
        "Weak candidate",
        "https://example.com/weak",
        published_at=iso(hours_ago(50)),
        normalize=False,
    )
    weak["penalties"] = ["pure_marketing", "too_niche"]
    ranked = rank_candidates([weak, strong], policy, now=NOW, limit=1)
    assert len(ranked) == 1
    assert ranked[0]["url"] == "https://example.com/strong"
    assert ranked[0]["score"] == 82
    full = rank_candidates([weak, strong], policy, now=NOW)
    assert full[1]["score"] == 2
