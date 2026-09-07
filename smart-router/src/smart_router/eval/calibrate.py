from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from smart_router.routing import DEFAULT_CALIBRATION, _score_facts, analyze_request


def calibrate(path: Path) -> dict[str, Any]:
    """Derive conservative score cut-points from labeled request JSONL.

    Input rows are {"request": {...}, "label": "fast|standard|strong"}.
    We intentionally keep the v0.2 weight table stable and only learn thresholds.
    """
    by_label: dict[str, list[int]] = defaultdict(list)
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        label = row.get("label")
        request = row.get("request")
        if label not in {"fast", "standard", "strong"} or not isinstance(request, dict):
            raise ValueError(f"invalid calibration row on line {line_no}")
        score, _ = _score_facts(analyze_request(request), DEFAULT_CALIBRATION["weights"])
        by_label[label].append(score)
    if not all(by_label[tier] for tier in ("fast", "standard", "strong")):
        raise ValueError("calibration data must contain fast, standard, and strong labels")
    fast_max = max(by_label["fast"])
    standard_max = max(max(by_label["standard"]), fast_max + 1)
    result = dict(DEFAULT_CALIBRATION)
    result["fast_max_score"] = fast_max
    result["standard_max_score"] = standard_max
    result["rows"] = sum(len(v) for v in by_label.values())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate deterministic Smart Router score thresholds.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = calibrate(args.input)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
