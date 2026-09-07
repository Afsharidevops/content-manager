from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import joblib
import numpy as np

from smart_router.features import FEATURE_NAMES, extract_safe_features
from smart_router.learned.model import LearnedPolicy, load_learned_policy
from smart_router.learned.train import train_model
from smart_router.routing import build_policy_runtime, decide


class FixedEstimator:
    classes_ = np.array(["fast", "standard", "strong"])
    def __init__(self, probabilities):
        self.probabilities = probabilities
    def predict_proba(self, matrix):
        return np.array([self.probabilities], dtype=float)


def _write_artifact(tmp_path: Path, probabilities=(0.2, 0.6, 0.2), schema=1):
    model = tmp_path / "model.joblib"
    meta = tmp_path / "model.json"
    joblib.dump(FixedEstimator(probabilities), model)
    meta.write_text(json.dumps({
        "feature_schema_version": schema,
        "feature_names": list(FEATURE_NAMES),
        "classes": ["fast", "standard", "strong"],
        "min_confidence": 0.7,
        "fallback_tier": "standard"
    }))
    return model, meta


def test_low_confidence_falls_back_to_standard(tmp_path):
    model, meta = _write_artifact(tmp_path, (0.40, 0.34, 0.26))
    policy = load_learned_policy(str(model), str(meta))
    pred = policy.predict(extract_safe_features({"messages": [{"role": "user", "content": "hello"}]}))
    assert pred.raw_tier == "fast"
    assert pred.tier == "standard"
    assert pred.low_confidence_fallback


def test_schema_mismatch_rejected(tmp_path):
    model, meta = _write_artifact(tmp_path, schema=999)
    try:
        load_learned_policy(str(model), str(meta))
    except ValueError as exc:
        assert "schema" in str(exc)
    else:
        raise AssertionError("schema mismatch must fail")


def test_missing_model_fail_opens_to_heuristic(learned_settings):
    runtime = build_policy_runtime(learned_settings)
    assert runtime.learned is None
    decision = decide({"model": "auto", "messages": [{"role": "user", "content": "translate hello"}]}, learned_settings, runtime=runtime)
    assert decision.policy_fallback == "heuristic"
    assert decision.proposed_tier == "fast"


def test_training_is_reproducible(tmp_path):
    sample = Path(__file__).parents[1] / "examples" / "learned-routing-sample.jsonl"
    a = tmp_path / "a.joblib"; am = tmp_path / "a.json"
    b = tmp_path / "b.joblib"; bm = tmp_path / "b.json"
    ma = train_model(str(sample), str(a), str(am), random_seed=7)
    mb = train_model(str(sample), str(b), str(bm), random_seed=7)
    assert ma["metrics"] == mb["metrics"]
    assert ma["feature_schema_version"] == 1
    assert ma["training_rows"] == 60


def test_logistic_regression_training_supported(tmp_path):
    sample = Path(__file__).parents[1] / "examples" / "learned-routing-sample.jsonl"
    model = tmp_path / "logistic.joblib"
    meta = tmp_path / "logistic.json"
    metadata = train_model(
        str(sample), str(model), str(meta), model_type="logistic-regression", random_seed=11
    )
    assert metadata["model_type"] == "LogisticRegression"
    assert model.is_file() and meta.is_file()


def test_nine_row_balanced_training_uses_three_validation_rows(tmp_path):
    source = Path(__file__).parents[1] / "examples" / "learned-routing-sample.jsonl"
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    selected = []
    for tier in ("fast", "standard", "strong"):
        selected.extend([row for row in rows if row["label"] == tier][:3])
    sample = tmp_path / "nine.jsonl"
    sample.write_text("\n".join(json.dumps(row) for row in selected) + "\n")
    metadata = train_model(
        str(sample), str(tmp_path / "m.joblib"), str(tmp_path / "m.json"), random_seed=3
    )
    assert metadata["training_rows"] == 9
    assert metadata["validation_rows"] == 3


def test_invalid_probability_inference_fails_open(learned_settings):
    broken = LearnedPolicy(
        FixedEstimator((0.9, 0.9, -0.8)),
        {"feature_schema_version": 1},
        0.7,
        "standard",
    )
    runtime = build_policy_runtime(learned_settings)
    runtime = type(runtime)(runtime.calibration, broken, None)
    decision = decide(
        {"model": "auto", "messages": [{"role": "user", "content": "translate hello"}]},
        learned_settings,
        runtime=runtime,
    )
    assert decision.policy_fallback == "heuristic"
    assert decision.proposed_tier == "fast"


def test_corrupt_model_artifact_is_rejected(tmp_path):
    model = tmp_path / "bad.joblib"
    meta = tmp_path / "bad.json"
    model.write_bytes(b"not-a-joblib")
    meta.write_text(json.dumps({
        "feature_schema_version": 1,
        "feature_names": list(FEATURE_NAMES),
        "classes": ["fast", "standard", "strong"],
        "min_confidence": 0.7,
        "fallback_tier": "standard",
    }))
    try:
        load_learned_policy(str(model), str(meta))
    except Exception:
        pass
    else:
        raise AssertionError("corrupt learned artifacts must be rejected")
