from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib

from smart_router.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, SafeFeatures
from .schema import ALLOWED_TIERS


@dataclass(frozen=True)
class LearnedPrediction:
    tier: str
    raw_tier: str
    confidence: float
    probabilities: dict[str, float]
    low_confidence_fallback: bool


@dataclass(frozen=True)
class LearnedPolicy:
    estimator: Any
    metadata: dict[str, Any]
    min_confidence: float
    fallback_tier: str

    def predict(self, features: SafeFeatures | list[float] | tuple[float, ...]) -> LearnedPrediction:
        row = features.as_row() if hasattr(features, "as_row") else [float(value) for value in features]
        if len(row) != len(FEATURE_NAMES):
            raise ValueError("learned feature vector length is incompatible")
        matrix = [row]
        probabilities = self.estimator.predict_proba(matrix)
        if len(probabilities) != 1:
            raise ValueError("learned model returned invalid prediction shape")
        classes = [str(item) for item in self.estimator.classes_]
        values = [float(item) for item in probabilities[0]]
        if set(classes) != set(ALLOWED_TIERS) or len(values) != 3:
            raise ValueError("learned model classes must be fast/standard/strong")
        if any(not math.isfinite(v) or v < 0 or v > 1 for v in values):
            raise ValueError("learned model returned invalid probabilities")
        if abs(sum(values) - 1.0) > 1e-3:
            raise ValueError("learned probabilities must sum to 1")
        mapped = {tier: values[classes.index(tier)] for tier in ALLOWED_TIERS}
        raw_tier = max(ALLOWED_TIERS, key=lambda tier: mapped[tier])
        confidence = mapped[raw_tier]
        low = confidence < self.min_confidence
        return LearnedPrediction(
            tier=self.fallback_tier if low else raw_tier,
            raw_tier=raw_tier,
            confidence=confidence,
            probabilities=mapped,
            low_confidence_fallback=low,
        )


def load_learned_policy(
    model_file: str,
    metadata_file: str,
    *,
    min_confidence: float | None = None,
    fallback_tier: str | None = None,
) -> LearnedPolicy:
    metadata_path = Path(metadata_file)
    model_path = Path(model_file)
    if not metadata_path.is_file():
        raise FileNotFoundError(metadata_file)
    if not model_path.is_file():
        raise FileNotFoundError(model_file)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    _validate_metadata(metadata)
    expected_hash = metadata.get("artifact_sha256")
    if expected_hash:
        actual_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("learned model artifact hash mismatch")

    # joblib/pickle is executable. Only trusted local training artifacts may be loaded.
    estimator = joblib.load(model_path)
    if not hasattr(estimator, "predict_proba") or not hasattr(estimator, "classes_"):
        raise ValueError("learned estimator must implement predict_proba and classes_")
    classes = {str(item) for item in estimator.classes_}
    if classes != set(ALLOWED_TIERS):
        raise ValueError("learned estimator classes are incompatible")
    threshold = float(
        metadata.get("min_confidence", 0.70)
        if min_confidence is None
        else min_confidence
    )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")
    fallback = str(
        metadata.get("fallback_tier", "standard")
        if fallback_tier is None
        else fallback_tier
    )
    if fallback not in ALLOWED_TIERS:
        raise ValueError("fallback_tier is invalid")
    return LearnedPolicy(estimator, metadata, threshold, fallback)


def _validate_metadata(metadata: dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("learned metadata must be a JSON object")
    if metadata.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError(
            "learned model feature schema is incompatible with router schema"
        )
    names = metadata.get("feature_names")
    if names is not None and list(names) != list(FEATURE_NAMES):
        raise ValueError("learned model feature names are incompatible")
    classes = metadata.get("classes")
    if classes is not None and set(classes) != set(ALLOWED_TIERS):
        raise ValueError("learned metadata classes are incompatible")
