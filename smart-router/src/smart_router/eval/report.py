from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from smart_router.config import Settings
from smart_router.learned.metrics import classification_metrics
from smart_router.learned.model import load_learned_policy
from smart_router.learned.schema import load_training_rows
from smart_router.routing import analyze_request, apply_capability_gates, build_policy_runtime, decide, tier_satisfies_capabilities

TIERS = ("fast", "standard", "strong")


def _fixed_metrics(labels: list[str], fixed: str) -> dict[str, Any]:
    return classification_metrics(labels, [fixed] * len(labels))


def _policy_predictions(rows, settings: Settings, policy: str) -> list[str]:
    configured = replace(settings, policy=policy)
    runtime = build_policy_runtime(configured)
    predictions = []
    for row in rows:
        # Training rows contain safe features, not raw prompts. Deterministic heuristic
        # comparison is only meaningful when an optional request is present.
        if row.request is None:
            continue
        predictions.append(decide(row.request, configured, runtime=runtime).proposed_tier)
    return predictions



def _capability_metrics(rows, predicted: list[str], settings: Settings) -> dict[str, Any]:
    evaluated = 0
    violations = 0
    upgrades = 0
    for row, proposed in zip(rows, predicted):
        if row.request is None:
            continue
        evaluated += 1
        facts = analyze_request(row.request)
        try:
            final_tier, _ = apply_capability_gates(proposed, facts, settings, [])
        except ValueError:
            violations += 1
            continue
        if final_tier != proposed:
            upgrades += 1
        if not tier_satisfies_capabilities(final_tier, facts, settings):
            violations += 1
    return {
        "capability_rows_evaluated": evaluated,
        "capability_violations": violations if evaluated else None,
        "capability_upgrade_count": upgrades if evaluated else None,
    }


def report(path: Path, settings: Settings, learned_model: str | None = None, metadata: str | None = None) -> dict[str, Any]:
    rows = load_training_rows(path)
    labels = [row.label for row in rows]
    result: dict[str, Any] = {
        "rows": len(rows),
        "label_distribution": dict(Counter(labels)),
        "baselines": {tier: _fixed_metrics(labels, tier) for tier in TIERS},
    }
    if learned_model and metadata:
        learned = load_learned_policy(learned_model, metadata)
        predicted = [learned.predict(row.features).tier for row in rows]
        result["learned"] = classification_metrics(labels, predicted)
        result["learned"]["fallback_rate_due_to_low_confidence"] = sum(
            learned.predict(row.features).low_confidence_fallback for row in rows
        ) / len(rows)
        result["learned"].update(_capability_metrics(rows, predicted, settings))
    requests = [row for row in rows if row.request is not None]
    if requests:
        for policy in ("heuristic", "calibrated"):
            predicted = _policy_predictions(requests, settings, policy)
            result[policy] = classification_metrics([row.label for row in requests], predicted)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Smart Router policies with fixed-routing baselines.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--learned-model")
    parser.add_argument("--metadata")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.learned_model) != bool(args.metadata):
        parser.error("--learned-model and --metadata must be provided together")
    settings = Settings.from_env()
    result = report(args.input, settings, args.learned_model, args.metadata)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
