"""Deterministic deduplication of normalized content items.

Operates on items already enriched by content_pipeline.normalize.  Two rules:

1. exact canonical-URL equality (same article fetched from several sources);
2. near-duplicate normalized titles above a similarity threshold (repeat
   release announcements, republished posts with lightly edited titles).

Everything is deterministic and offline.
"""

from __future__ import annotations

from difflib import SequenceMatcher


def title_similarity(left: str, right: str) -> float:
    """Ratcliff-Obershelp similarity in ``[0, 1]`` for two normalized titles."""
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def dedupe_items(
    items,
    *,
    url_key: str = "canonical_url",
    title_key: str = "title_norm",
    title_threshold: float = 0.90,
):
    """Return ``(kept, dropped)`` where ``dropped`` explains each removal.

    Keeps the first occurrence in input order; every later duplicate is listed
    in ``dropped`` as ``{"item", "reason", "key"}`` with ``reason`` one of
    ``"duplicate_url"`` or ``"duplicate_title"``.
    """
    kept: list = []
    dropped: list = []
    seen_urls: set = set()

    for item in items:
        url = str(item.get(url_key) or "").strip()
        if url and url in seen_urls:
            dropped.append({"item": item, "reason": "duplicate_url", "key": url})
            continue

        title = str(item.get(title_key) or "")
        duplicate_key = None
        if title:
            for candidate in kept:
                candidate_title = str(candidate.get(title_key) or "")
                if candidate_title and title_similarity(title, candidate_title) >= title_threshold:
                    duplicate_key = candidate_title
                    break
        if duplicate_key is not None:
            dropped.append(
                {"item": item, "reason": "duplicate_title", "key": duplicate_key}
            )
            continue

        if url:
            seen_urls.add(url)
        kept.append(item)

    return kept, dropped
