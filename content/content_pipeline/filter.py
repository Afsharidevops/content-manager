"""Deterministic content filtering.

Runs before any model call and before any shortlist is built.  Rejects:

- stale items (outside the configured freshness window);
- items whose canonical host is blocked;
- items whose normalized title matches a low-value marketing keyword;
- items that hit a topic blocklist (political / religious / controversial).

No network access; every check is a pure function of the item and the policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

# Order of the term groups checked on each item; also used as filter reasons.
TOPIC_GROUPS = ("political_terms", "religious_terms", "controversial_terms")

FRESHNESS_REASON = "stale"
DOMAIN_REASON = "blocked_domain"
LOW_VALUE_REASON = "low_value"
TOPIC_REASON = "topic_blocklist"


def _coerce_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def parse_datetime(value) -> datetime | None:
    """Parse ISO-8601 or RFC-2822 timestamps into an aware datetime, else None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _coerce_aware(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return _coerce_aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        return _coerce_aware(parsedate_to_datetime(text))
    except (TypeError, ValueError, IndexError):
        return None


def item_age_hours(item: dict, now: datetime | None = None) -> float | None:
    """Hours between the item's publication time and ``now`` (UTC)."""
    stamp = parse_datetime(
        item.get("published_at")
        or item.get("published")
        or item.get("date")
        or item.get("updated")
    )
    if stamp is None:
        return None
    reference = _coerce_aware(now) if now is not None else datetime.now(timezone.utc)
    return max(0.0, (reference - stamp).total_seconds() / 3600.0)


def is_fresh(item: dict, freshness_hours: int = 72, now: datetime | None = None) -> bool:
    """True when the item is inside the freshness window.

    Items without a parseable timestamp are kept: we cannot prove they are
    stale, and silently dropping them would lose real candidates.
    """
    age = item_age_hours(item, now=now)
    if age is None:
        return True
    return age <= freshness_hours


def item_host(item: dict) -> str:
    url = str(item.get("canonical_url") or item.get("url") or item.get("link") or "")
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_blocked_domain(item: dict, blocked_domains) -> bool:
    """Exact-host or subdomain-suffix match against the blocked list."""
    host = item_host(item)
    if not host:
        return False
    for domain in blocked_domains or []:
        domain = str(domain).strip().lower()
        if domain and (host == domain or host.endswith("." + domain)):
            return True
    return False


def matches_low_value(item: dict, keywords) -> bool:
    """True when a low-value keyword appears in the normalized title."""
    title = str(item.get("title_norm") or "")
    if not title:
        return False
    return any(str(keyword).strip().casefold() in title for keyword in keywords or [])


def _topic_text(item: dict) -> str:
    parts = []
    for key in ("title", "title_norm", "summary", "description", "excerpt", "category", "tags"):
        value = item.get(key)
        if isinstance(value, (list, tuple)):
            value = " ".join(str(part) for part in value)
        if value:
            parts.append(str(value))
    return "\n".join(parts).casefold()


def topic_hit(item: dict, policy: dict) -> tuple | None:
    """Return ``(group, matched_term)`` on the first blocklist hit, else None."""
    text = _topic_text(item)
    if not text:
        return None
    exclusions = policy.get("exclusions") or {}
    for group in TOPIC_GROUPS:
        for term in exclusions.get(group) or []:
            term_text = str(term).strip()
            if term_text and term_text.casefold() in text:
                return group, term_text
    return None


def filter_items(
    policy: dict,
    items,
    *,
    now: datetime | None = None,
    enforce_freshness: bool = True,
):
    """Return ``(kept, rejected)``.

    ``rejected`` is a list of ``{"item", "reason", "detail"}`` records so that
    callers can log, audit, and (for transient reasons) retry.
    """
    kept: list = []
    rejected: list = []

    freshness_hours = int(policy.get("freshness_hours", 72))
    exclusions = policy.get("exclusions") or {}
    blocked = exclusions.get("blocked_domains") or []
    low_value = exclusions.get("low_value_title_keywords") or []

    for item in items:
        if enforce_freshness and not is_fresh(item, freshness_hours=freshness_hours, now=now):
            age = item_age_hours(item, now=now)
            rejected.append(
                {"item": item, "reason": FRESHNESS_REASON, "detail": f"age_hours={age:.1f}"}
            )
            continue

        if is_blocked_domain(item, blocked):
            rejected.append(
                {"item": item, "reason": DOMAIN_REASON, "detail": item_host(item)}
            )
            continue

        if matches_low_value(item, low_value):
            rejected.append(
                {"item": item, "reason": LOW_VALUE_REASON, "detail": str(item.get("title") or "")}
            )
            continue

        hit = topic_hit(item, policy)
        if hit is not None:
            group, term = hit
            rejected.append(
                {"item": item, "reason": TOPIC_REASON, "detail": f"{group}:{term}"}
            )
            continue

        kept.append(item)

    return kept, rejected
