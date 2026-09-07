from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from smart_router.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, validate_feature_mapping

ALLOWED_TIERS = ("fast", "standard", "strong")


@dataclass(frozen=True)
class TrainingRow:
    features: list[float]
    label: str
    request: dict[str, Any] | None = None


def parse_training_row(
    payload: dict[str, Any], *, cost_weight: float = 0.10, latency_weight: float = 0.05,
    min_quality: float = 0.0, allow_extra_features: bool = False,
) -> TrainingRow:
    if payload.get("schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {FEATURE_SCHEMA_VERSION}"
        )
    features = validate_feature_mapping(
        payload.get("features"), allow_extra=allow_extra_features
    )
    label = payload.get("label")
    if label is None and "results" in payload:
        label = label_from_outcomes(
            payload["results"],
            cost_weight=cost_weight,
            latency_weight=latency_weight,
            min_quality=min_quality,
        )
    if label not in ALLOWED_TIERS:
        raise ValueError("label must be fast, standard, or strong")
    request = payload.get("request")
    if request is not None and not isinstance(request, dict):
        raise ValueError("request must be an object when present")
    return TrainingRow(features=features, label=str(label), request=request)


def label_from_outcomes(
    results: dict[str, Any], *, cost_weight: float, latency_weight: float,
    min_quality: float,
) -> str:
    if not isinstance(results, dict):
        raise ValueError("results must be an object")
    parsed: dict[str, tuple[float, float, float]] = {}
    for tier in ALLOWED_TIERS:
        item = results.get(tier)
        if not isinstance(item, dict):
            raise ValueError(f"results.{tier} is required")
        try:
            quality = float(item["quality"])
            cost = float(item["cost"])
            latency = float(item["latency_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid results.{tier}") from exc
        if quality < 0 or cost < 0 or latency < 0:
            raise ValueError("quality/cost/latency must be non-negative")
        parsed[tier] = (quality, cost, latency)

    max_cost = max(v[1] for v in parsed.values()) or 1.0
    max_latency = max(v[2] for v in parsed.values()) or 1.0
    candidates = {
        tier: values for tier, values in parsed.items() if values[0] >= min_quality
    }
    if not candidates:
        candidates = parsed
    utilities = {
        tier: quality
        - cost_weight * (cost / max_cost)
        - latency_weight * (latency / max_latency)
        for tier, (quality, cost, latency) in candidates.items()
    }
    # Stable tie break favors the cheaper/lower tier.
    return max(ALLOWED_TIERS, key=lambda tier: (utilities.get(tier, float("-inf")), -ALLOWED_TIERS.index(tier)))


def feature_metadata() -> dict[str, Any]:
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
    }


def load_training_rows(path: str | Path, *, cost_weight: float = 0.10, latency_weight: float = 0.05, min_quality: float = 0.0) -> list[TrainingRow]:
    rows: list[TrainingRow] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("row must be an object")
                rows.append(parse_training_row(payload, cost_weight=cost_weight, latency_weight=latency_weight, min_quality=min_quality))
            except Exception as exc:
                raise ValueError(f"invalid training row {line_number}: {exc}") from exc
    if not rows:
        raise ValueError("training/evaluation file is empty")
    return rows
