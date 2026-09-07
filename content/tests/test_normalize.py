"""Unit tests for deterministic URL/title normalization."""

from __future__ import annotations

from content_pipeline.normalize import (
    canonicalize_url,
    normalize_item,
    normalize_title,
)


def test_canonicalize_drops_tracking_and_fragment_and_sorts():
    url = "https://Example.com/Path?a=1&b=0&utm_source=rss&fbclid=xyz#section"
    assert canonicalize_url(url) == "https://example.com/Path?a=1&b=0"


def test_canonicalize_strips_www_and_default_ports():
    assert canonicalize_url("https://www.example.com:443/a/") == "https://example.com/a/"
    assert canonicalize_url("http://example.com:80") == "http://example.com/"


def test_non_tracking_params_are_preserved():
    url = "https://example.com/watch?v=abc123&list=xyz&utm_medium=email"
    # Tracking params are dropped; the remaining ones are kept (sorted).
    assert canonicalize_url(url) == "https://example.com/watch?list=xyz&v=abc123"


def test_tracking_variants_collapse_to_one_canonical_url():
    left = canonicalize_url("https://github.com/org/proj/releases/tag/v1.2?utm_source=hn")
    right = canonicalize_url("https://github.com/org/proj/releases/tag/v1.2?utm_medium=email")
    assert left == right == "https://github.com/org/proj/releases/tag/v1.2"


def test_title_normalization_casefold_punctuation_whitespace():
    assert (
        normalize_title("  State-of-the-Art: v2.0 Is OUT!  ")
        == "state of the art v2 0 is out"
    )


def test_title_normalization_removes_diacritic_lookalikes_via_nfc():
    # The two strings are canonically equivalent after NFC + casefold.
    assert normalize_title("café") == normalize_title("café")


def test_normalize_item_adds_canonical_fields_without_mutating_input():
    raw = {
        "source_id": "s1",
        "title": "Hello, World!",
        "url": "https://a.example/x?utm_source=rss",
        "published_at": "2026-09-01T08:00:00Z",
    }
    out = normalize_item(raw)
    assert out["canonical_url"] == "https://a.example/x"
    assert out["title_norm"] == "hello world"
    assert len(out["content_hash"]) == 64
    assert "canonical_url" not in raw  # input untouched
