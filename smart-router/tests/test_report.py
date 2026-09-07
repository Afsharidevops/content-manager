from __future__ import annotations

import json
from pathlib import Path

from smart_router.eval.report import report
from smart_router.features import extract_safe_features
from smart_router.learned.train import train_model


def test_report_marks_capability_metrics_unavailable_without_requests(settings, tmp_path):
    sample = Path(__file__).parents[1] / "examples" / "learned-routing-sample.jsonl"
    model = tmp_path / "m.joblib"
    meta = tmp_path / "m.json"
    train_model(str(sample), str(model), str(meta), random_seed=5)
    result = report(sample, settings, str(model), str(meta))
    assert result["learned"]["capability_rows_evaluated"] == 0
    assert result["learned"]["capability_violations"] is None


def test_report_measures_capability_upgrades_from_request_rows(settings, tmp_path):
    requests = [
        ({"model": "auto", "messages": [{"role": "user", "content": "translate hi"}]}, "fast"),
        ({"model": "auto", "messages": [{"role": "user", "content": "use tool"}], "tools": [{"type": "function", "function": {"name": "read"}}]}, "standard"),
        ({"model": "auto", "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}]}, "strong"),
    ]
    rows = []
    # Three copies per tier keep stratified training valid.
    for request, label in requests:
        features = extract_safe_features(request, strongest_context=settings.strong.max_context)
        for _ in range(3):
            rows.append({"schema_version": 1, "features": features.as_dict(), "label": label, "request": request})
    dataset = tmp_path / "requests.jsonl"
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    model = tmp_path / "m.joblib"
    meta = tmp_path / "m.json"
    train_model(str(dataset), str(model), str(meta), random_seed=9)
    result = report(dataset, settings, str(model), str(meta))
    assert result["learned"]["capability_rows_evaluated"] == 9
    assert result["learned"]["capability_violations"] == 0
