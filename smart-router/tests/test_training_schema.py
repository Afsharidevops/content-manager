from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_router.features import extract_safe_features
from smart_router.learned.schema import parse_training_row
from smart_router.learned.train import load_rows, train_model


def _feature_map():
    return extract_safe_features({"messages": [{"role": "user", "content": "hello"}]}).as_dict()


def test_training_rejects_unknown_label():
    with pytest.raises(ValueError, match="label must be"):
        parse_training_row({"schema_version": 1, "features": _feature_map(), "label": "turbo"})


def test_training_rejects_missing_feature_key():
    features = _feature_map()
    features.pop("input_tokens")
    with pytest.raises(ValueError, match="missing feature keys"):
        parse_training_row({"schema_version": 1, "features": features, "label": "fast"})


def test_training_rejects_unknown_feature_key():
    features = _feature_map()
    features["raw_prompt"] = 1
    with pytest.raises(ValueError, match="unknown feature keys"):
        parse_training_row({"schema_version": 1, "features": features, "label": "fast"})


def test_training_rejects_too_few_rows(tmp_path: Path):
    features = _feature_map()
    rows = []
    for label in ("fast", "standard", "strong"):
        for _ in range(2):
            rows.append({"schema_version": 1, "features": features, "label": label})
    path = tmp_path / "small.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(ValueError, match="at least 9"):
        load_rows(str(path), cost_weight=.1, latency_weight=.05, min_quality=0)


def test_training_metadata_contains_artifact_hash(tmp_path: Path):
    source = Path(__file__).parents[1] / "examples" / "learned-routing-sample.jsonl"
    model = tmp_path / "m.joblib"
    meta = tmp_path / "m.json"
    metadata = train_model(str(source), str(model), str(meta), random_seed=13)
    assert len(metadata["artifact_sha256"]) == 64
    from smart_router import __version__

    assert metadata["router_version"] == __version__
    assert json.loads(meta.read_text())["artifact_sha256"] == metadata["artifact_sha256"]
