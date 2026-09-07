"""Shared helpers for offline tests. Never touches the network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from content_pipeline.normalize import normalize_item

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def hours_ago(hours: float, base: datetime | None = None) -> datetime:
    ref = base or NOW
    return ref - timedelta(hours=hours)


def iso(timestamp: datetime) -> str:
    return timestamp.isoformat()


def make_item(
    title: str,
    url: str,
    *,
    source_id: str = "s1",
    published_at=None,
    summary: str = "",
    category: str = "",
    tags=None,
    normalize: bool = True,
) -> dict:
    """Build a raw item dict and (by default) enrich it with canonical fields."""
    item = {
        "source_id": source_id,
        "title": title,
        "url": url,
        "published_at": published_at,
        "summary": summary,
        "category": category,
        "tags": list(tags or []),
    }
    return normalize_item(item) if normalize else item
