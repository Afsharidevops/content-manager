from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

TIERS = ("fast", "standard", "strong")
RANK = {tier: index for index, tier in enumerate(TIERS)}


class PreferenceDataError(ValueError):
    pass


def choose_preferred_tier(
    quality_by_tier: Mapping[str, Any],
    *,
    minimum_tier: str = "fast",
    retention: float = 0.95,
    costs: Mapping[str, float] | None = None,
) -> str:
    """Choose the cheapest safe tier that meets a strong-quality retention target.

    This converts measured per-tier outcomes into a three-class label compatible
    with the existing ``smart-router-train`` input format. It is deliberately
    conservative: the capability-derived minimum tier is always respected.
    """
    minimum_tier = str(minimum_tier).lower()
    if minimum_tier not in RANK:
        raise PreferenceDataError(f"minimum_tier must be one of {', '.join(TIERS)}")
    if not 0 < retention <= 1.5:
        raise PreferenceDataError("retention must be > 0 and <= 1.5")
    quality: dict[str, float] = {}
    for tier in TIERS:
        if tier not in quality_by_tier:
            raise PreferenceDataError(f"quality_by_tier is missing {tier}")
        quality[tier] = float(quality_by_tier[tier])
    strong_quality = quality["strong"]
    threshold = strong_quality * retention
    cost_map = dict(costs or {"fast": 1.0, "standard": 3.0, "strong": 10.0})
    candidates = [tier for tier in TIERS if RANK[tier] >= RANK[minimum_tier] and quality[tier] >= threshold]
    if not candidates:
        return "strong"
    return min(candidates, key=lambda tier: (float(cost_map.get(tier, 1e30)), RANK[tier]))


def build_row(
    raw: Mapping[str, Any],
    *,
    retention: float,
    costs: Mapping[str, float] | None,
    schema_version: int,
) -> dict[str, Any]:
    features = raw.get("features")
    if not isinstance(features, Mapping) or not features:
        raise PreferenceDataError("row requires a non-empty privacy-safe features object")
    quality = raw.get("quality_by_tier", raw.get("tier_scores"))
    if not isinstance(quality, Mapping):
        raise PreferenceDataError("row requires quality_by_tier/tier_scores")
    minimum_tier = str(raw.get("minimum_tier", "fast"))
    label = choose_preferred_tier(
        quality,
        minimum_tier=minimum_tier,
        retention=retention,
        costs=costs,
    )
    return {
        "schema_version": int(raw.get("schema_version", schema_version)),
        "features": dict(features),
        "label": label,
    }


def _load_costs(path: Path | None) -> dict[str, float] | None:
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    source = raw.get("costs", raw) if isinstance(raw, Mapping) else None
    if not isinstance(source, Mapping):
        raise PreferenceDataError("cost config must be a JSON object")
    result: dict[str, float] = {}
    for tier in TIERS:
        value = source.get(tier)
        if isinstance(value, Mapping):
            # Preference labeling only needs an ordering proxy. For token-price
            # files use an equal input/output blend; benchmark.py remains the
            # authoritative token-weighted evaluator.
            inp = float(value.get("input_per_million", 0.0))
            out = float(value.get("output_per_million", 0.0))
            result[tier] = inp + out
        else:
            result[tier] = float(value)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build preference-derived three-tier training labels from measured per-tier quality outcomes."
    )
    parser.add_argument("input", type=Path, help="JSONL with features + quality_by_tier")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retention", type=float, default=0.95, help="minimum quality relative to strong")
    parser.add_argument("--cost-config", type=Path)
    parser.add_argument("--schema-version", type=int, default=1)
    args = parser.parse_args()
    costs = _load_costs(args.cost_config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.input.open("r", encoding="utf-8") as source, args.output.open("w", encoding="utf-8") as target:
        for line_number, line in enumerate(source, 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                raw = json.loads(text)
                if not isinstance(raw, Mapping):
                    raise PreferenceDataError("row must be a JSON object")
                row = build_row(raw, retention=args.retention, costs=costs, schema_version=args.schema_version)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise PreferenceDataError(f"invalid preference row on line {line_number}: {exc}") from exc
            target.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            written += 1
    if not written:
        raise PreferenceDataError("no preference rows were written")
    print(f"rows={written}")
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
