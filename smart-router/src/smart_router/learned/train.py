from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from smart_router import __version__
from smart_router.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from .metrics import classification_metrics
from .schema import ALLOWED_TIERS, parse_training_row


def load_rows(path: str, *, cost_weight: float, latency_weight: float, min_quality: float):
    x: list[list[float]] = []
    y: list[str] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                row = parse_training_row(
                    payload,
                    cost_weight=cost_weight,
                    latency_weight=latency_weight,
                    min_quality=min_quality,
                )
            except Exception as exc:
                raise ValueError(f"invalid training row {line_number}: {exc}") from exc
            x.append(row.features)
            y.append(row.label)
    if len(x) < 9:
        raise ValueError("at least 9 training rows are required")
    if set(y) != set(ALLOWED_TIERS):
        raise ValueError("training data must include fast, standard, and strong labels")
    counts = Counter(y)
    if any(counts[tier] < 2 for tier in ALLOWED_TIERS):
        raise ValueError("training data must include at least two rows for each tier")
    return x, y


def train_model(
    input_path: str,
    output_path: str,
    metadata_path: str,
    *,
    validation_fraction: float = 0.20,
    random_seed: int = 42,
    model_type: str = "hist-gradient-boosting",
    min_confidence: float = 0.70,
    fallback_tier: str = "standard",
    cost_weight: float = 0.10,
    latency_weight: float = 0.05,
    min_quality: float = 0.0,
) -> dict:
    if not 0.05 <= validation_fraction <= 0.5:
        raise ValueError("validation_fraction must be between 0.05 and 0.5")
    if not 0.0 <= min_confidence <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    if fallback_tier not in ALLOWED_TIERS:
        raise ValueError("fallback_tier is invalid")
    x, y = load_rows(
        input_path,
        cost_weight=cost_weight,
        latency_weight=latency_weight,
        min_quality=min_quality,
    )
    class_count = len(ALLOWED_TIERS)
    validation_rows = max(class_count, math.ceil(len(x) * validation_fraction))
    validation_rows = min(validation_rows, len(x) - class_count)
    x_train, x_valid, y_train, y_valid = train_test_split(
        x,
        y,
        test_size=validation_rows,
        random_state=random_seed,
        stratify=y,
    )
    if model_type == "hist-gradient-boosting":
        estimator = HistGradientBoostingClassifier(random_state=random_seed)
        model_name = "HistGradientBoostingClassifier"
    elif model_type == "logistic-regression":
        estimator = LogisticRegression(
            max_iter=1000, random_state=random_seed
        )
        model_name = "LogisticRegression"
    else:
        raise ValueError("model must be hist-gradient-boosting or logistic-regression")
    estimator.fit(x_train, y_train)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(estimator, output)
    artifact_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    predictions = estimator.predict(x_valid)
    metrics = classification_metrics(y_valid, predictions)
    metadata = {
        "router_version": __version__,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "model_type": model_name,
        "classes": [str(item) for item in estimator.classes_],
        "training_rows": len(x),
        "training_rows_fit": len(x_train),
        "validation_rows": len(x_valid),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "random_seed": random_seed,
        "min_confidence": min_confidence,
        "fallback_tier": fallback_tier,
        "artifact_sha256": artifact_hash,
        "metrics": metrics,
        "utility": {
            "cost_weight": cost_weight,
            "latency_weight": latency_weight,
            "min_quality": min_quality,
        },
        "security": "Only load artifacts produced by a trusted Smart Router training process.",
    }
    meta = Path(metadata_path)
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smart-router-train")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--validation-fraction", type=float, default=0.20)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument(
        "--model",
        choices=("hist-gradient-boosting", "logistic-regression"),
        default="hist-gradient-boosting",
    )
    parser.add_argument("--min-confidence", type=float, default=0.70)
    parser.add_argument("--fallback-tier", choices=ALLOWED_TIERS, default="standard")
    parser.add_argument("--cost-weight", type=float, default=0.10)
    parser.add_argument("--latency-weight", type=float, default=0.05)
    parser.add_argument("--min-quality", type=float, default=0.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    metadata = train_model(
        args.input,
        args.output,
        args.metadata,
        validation_fraction=args.validation_fraction,
        random_seed=args.random_seed,
        model_type=args.model,
        min_confidence=args.min_confidence,
        fallback_tier=args.fallback_tier,
        cost_weight=args.cost_weight,
        latency_weight=args.latency_weight,
        min_quality=args.min_quality,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
