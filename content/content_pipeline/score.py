"""Deterministic candidate scoring for the editorial policy.

Runs after normalization, deduplication, and filtering.  Each policy weight is
scored on a 0-100 scale: ``freshness`` is derived from the item timestamp, and
the remaining quality signals come from ``item["signals"]`` when present or
fall back to a conservative neutral value so scoring never crashes on raw
discovery output.  Configured penalties are applied when the item carries the
matching flag in ``item["penalties"]``.

Pure and offline; no network access and no model calls.
"""

from __future__ import annotations

from content_pipeline.filter import item_age_hours

DEFAULT_SIGNAL = 50.0


def freshness_score(item: dict, *, freshness_hours: int = 72, now=None) -> float:
    """Map item age to a 0-100 freshness score.

    Items without a parseable timestamp receive the neutral 50: the freshness
    filter keeps them because staleness cannot be proven, and scoring should
    not silently rank them as brand new either.
    """
    age = item_age_hours(item, now=now)
    if age is None:
        return DEFAULT_SIGNAL
    if freshness_hours <= 0:
        return 0.0
    return max(0.0, 100.0 * (1.0 - age / float(freshness_hours)))


def signal_value(item: dict, key: str, default: float = DEFAULT_SIGNAL) -> float:
    """Read one model/editorial signal (0-100) or return the neutral default."""
    signals = item.get("signals")
    if isinstance(signals, dict):
        value = signals.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return float(default)


def _clamp_score(value: float) -> int:
    return int(max(0, min(100, round(value))))


def score_item(item: dict, policy: dict, *, now=None) -> int:
    """Return the deterministic editorial score (0-100) for one item."""
    scoring = policy.get("scoring") or {}
    weights = scoring.get("weights") or {}
    freshness_hours = int(policy.get("freshness_hours", 72))

    weighted = 0.0
    weight_sum = 0.0
    for key, weight in weights.items():
        weight = float(weight)
        if key == "freshness":
            value = freshness_score(item, freshness_hours=freshness_hours, now=now)
        else:
            value = signal_value(item, key)
        weighted += value * weight
        weight_sum += weight
    base = (weighted / weight_sum) if weight_sum else 0.0

    penalties = 0.0
    flagged = set(item.get("penalties") or [])
    configured = scoring.get("penalties") or {}
    for name, value in configured.items():
        if name in flagged:
            penalties += float(value)

    return _clamp_score(base + penalties)


def rank_candidates(items, policy, *, now=None, limit=None):
    """Score and sort candidates by descending score, preserving input order for ties."""
    ranked = []
    for item in items:
        copy = dict(item)
        copy["score"] = score_item(item, policy, now=now)
        ranked.append(copy)
    ranked.sort(key=lambda item: item["score"], reverse=True)
    if limit is not None:
        ranked = ranked[: int(limit)]
    return ranked
