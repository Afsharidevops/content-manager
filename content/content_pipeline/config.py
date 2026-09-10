"""YAML policy loading.

Files under config/ are the owner-editable source of truth.  load_policy() and
load_categories() read them and merge them over minimal code defaults so that a
missing key degrades to a safe empty value instead of crashing the filters.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
DEFAULT_POLICY_PATH = CONFIG_DIR / "editorial-policy.yaml"
DEFAULT_CATEGORIES_PATH = CONFIG_DIR / "categories.yaml"

_POLICY_DEFAULTS = {
    "schema_version": 1,
    "pipeline": {
        "timezone": "Asia/Tehran",
        "daily_proposal_time": "08:00",
        "min_candidates": 5,
        "max_candidates": 5,
        "max_approved_per_day": 3,
        "max_consecutive_same_category": 3,
    },
    "dedupe": {"title_similarity_threshold": 0.90},
    "routines": [],
    "freshness_hours": 72,
    "scoring": {
        "weights": {
            "local_lab_relevance": 30,
            "practical_usefulness": 20,
            "freshness": 15,
            "source_trust": 15,
            "novelty": 10,
            "visual_fit": 5,
            "iranian_audience": 5,
        },
        "penalties": {
            "duplicate_topic": -25,
            "pure_marketing": -30,
            "no_primary_source": -40,
            "too_niche": -15,
            "unsafe_guidance": -100,
        },
    },
    "exclusions": {
        "blocked_domains": [],
        "low_value_title_keywords": [],
        "political_terms": [],
        "religious_terms": [],
        "controversial_terms": [],
    },
}


def load_yaml(path) -> object:
    """Read one YAML file into plain Python objects."""
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto a copy of ``base``."""
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_policy(path=DEFAULT_POLICY_PATH) -> dict:
    """Load the editorial policy, merged over safe code defaults."""
    data = load_yaml(path) if Path(path).exists() else {}
    return _deep_merge(_POLICY_DEFAULTS, data)


def load_categories(path=DEFAULT_CATEGORIES_PATH) -> list:
    """Load the category list (as plain dicts) or an empty list."""
    if not Path(path).exists():
        return []
    data = load_yaml(path) or {}
    return data.get("categories", [])
