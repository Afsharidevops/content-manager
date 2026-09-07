from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_router.eval.benchmark import (
    BenchmarkError,
    baseline_selections,
    compute_metrics,
    evaluate_gates,
    main,
    pareto_frontier,
    parse_record,
    probability_selection,
)


def row(**overrides):
    payload = {
        "expected_tier": "standard",
        "selected_tier": "standard",
        "minimum_tier": "fast",
        "probabilities": {"fast": 0.1, "standard": 0.8, "strong": 0.1},
        "quality_by_tier": {"fast": 0.4, "standard": 0.85, "strong": 0.9},
        "confidence": 0.8,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    payload.update(overrides)
    return parse_record(payload)


def test_capability_minimum_gate_applies_to_baseline_and_metrics():
    rows = [row(expected_tier="strong", selected_tier="fast", minimum_tier="strong")]
    selections = baseline_selections(rows, "fast")
    assert selections == ["strong"]
    metrics = compute_metrics(rows, ["fast"], name="router", costs={"fast": 1, "standard": 3, "strong": 10})
    assert metrics.safe_routing_rate == 1.0
    assert metrics.capability_upgrade_rate == 1.0


def test_false_fast_and_cost_metrics():
    rows = [
        row(expected_tier="standard", selected_tier="fast"),
        row(expected_tier="fast", selected_tier="fast", quality_by_tier={"fast": 0.9, "standard": 0.9, "strong": 0.9}),
    ]
    metrics = compute_metrics(rows, [r.selected_tier for r in rows], name="router", costs={"fast": 1, "standard": 3, "strong": 10})
    assert metrics.false_fast_rate == 0.5
    assert metrics.cost_ratio_vs_strong == pytest.approx(0.1)
    assert metrics.quality_kind == "measured_response_quality"


def test_proxy_quality_is_explicit_when_scores_missing():
    rows = [row(quality_by_tier=None, expected_tier="standard", selected_tier="standard")]
    metrics = compute_metrics(rows, ["standard"], name="router", costs={"fast": 1, "standard": 3, "strong": 10})
    assert metrics.quality_kind == "safe_routing_proxy"
    assert metrics.quality_retention_vs_strong == 1.0


def test_probability_threshold_selection_and_minimum_gate():
    sample = row(
        minimum_tier="standard",
        probabilities={"fast": 0.85, "standard": 0.1, "strong": 0.05},
    )
    assert probability_selection(sample, standard_threshold=0.5, strong_threshold=0.5) == "standard"


def test_pareto_frontier_discards_dominated_points():
    points = [
        {"cost_ratio_vs_strong": 0.2, "quality_retention_vs_strong": 0.8},
        {"cost_ratio_vs_strong": 0.3, "quality_retention_vs_strong": 0.75},
        {"cost_ratio_vs_strong": 0.4, "quality_retention_vs_strong": 0.9},
    ]
    frontier = pareto_frontier(points)
    assert [(p["cost_ratio_vs_strong"], p["quality_retention_vs_strong"]) for p in frontier] == [(0.2, 0.8), (0.4, 0.9)]


def test_release_gates_detect_failures():
    rows = [row(expected_tier="strong", selected_tier="fast", quality_by_tier={"fast": 0.2, "standard": 0.7, "strong": 1.0})]
    metrics = compute_metrics(rows, ["fast"], name="router", costs={"fast": 1, "standard": 3, "strong": 10})
    failures = evaluate_gates(
        metrics,
        row_count=1,
        min_rows=10,
        min_quality_retention=0.95,
        max_false_fast_rate=0.01,
        max_cost_ratio=0.9,
        require_measured_quality=True,
    )
    assert len(failures) == 3


def test_cli_writes_report_and_summary_without_plots(tmp_path: Path):
    source = tmp_path / "rows.jsonl"
    source.write_text(
        "\n".join(
            json.dumps(
                {
                    "expected_tier": tier,
                    "selected_tier": tier,
                    "probabilities": {
                        "fast": 0.8 if tier == "fast" else 0.05,
                        "standard": 0.8 if tier == "standard" else 0.1,
                        "strong": 0.8 if tier == "strong" else 0.05,
                    },
                    "quality_by_tier": {"fast": 0.5, "standard": 0.8, "strong": 1.0},
                }
            )
            for tier in ("fast", "standard", "strong")
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"
    rc = main([str(source), "--output-dir", str(output), "--no-plots", "--min-rows", "3"])
    assert rc == 0
    assert (output / "summary.json").exists()
    assert (output / "report.md").exists()
    assert (output / "frontier.csv").exists()


def test_invalid_tier_rejected():
    with pytest.raises(BenchmarkError):
        row(expected_tier="ultra")
