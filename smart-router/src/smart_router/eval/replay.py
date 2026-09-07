from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from smart_router.config import Settings
from smart_router.routing import build_policy_runtime, decide


def iter_requests(path: Path) -> Iterable[dict[str, Any]]:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_no}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"line {line_no} must contain a JSON object")
        yield item.get("request", item)


def replay(path: Path, settings: Settings) -> list[dict[str, Any]]:
    runtime = build_policy_runtime(settings)
    rows: list[dict[str, Any]] = []
    for request in iter_requests(path):
        decision = decide(request, settings, runtime=runtime)
        rows.append(
            {
                "tier": decision.proposed_tier,
                "model": decision.proposed_model,
                "policy": decision.policy,
                "score": decision.score,
                "confidence": decision.confidence,
                "probabilities": decision.probabilities,
                "capability_upgrade": decision.capability_upgrade,
                "features": decision.safe_features.as_dict() if decision.safe_features else {},
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay OpenAI-compatible requests through Smart Router offline.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--policy", choices=["heuristic", "calibrated", "learned"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.policy:
        settings = replace(settings, policy=args.policy)
    rows = replay(args.input, settings)
    rendered = "\n".join(json.dumps(row, sort_keys=True) for row in rows) + ("\n" if rows else "")
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
