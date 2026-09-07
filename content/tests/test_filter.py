"""Unit tests for deterministic pre-model content filtering."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from content_pipeline.config import load_policy
from content_pipeline.filter import (
    DOMAIN_REASON,
    FRESHNESS_REASON,
    LOW_VALUE_REASON,
    TOPIC_REASON,
    filter_items,
    is_blocked_domain,
    is_fresh,
    item_age_hours,
    matches_low_value,
    parse_datetime,
    topic_hit,
)

from testkit import NOW, hours_ago, iso, make_item


def _policy() -> dict:
    return deepcopy(load_policy())


def test_political_item_rejected_and_technical_item_kept():
    policy = _policy()
    items = [
        make_item(
            "Senate advances landmark AI safety bill to full vote",
            "https://news.example.com/2026/ai-bill",
            published_at=iso(hours_ago(2)),
        ),
        make_item(
            "Run Kubernetes on bare metal with kubeadm",
            "https://kubernetes.example.com/bare-metal",
            published_at=iso(hours_ago(2)),
        ),
    ]
    kept, rejected = filter_items(policy, items, now=NOW)

    assert [item["title"] for item in kept] == [
        "Run Kubernetes on bare metal with kubeadm"
    ]
    assert len(rejected) == 1
    assert rejected[0]["reason"] == TOPIC_REASON
    assert rejected[0]["detail"].startswith("political_terms:")


def test_topic_hit_scans_title_and_reports_group_term():
    policy = _policy()
    hit = topic_hit({"title": "The full election results are in"}, policy)
    assert hit is not None
    group, term = hit
    assert group == "political_terms"
    assert term == "election"


def test_stale_item_rejected_outside_freshness_window():
    policy = _policy()
    item = make_item(
        "Old news by now",
        "https://news.example.com/old",
        published_at=iso(hours_ago(100)),
    )
    kept, rejected = filter_items(policy, [item], now=NOW)
    assert kept == []
    assert rejected[0]["reason"] == FRESHNESS_REASON
    assert "100" in rejected[0]["detail"]


def test_item_at_exact_freshness_boundary_is_kept():
    policy = _policy()
    item = make_item(
        "Borderline but still within the window",
        "https://tech.example.com/borderline",
        published_at=iso(hours_ago(72)),
    )
    kept, rejected = filter_items(policy, [item], now=NOW)
    assert [entry["title"] for entry in kept] == [
        "Borderline but still within the window"
    ]
    assert rejected == []


def test_undated_item_is_not_dropped_as_stale():
    policy = _policy()
    item = make_item("No timestamp available", "https://blog.example.com/undated")
    assert item.get("published_at") is None
    kept, rejected = filter_items(policy, [item], now=NOW)
    assert kept and rejected == []


def test_blocked_domain_rejected():
    policy = _policy()
    policy["exclusions"]["blocked_domains"] = ["example.com"]
    item = make_item(
        "Post from a blocked source",
        "https://sub.example.com/article",
        published_at=iso(hours_ago(1)),
    )
    kept, rejected = filter_items(policy, [item], now=NOW)
    assert kept == []
    assert rejected[0]["reason"] == DOMAIN_REASON
    assert rejected[0]["detail"] == "sub.example.com"


def test_is_blocked_domain_exact_and_suffix():
    blocked = ["example.com"]
    assert is_blocked_domain(make_item("a", "https://example.com/x"), blocked)
    assert is_blocked_domain(make_item("a", "https://news.example.com/x"), blocked)
    assert is_blocked_domain(make_item("a", "https://notexample.com/x"), blocked) is False
    assert is_blocked_domain(make_item("a", "https://example.com.evil.net/x"), blocked) is False
    assert is_blocked_domain(make_item("a", "https://example.org/x"), blocked) is False


def test_low_value_title_rejected():
    policy = _policy()
    item = make_item(
        "Get rich quick with automated trading bots",
        "https://ads.example.com/auto-trading",
        published_at=iso(hours_ago(1)),
    )
    kept, rejected = filter_items(policy, [item], now=NOW)
    assert kept == []
    assert rejected[0]["reason"] == LOW_VALUE_REASON


def test_matches_low_value_on_normalized_title():
    keywords = ["get rich", "free money"]
    assert matches_low_value(make_item("GET RICH overnight", "https://x.example/a"), keywords)
    assert not matches_low_value(
        make_item("A rich history of Unix", "https://x.example/b"), keywords
    )


def test_parse_datetime_variants():
    assert parse_datetime(None) is None
    assert parse_datetime("") is None
    assert parse_datetime("not a date") is None
    assert parse_datetime("2026-09-01T08:00:00Z") == parse_datetime(
        "2026-09-01T08:00:00+00:00"
    )
    rfc = parse_datetime("Mon, 01 Sep 2026 08:00:00 GMT")
    assert rfc is not None
    assert rfc.tzinfo is not None
    naive = parse_datetime("2026-09-01T08:00:00")
    assert naive is not None
    assert naive.utcoffset() == timedelta(0)


def test_item_age_hours_is_non_negative():
    item = make_item(
        "Published slightly in the future",
        "https://news.example.com/future",
        published_at=iso(hours_ago(-1)),
    )
    assert item_age_hours(item, now=NOW) == 0.0


def test_is_fresh_without_timestamp():
    assert is_fresh(make_item("No date", "https://blog.example.com/x"), now=NOW)
