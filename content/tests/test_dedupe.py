"""Unit tests for deterministic deduplication (exact URL + near titles)."""

from __future__ import annotations

from content_pipeline.dedupe import dedupe_items, title_similarity

from testkit import make_item

_BASE = "https://blog.example.com/post/42"


def _reasons(dropped) -> list:
    return sorted(record["reason"] for record in dropped)


def test_title_similarity_edge_cases():
    assert title_similarity("", "anything") == 0.0
    assert title_similarity("anything", "") == 0.0
    assert title_similarity("", "") == 0.0
    assert title_similarity("exact same title", "exact same title") == 1.0
    # Closely related text scores higher than unrelated text.
    related = title_similarity("state of the art v2", "state of the art v3")
    unrelated = title_similarity("state of the art v2", "how to bake sourdough")
    assert related > unrelated


def test_tracking_urls_and_duplicate_titles_collapse_to_unique_items():
    feed_a = make_item(
        "Example Framework v2 Released",
        f"{_BASE}?utm_source=hn",
        source_id="feed_a",
    )
    feed_b = make_item(
        "Example Framework v2 Released",
        f"{_BASE}?utm_medium=email",
        source_id="feed_b",
    )
    feed_c = make_item(
        "Example Framework v2 Released",
        "https://mirror.example.net/2026/example-v2",
        source_id="feed_c",
    )
    feed_d = make_item(
        "Rust 1.82 stabilizes the 2024 edition",
        "https://blog.rust-lang.org/2026/10/15/Rust-1.82.0.html",
        source_id="feed_d",
    )

    kept, dropped = dedupe_items([feed_a, feed_b, feed_c, feed_d])

    assert [item["title"] for item in kept] == [
        "Example Framework v2 Released",
        "Rust 1.82 stabilizes the 2024 edition",
    ]
    # feed_b collapses on the canonical URL; feed_c is a mirror with a
    # near-identical title that still scores above the default threshold.
    assert [record["item"]["source_id"] for record in dropped] == ["feed_b", "feed_c"]
    assert _reasons(dropped) == ["duplicate_title", "duplicate_url"]


def test_near_duplicate_titles_respect_threshold():
    left = make_item(
        "Kubernetes 1.31 brings exciting new features to the platform",
        f"{_BASE}/a",
        source_id="a",
    )
    right = make_item(
        "Kubernetes 1.31 brings exciting new features to the platform today",
        f"{_BASE}/b",
        source_id="b",
    )

    # A loose threshold collapses the lightly-edited second post.
    kept_loose, dropped_loose = dedupe_items([left, right], title_threshold=0.60)
    assert [item["source_id"] for item in kept_loose] == ["a"]
    assert [record["reason"] for record in dropped_loose] == ["duplicate_title"]

    # A strict threshold treats the two posts as distinct articles.
    kept_strict, dropped_strict = dedupe_items([left, right], title_threshold=0.99)
    assert [item["source_id"] for item in kept_strict] == ["a", "b"]
    assert dropped_strict == []
