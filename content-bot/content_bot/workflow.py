"""Editorial workflow helpers shared by on-demand and daily runs."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from content_pipeline.config import load_policy as _load_merged_policy
from content_pipeline.dedupe import dedupe_items
from content_pipeline.filter import filter_items
from content_pipeline.normalize import normalize_items
from content_pipeline.score import rank_candidates
from content_pipeline.tools import Registry, ToolRegistryError, load_registry

log = logging.getLogger("content_bot")

REJECTION_LABELS = {
    "stale": "older than the configured freshness window",
    "blocked_domain": "blocked source domain",
    "low_value": "low-value marketing-style title",
    "topic_blocklist": "blocked topic ({detail})",
    "duplicate_url": "duplicate of an already known URL",
    "duplicate_title": "near-duplicate of an already known item",
}


def load_policy(policy_dir: str | Path) -> dict:
    path = Path(policy_dir) / "editorial-policy.yaml"
    if path.is_file():
        return _load_merged_policy(str(path))
    return _load_merged_policy()


def load_tools(policy_dir: str | Path) -> Registry | None:
    """Load the shared tool registry when the deployment ships one.

    A missing file is normal (the registry is optional); an unreadable or
    invalid one is worth a warning but must never stop the bot.
    """
    path = Path(policy_dir) / "tools.json"
    if not path.is_file():
        return None
    try:
        return load_registry(path)
    except ToolRegistryError as error:
        log.warning("tool registry ignored: %s", error)
        return None


def load_sources(policy_dir: str | Path) -> list[dict]:
    """Load configured RSS/Atom sources, skipping unusable entries."""
    path = Path(policy_dir) / "sources.yaml"
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    sources = data.get("sources") or []
    return [
        {"name": str(source.get("name") or source.get("url")), "url": str(source.get("url") or "")}
        for source in sources
        if isinstance(source, dict) and str(source.get("url") or "").startswith(("http://", "https://"))
    ]


def rejection_label(record: dict) -> str:
    template = REJECTION_LABELS.get(record.get("reason"), record.get("reason", "rejected"))
    return template.format(detail=record.get("detail", ""))


ON_DEMAND_DEFAULTS = {
    "enforce_freshness": False,
    "unlimited_approvals": True,
}


def on_demand_settings(policy: dict) -> dict:
    """Return the operator-sent-link policy section with defaults applied."""
    settings = policy.get("on_demand")
    if not isinstance(settings, dict):
        return dict(ON_DEMAND_DEFAULTS)
    merged = dict(ON_DEMAND_DEFAULTS)
    for key in ON_DEMAND_DEFAULTS:
        if key in settings:
            merged[key] = settings[key]
    return merged


def evaluate_single(item: dict, policy: dict, *, now=None, enforce_freshness: bool = True):
    """Run normalization plus filtering on one raw item.

    Returns ``(item, None)`` when accepted or ``(None, rejection)`` otherwise.
    """
    normalized = normalize_items([item])[0]
    kept, rejected = filter_items(
        policy,
        [normalized],
        now=now,
        enforce_freshness=enforce_freshness,
    )
    if rejected:
        return None, rejected[0]
    return kept[0], None


def prepare_daily(
    raw_items,
    policy: dict,
    *,
    now=None,
    limit: int | None = None,
) -> dict:
    """Normalize, deduplicate, filter, score, and rank one discovery batch."""
    pipeline = policy.get("pipeline") or {}
    if limit is None:
        limit = int(pipeline.get("max_candidates", 5))
    normalized = normalize_items(raw_items)
    deduped, dropped = dedupe_items(normalized)
    kept, rejected = filter_items(policy, deduped, now=now)
    ranked = rank_candidates(kept, policy, now=now, limit=limit)
    return {
        "candidates": ranked,
        "rejected": rejected,
        "dropped": dropped,
    }


def select_with_category_mix(candidates, last_categories, max_streak: int = 3):
    """Pick candidates without exceeding consecutive same-category publishes.

    ``last_categories`` lists previously published categories in chronological
    order; the streak starts from the most recent entries and is carried over
    into the current batch.  Returns ``(picked, skipped)``.
    """
    streak_category = ""
    streak = 0
    for category in reversed(list(last_categories or [])):
        category = str(category or "")
        if streak_category and category != streak_category:
            break
        streak_category = category
        streak += 1

    picked = []
    skipped = []
    for item in candidates:
        category = str(item.get("category") or "")
        if category and category == streak_category and streak >= max_streak:
            skipped.append(item)
            continue
        if category == streak_category:
            streak += 1
        else:
            streak_category = category
            streak = 1
        picked.append(item)
    return picked, skipped
