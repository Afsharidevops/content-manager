from __future__ import annotations

from smart_router.eval.preference_data import build_row, choose_preferred_tier


def test_preference_chooses_cheapest_tier_meeting_retention():
    assert choose_preferred_tier(
        {"fast": 0.96, "standard": 0.98, "strong": 1.0}, retention=0.95
    ) == "fast"


def test_preference_respects_capability_floor():
    assert choose_preferred_tier(
        {"fast": 0.99, "standard": 0.97, "strong": 1.0},
        minimum_tier="standard",
        retention=0.95,
    ) == "standard"


def test_build_row_emits_existing_training_shape():
    row = build_row(
        {
            "schema_version": 1,
            "features": {"token_count": 123},
            "minimum_tier": "fast",
            "quality_by_tier": {"fast": 0.7, "standard": 0.96, "strong": 1.0},
        },
        retention=0.95,
        costs=None,
        schema_version=1,
    )
    assert set(row) == {"schema_version", "features", "label"}
    assert row["label"] == "standard"
