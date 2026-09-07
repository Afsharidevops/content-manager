from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_router.eval.calibrate import calibrate
from smart_router.eval.replay import iter_requests, replay


def test_legacy_calibrate_keeps_three_tiers(tmp_path: Path):
    rows = [
        {"request": {"model": "auto", "messages": [{"role": "user", "content": "translate hello"}]}, "label": "fast"},
        {"request": {"model": "auto", "messages": [{"role": "user", "content": "use tools"}], "tools": [{"type": "function", "function": {"name": "read"}}]}, "label": "standard"},
        {"request": {"model": "auto", "messages": [{"role": "user", "content": "architecture migration root cause"}]}, "label": "strong"},
    ]
    path = tmp_path / "calibrate.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    result = calibrate(path)
    assert result["rows"] == 3
    assert result["fast_max_score"] < result["standard_max_score"]
    assert "weights" in result


def test_legacy_calibrate_rejects_missing_tier(tmp_path: Path):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"request": {"model": "auto", "messages": []}, "label": "fast"}) + "\n")
    with pytest.raises(ValueError, match="fast, standard, and strong"):
        calibrate(path)


def test_legacy_replay_accepts_wrapped_and_direct_requests(settings, tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text(
        json.dumps({"request": {"model": "auto", "messages": [{"role": "user", "content": "translate hi"}]}})
        + "\n"
        + json.dumps({"model": "auto", "messages": [{"role": "user", "content": "architecture migration"}]})
        + "\n"
    )
    raw = list(iter_requests(path))
    assert len(raw) == 2
    result = replay(path, settings)
    assert len(result) == 2
    assert all(row["tier"] in {"fast", "standard", "strong"} for row in result)
    assert all("features" in row for row in result)
