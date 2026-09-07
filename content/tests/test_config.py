"""Guard rails: the shipped configs must keep the decided product values."""

from __future__ import annotations

from content_pipeline.config import (
    DEFAULT_CATEGORIES_PATH,
    DEFAULT_POLICY_PATH,
    load_categories,
    load_policy,
)


def test_default_config_files_exist():
    assert DEFAULT_POLICY_PATH.exists()
    assert DEFAULT_CATEGORIES_PATH.exists()


def test_policy_encodes_decided_cadence_and_limits():
    policy = load_policy()
    pipe = policy["pipeline"]
    # exactly five candidates, up to three approved posts/day.
    assert pipe["min_candidates"] == 5
    assert pipe["max_candidates"] == 5
    assert pipe["max_approved_per_day"] == 3
    assert pipe["max_consecutive_same_category"] == 3
    assert pipe["timezone"] == "Asia/Tehran"
    assert pipe["daily_proposal_time"] == "08:00"
    assert policy["freshness_hours"] == 72


def test_policy_scoring_weights_sum_to_100():
    policy = load_policy()
    assert sum(policy["scoring"]["weights"].values()) == 100


def test_policy_has_english_topic_blocklists():
    policy = load_policy()
    exclusions = policy["exclusions"]
    for group in ("political_terms", "religious_terms", "controversial_terms"):
        assert isinstance(exclusions[group], list)
        assert exclusions[group], f"{group} must not be empty"


def test_categories_are_decided_set_without_build_in_public():
    categories = load_categories()
    ids = [category["id"] for category in categories]
    assert len(categories) == 9
    assert len(set(ids)) == len(ids)
    assert sum(category["weight"] for category in categories) == 100
    assert not any("build" in category_id or "locallab" in category_id for category_id in ids)


def test_each_category_is_well_formed():
    for category in load_categories():
        assert category["id"]
        assert category["en_label"]
        assert category["icon"]
        assert category["weight"] > 0
        assert isinstance(category["keywords"], list) and category["keywords"]
