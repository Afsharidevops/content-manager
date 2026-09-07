from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

TIERS = ("fast", "standard", "strong")
TIER_RANK = {tier: idx for idx, tier in enumerate(TIERS)}
DEFAULT_COSTS = {"fast": 1.0, "standard": 3.0, "strong": 10.0}


class BenchmarkError(ValueError):
    """Raised when benchmark input is invalid or insufficient."""


@dataclass(frozen=True)
class BenchmarkRow:
    expected_tier: str
    selected_tier: str
    minimum_tier: str
    probabilities: dict[str, float] | None
    quality_by_tier: dict[str, float] | None
    confidence: float | None
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Metrics:
    name: str
    rows: int
    exact_agreement: float
    safe_routing_rate: float
    false_fast_rate: float
    under_route_rate: float
    strong_overroute_rate: float
    capability_upgrade_rate: float
    cost: float
    cost_ratio_vs_strong: float
    cost_savings_vs_strong: float
    quality: float
    quality_retention_vs_strong: float
    quality_kind: str
    tier_distribution: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rows": self.rows,
            "exact_agreement": self.exact_agreement,
            "safe_routing_rate": self.safe_routing_rate,
            "false_fast_rate": self.false_fast_rate,
            "under_route_rate": self.under_route_rate,
            "strong_overroute_rate": self.strong_overroute_rate,
            "capability_upgrade_rate": self.capability_upgrade_rate,
            "cost": self.cost,
            "cost_ratio_vs_strong": self.cost_ratio_vs_strong,
            "cost_savings_vs_strong": self.cost_savings_vs_strong,
            "quality": self.quality,
            "quality_retention_vs_strong": self.quality_retention_vs_strong,
            "quality_kind": self.quality_kind,
            "tier_distribution": self.tier_distribution,
        }


def _tier(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if text not in TIER_RANK:
        raise BenchmarkError(f"{field} must be one of {', '.join(TIERS)}; got {value!r}")
    return text


def _number(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(f"{field} must be numeric; got {value!r}") from exc
    if not math.isfinite(result):
        raise BenchmarkError(f"{field} must be finite; got {value!r}")
    return result


def _nonnegative_int(value: Any, *, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(f"{field} must be an integer; got {value!r}") from exc
    if result < 0:
        raise BenchmarkError(f"{field} must be >= 0; got {result}")
    return result


def _probabilities(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BenchmarkError("probabilities must be an object mapping tier to probability")
    probs = {tier: _number(value.get(tier, 0.0), field=f"probabilities.{tier}") for tier in TIERS}
    if any(prob < 0.0 or prob > 1.0 for prob in probs.values()):
        raise BenchmarkError("probabilities must be between 0 and 1")
    total = sum(probs.values())
    if total <= 0:
        raise BenchmarkError("probabilities must sum to a positive value")
    return {tier: prob / total for tier, prob in probs.items()}


def _quality_map(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BenchmarkError("quality_by_tier must be an object mapping tier to score")
    result: dict[str, float] = {}
    for tier in TIERS:
        if tier not in value:
            raise BenchmarkError(f"quality_by_tier is missing {tier!r}")
        result[tier] = _number(value[tier], field=f"quality_by_tier.{tier}")
    return result


def _usage(record: Mapping[str, Any]) -> tuple[int, int]:
    usage = record.get("usage")
    if isinstance(usage, Mapping):
        input_value = usage.get("input_tokens", usage.get("prompt_tokens", 0))
        output_value = usage.get("output_tokens", usage.get("completion_tokens", 0))
    else:
        input_value = record.get("input_tokens", record.get("prompt_tokens", 0))
        output_value = record.get("output_tokens", record.get("completion_tokens", 0))
    return (
        _nonnegative_int(input_value or 0, field="input_tokens"),
        _nonnegative_int(output_value or 0, field="output_tokens"),
    )


def parse_record(record: Mapping[str, Any]) -> BenchmarkRow:
    expected = record.get("expected_tier", record.get("label", record.get("target_tier")))
    selected = record.get("selected_tier", record.get("final_tier", record.get("predicted_tier", record.get("proposed_tier"))))
    if expected is None:
        raise BenchmarkError("record is missing expected_tier/label/target_tier")
    if selected is None:
        raise BenchmarkError("record is missing selected_tier/final_tier/predicted_tier/proposed_tier")

    expected_tier = _tier(expected, field="expected_tier")
    selected_tier = _tier(selected, field="selected_tier")
    minimum_tier = _tier(record.get("minimum_tier", "fast"), field="minimum_tier")
    probabilities = _probabilities(record.get("probabilities"))
    quality = _quality_map(record.get("quality_by_tier", record.get("tier_scores")))
    confidence_value = record.get("confidence")
    confidence = None if confidence_value is None else _number(confidence_value, field="confidence")
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise BenchmarkError("confidence must be between 0 and 1")
    input_tokens, output_tokens = _usage(record)
    return BenchmarkRow(
        expected_tier=expected_tier,
        selected_tier=selected_tier,
        minimum_tier=minimum_tier,
        probabilities=probabilities,
        quality_by_tier=quality,
        confidence=confidence,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def load_jsonl(path: Path) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as exc:
                raise BenchmarkError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(raw, Mapping):
                raise BenchmarkError(f"{path}:{line_number}: each JSONL row must be an object")
            try:
                rows.append(parse_record(raw))
            except BenchmarkError as exc:
                raise BenchmarkError(f"{path}:{line_number}: {exc}") from exc
    if not rows:
        raise BenchmarkError(f"{path}: no benchmark rows found")
    return rows


def load_costs(path: Path | None) -> tuple[dict[str, Any], str]:
    if path is None:
        return dict(DEFAULT_COSTS), "normalized_tier_weight"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise BenchmarkError("cost config must be a JSON object")
    kind = str(raw.get("kind", "normalized_tier_weight"))
    source = raw.get("costs", raw)
    if not isinstance(source, Mapping):
        raise BenchmarkError("cost config 'costs' must be an object")

    costs: dict[str, Any] = {}
    for tier in TIERS:
        value = source.get(tier)
        if isinstance(value, Mapping):
            input_rate = _number(
                value.get("input_per_million"), field=f"costs.{tier}.input_per_million"
            )
            output_rate = _number(
                value.get("output_per_million"), field=f"costs.{tier}.output_per_million"
            )
            if input_rate < 0 or output_rate < 0 or input_rate + output_rate <= 0:
                raise BenchmarkError(f"costs.{tier} token rates must be non-negative and not both zero")
            costs[tier] = {
                "input_per_million": input_rate,
                "output_per_million": output_rate,
            }
        else:
            scalar = _number(value, field=f"costs.{tier}")
            if scalar <= 0:
                raise BenchmarkError(f"costs.{tier} must be > 0")
            costs[tier] = scalar
    return costs, kind


def _gated(tier: str, minimum_tier: str) -> str:
    return TIERS[max(TIER_RANK[tier], TIER_RANK[minimum_tier])]


def _row_cost(row: BenchmarkRow, selected: str, costs: Mapping[str, Any], *, token_weighted: bool) -> float:
    spec = costs[selected]
    if isinstance(spec, Mapping):
        if row.input_tokens + row.output_tokens <= 0:
            raise BenchmarkError(
                "per-token price config requires input/output token usage on every benchmark row"
            )
        return (
            row.input_tokens * float(spec["input_per_million"])
            + row.output_tokens * float(spec["output_per_million"])
        ) / 1_000_000.0

    base = float(spec)
    if not token_weighted:
        return base
    token_count = max(row.input_tokens + row.output_tokens, 1)
    return base * token_count / 1000.0


def _measured_quality_available(rows: Sequence[BenchmarkRow]) -> bool:
    return bool(rows) and all(row.quality_by_tier is not None for row in rows)


def compute_metrics(
    rows: Sequence[BenchmarkRow],
    selections: Sequence[str],
    *,
    name: str,
    costs: Mapping[str, Any],
    token_weighted_cost: bool = False,
) -> Metrics:
    if len(rows) != len(selections):
        raise BenchmarkError("rows and selections must have equal length")
    if not rows:
        raise BenchmarkError("cannot compute metrics for zero rows")

    chosen = [_gated(_tier(tier, field="selection"), row.minimum_tier) for row, tier in zip(rows, selections)]
    n = len(rows)
    exact = sum(tier == row.expected_tier for row, tier in zip(rows, chosen)) / n
    safe = sum(TIER_RANK[tier] >= TIER_RANK[row.expected_tier] for row, tier in zip(rows, chosen)) / n
    false_fast = sum(tier == "fast" and TIER_RANK[row.expected_tier] > TIER_RANK["fast"] for row, tier in zip(rows, chosen)) / n
    under_route = sum(TIER_RANK[tier] < TIER_RANK[row.expected_tier] for row, tier in zip(rows, chosen)) / n
    strong_overroute = sum(tier == "strong" and row.expected_tier != "strong" for row, tier in zip(rows, chosen)) / n
    capability_upgrades = sum(TIER_RANK[tier] > TIER_RANK[raw] for row, tier, raw in zip(rows, chosen, selections)) / n

    cost = sum(_row_cost(row, tier, costs, token_weighted=token_weighted_cost) for row, tier in zip(rows, chosen))
    strong_cost = sum(_row_cost(row, "strong", costs, token_weighted=token_weighted_cost) for row in rows)
    if strong_cost <= 0:
        raise BenchmarkError("strong-only baseline cost must be > 0")
    cost_ratio = cost / strong_cost

    measured = _measured_quality_available(rows)
    if measured:
        selected_quality = mean(float(row.quality_by_tier[tier]) for row, tier in zip(rows, chosen) if row.quality_by_tier is not None)
        strong_quality = mean(float(row.quality_by_tier["strong"]) for row in rows if row.quality_by_tier is not None)
        retention = selected_quality / strong_quality if strong_quality else 1.0
        quality_kind = "measured_response_quality"
        quality = selected_quality
    else:
        quality = safe
        retention = safe
        quality_kind = "safe_routing_proxy"

    distribution_counter = Counter(chosen)
    distribution = {tier: distribution_counter[tier] / n for tier in TIERS}

    return Metrics(
        name=name,
        rows=n,
        exact_agreement=exact,
        safe_routing_rate=safe,
        false_fast_rate=false_fast,
        under_route_rate=under_route,
        strong_overroute_rate=strong_overroute,
        capability_upgrade_rate=capability_upgrades,
        cost=cost,
        cost_ratio_vs_strong=cost_ratio,
        cost_savings_vs_strong=1.0 - cost_ratio,
        quality=quality,
        quality_retention_vs_strong=retention,
        quality_kind=quality_kind,
        tier_distribution=distribution,
    )


def baseline_selections(rows: Sequence[BenchmarkRow], tier: str) -> list[str]:
    tier = _tier(tier, field="baseline tier")
    return [_gated(tier, row.minimum_tier) for row in rows]


def probability_selection(row: BenchmarkRow, *, standard_threshold: float, strong_threshold: float) -> str:
    if row.probabilities is None:
        raise BenchmarkError("threshold sweep requires probabilities on every row")
    p_standard_or_strong = row.probabilities["standard"] + row.probabilities["strong"]
    if row.probabilities["strong"] >= strong_threshold:
        tier = "strong"
    elif p_standard_or_strong >= standard_threshold:
        tier = "standard"
    else:
        tier = "fast"
    return _gated(tier, row.minimum_tier)


def threshold_sweep(
    rows: Sequence[BenchmarkRow],
    *,
    costs: Mapping[str, Any],
    token_weighted_cost: bool,
    step: float,
) -> list[dict[str, Any]]:
    if not all(row.probabilities is not None for row in rows):
        return []
    if step <= 0 or step > 1:
        raise BenchmarkError("threshold step must be > 0 and <= 1")
    values: list[float] = []
    cursor = step
    while cursor < 1.0:
        values.append(round(cursor, 8))
        cursor += step
    values.append(1.0)

    points: list[dict[str, Any]] = []
    for standard_threshold in values:
        for strong_threshold in values:
            selections = [
                probability_selection(
                    row,
                    standard_threshold=standard_threshold,
                    strong_threshold=strong_threshold,
                )
                for row in rows
            ]
            metrics = compute_metrics(
                rows,
                selections,
                name=f"s={standard_threshold:.2f},g={strong_threshold:.2f}",
                costs=costs,
                token_weighted_cost=token_weighted_cost,
            )
            point = metrics.as_dict()
            point["standard_threshold"] = standard_threshold
            point["strong_threshold"] = strong_threshold
            points.append(point)
    return points


def pareto_frontier(points: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return non-dominated points: lower cost ratio and higher quality are better."""
    ordered = sorted(points, key=lambda point: (float(point["cost_ratio_vs_strong"]), -float(point["quality_retention_vs_strong"])))
    frontier: list[dict[str, Any]] = []
    best_quality = -math.inf
    for point in ordered:
        quality = float(point["quality_retention_vs_strong"])
        if quality > best_quality + 1e-12:
            frontier.append(dict(point))
            best_quality = quality
    return frontier


def confusion_matrix(rows: Sequence[BenchmarkRow], selections: Sequence[str]) -> list[list[int]]:
    matrix = [[0 for _ in TIERS] for _ in TIERS]
    for row, selected in zip(rows, selections):
        selected = _gated(_tier(selected, field="selection"), row.minimum_tier)
        matrix[TIER_RANK[row.expected_tier]][TIER_RANK[selected]] += 1
    return matrix


def confidence_bins(rows: Sequence[BenchmarkRow], selections: Sequence[str]) -> list[dict[str, Any]]:
    samples = [
        (row.confidence, TIER_RANK[_gated(selected, row.minimum_tier)] < TIER_RANK[row.expected_tier])
        for row, selected in zip(rows, selections)
        if row.confidence is not None
    ]
    if not samples:
        return []
    result: list[dict[str, Any]] = []
    for low in [0.0, 0.2, 0.4, 0.6, 0.8]:
        high = low + 0.2
        bucket = [(confidence, risky) for confidence, risky in samples if confidence is not None and low <= confidence < high + (1e-12 if high >= 1.0 else 0.0)]
        if not bucket:
            continue
        result.append({
            "low": low,
            "high": min(high, 1.0),
            "count": len(bucket),
            "under_route_rate": sum(1 for _, risky in bucket if risky) / len(bucket),
        })
    return result


def _fmt_pct(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_frontier(path: Path, points: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "standard_threshold",
        "strong_threshold",
        "quality_retention_vs_strong",
        "cost_ratio_vs_strong",
        "cost_savings_vs_strong",
        "false_fast_rate",
        "under_route_rate",
        "strong_overroute_rate",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for point in points:
            writer.writerow(point)


def _plots(
    output_dir: Path,
    *,
    rows: Sequence[BenchmarkRow],
    selected: Sequence[str],
    baselines: Sequence[Metrics],
    router: Metrics,
    sweep: Sequence[Mapping[str, Any]],
    frontier: Sequence[Mapping[str, Any]],
    synthetic: bool,
) -> list[str]:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise BenchmarkError("plots require the 'bench' extra: pip install -e './smart-router[bench]'") from exc

    suffix = " — SYNTHETIC EXAMPLE, NOT A PERFORMANCE CLAIM" if synthetic else ""
    produced: list[str] = []

    fig, ax = plt.subplots(figsize=(8, 5))
    if sweep:
        ax.scatter(
            [float(point["cost_ratio_vs_strong"]) for point in sweep],
            [float(point["quality_retention_vs_strong"]) for point in sweep],
            alpha=0.18,
            s=18,
            label="threshold sweep",
        )
    if frontier:
        ax.plot(
            [float(point["cost_ratio_vs_strong"]) for point in frontier],
            [float(point["quality_retention_vs_strong"]) for point in frontier],
            marker="o",
            linewidth=1.4,
            label="Pareto frontier",
        )
    for metrics in [*baselines, router]:
        ax.scatter(metrics.cost_ratio_vs_strong, metrics.quality_retention_vs_strong, s=65)
        ax.annotate(metrics.name, (metrics.cost_ratio_vs_strong, metrics.quality_retention_vs_strong), xytext=(5, 5), textcoords="offset points")
    ax.set_xlabel("Cost ratio vs strong-only (lower is better)")
    ax.set_ylabel("Quality retention vs strong-only (higher is better)")
    ax.set_title("Hermes Smart Router quality vs cost" + suffix)
    ax.grid(True, alpha=0.25)
    if sweep or frontier:
        ax.legend(loc="best")
    fig.tight_layout()
    path = output_dir / "quality_vs_cost.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    produced.append(path.name)

    fig, ax = plt.subplots(figsize=(8, 5))
    values = [router.tier_distribution[tier] * 100.0 for tier in TIERS]
    bars = ax.bar(TIERS, values)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, value, f"{value:.1f}%", ha="center", va="bottom")
    ax.set_ylabel("Requests (%)")
    ax.set_title("Router tier distribution" + suffix)
    ax.set_ylim(0, max(100.0, max(values, default=0.0) * 1.15))
    fig.tight_layout()
    path = output_dir / "tier_distribution.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    produced.append(path.name)

    matrix = confusion_matrix(rows, selected)
    fig, ax = plt.subplots(figsize=(6, 5))
    image = ax.imshow(matrix)
    ax.set_xticks(range(len(TIERS)), TIERS)
    ax.set_yticks(range(len(TIERS)), TIERS)
    ax.set_xlabel("Selected tier")
    ax.set_ylabel("Expected tier")
    ax.set_title("Routing confusion matrix" + suffix)
    for i, row_values in enumerate(matrix):
        for j, value in enumerate(row_values):
            ax.text(j, i, str(value), ha="center", va="center")
    fig.colorbar(image, ax=ax)
    fig.tight_layout()
    path = output_dir / "confusion_matrix.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    produced.append(path.name)

    bins = confidence_bins(rows, selected)
    if bins:
        fig, ax = plt.subplots(figsize=(8, 5))
        labels = [f"{item['low']:.1f}–{item['high']:.1f}" for item in bins]
        rates = [100.0 * float(item["under_route_rate"]) for item in bins]
        ax.plot(labels, rates, marker="o")
        ax.set_xlabel("Model confidence")
        ax.set_ylabel("Under-route rate (%)")
        ax.set_title("Confidence vs routing risk" + suffix)
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        path = output_dir / "confidence_risk.png"
        fig.savefig(path, dpi=160)
        plt.close(fig)
        produced.append(path.name)

    return produced


def write_report(
    output_dir: Path,
    *,
    input_path: Path,
    rows: Sequence[BenchmarkRow],
    router: Metrics,
    baselines: Sequence[Metrics],
    frontier: Sequence[Mapping[str, Any]],
    cost_kind: str,
    synthetic: bool,
    gates: Sequence[str],
    plots: Sequence[str],
) -> None:
    quality_note = (
        "Measured response-quality scores were supplied for every tier on every row."
        if router.quality_kind == "measured_response_quality"
        else "No per-tier response-quality scores were supplied for every row, so the quality axis is the safe-routing agreement proxy. Do not present it as model-answer quality."
    )
    lines = [
        "# Hermes Smart Router v0.4.0 benchmark report",
        "",
    ]
    if synthetic:
        lines += [
            "> **Synthetic example only. This report is not a performance claim for Hermes Smart Router.**",
            "",
        ]
    lines += [
        f"- Input: `{input_path}`",
        f"- Rows: **{len(rows)}**",
        f"- Cost model: **{cost_kind}**",
        f"- Quality mode: **{router.quality_kind}**",
        f"- Note: {quality_note}",
        "",
        "## Headline metrics",
        "",
        "| Strategy | Quality retention | Cost vs strong-only | Cost savings | Exact agreement | False-fast | Under-route | Strong over-route |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for metrics in [*baselines, router]:
        lines.append(
            f"| {metrics.name} | {_fmt_pct(metrics.quality_retention_vs_strong)} | {_fmt_pct(metrics.cost_ratio_vs_strong)} | "
            f"{_fmt_pct(metrics.cost_savings_vs_strong)} | {_fmt_pct(metrics.exact_agreement)} | "
            f"{_fmt_pct(metrics.false_fast_rate)} | {_fmt_pct(metrics.under_route_rate)} | {_fmt_pct(metrics.strong_overroute_rate)} |"
        )
    lines += [
        "",
        "## Router tier mix",
        "",
        *[f"- {tier}: **{_fmt_pct(router.tier_distribution[tier])}**" for tier in TIERS],
        "",
        "## Figures",
        "",
        *[f"![{Path(plot).stem.replace('_', ' ')}]({plot})" for plot in plots],
        "",
        "## Pareto sweep",
        "",
    ]
    if frontier:
        lines.append(f"Generated **{len(frontier)}** non-dominated threshold points. See `frontier.csv`.")
    else:
        lines.append("No threshold sweep was generated because not every row supplied class probabilities.")
    lines += ["", "## Release gates", ""]
    if gates:
        lines.extend(f"- ❌ {gate}" for gate in gates)
    else:
        lines.append("- ✅ All configured gates passed.")
    lines += [
        "",
        "## Publishing guidance",
        "",
        "For public claims, use a representative held-out workload, disclose the grading method and cost configuration, report confidence intervals where possible, and retain this report plus `summary.json` as release evidence.",
        "",
    ]
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def evaluate_gates(
    router: Metrics,
    *,
    row_count: int,
    min_rows: int,
    min_quality_retention: float | None,
    max_false_fast_rate: float | None,
    max_cost_ratio: float | None,
    require_measured_quality: bool,
) -> list[str]:
    failures: list[str] = []
    if row_count < min_rows:
        failures.append(f"row count {row_count} is below --min-rows {min_rows}")
    if require_measured_quality and router.quality_kind != "measured_response_quality":
        failures.append("--require-measured-quality was set but one or more rows lack quality_by_tier")
    if min_quality_retention is not None and router.quality_retention_vs_strong < min_quality_retention:
        failures.append(
            f"quality retention {router.quality_retention_vs_strong:.4f} is below {min_quality_retention:.4f}"
        )
    if max_false_fast_rate is not None and router.false_fast_rate > max_false_fast_rate:
        failures.append(f"false-fast rate {router.false_fast_rate:.4f} exceeds {max_false_fast_rate:.4f}")
    if max_cost_ratio is not None and router.cost_ratio_vs_strong > max_cost_ratio:
        failures.append(f"cost ratio {router.cost_ratio_vs_strong:.4f} exceeds {max_cost_ratio:.4f}")
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smart-router-benchmark",
        description="Generate RouteLLM-style cost/quality evidence for Hermes Smart Router.",
    )
    parser.add_argument("input", type=Path, help="JSONL benchmark dataset")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-output"))
    parser.add_argument("--cost-config", type=Path, help="JSON cost/weight configuration")
    parser.add_argument("--token-weighted-cost", action="store_true", help="weight configured tier costs by total tokens/1000")
    parser.add_argument("--threshold-step", type=float, default=0.05, help="probability sweep step (default: 0.05)")
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--min-quality-retention", type=float)
    parser.add_argument("--max-false-fast-rate", type=float)
    parser.add_argument("--max-cost-ratio", type=float)
    parser.add_argument("--require-measured-quality", action="store_true")
    parser.add_argument("--synthetic", action="store_true", help="watermark report/plots as synthetic, non-claim evidence")
    parser.add_argument("--no-plots", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.min_rows < 1:
            raise BenchmarkError("--min-rows must be >= 1")
        for name in ("min_quality_retention", "max_false_fast_rate", "max_cost_ratio"):
            value = getattr(args, name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise BenchmarkError(f"--{name.replace('_', '-')} must be between 0 and 1")

        rows = load_jsonl(args.input)
        costs, cost_kind = load_costs(args.cost_config)
        selected = [row.selected_tier for row in rows]
        router = compute_metrics(
            rows,
            selected,
            name="smart-router",
            costs=costs,
            token_weighted_cost=args.token_weighted_cost,
        )
        baselines = [
            compute_metrics(
                rows,
                baseline_selections(rows, tier),
                name=f"{tier}-only",
                costs=costs,
                token_weighted_cost=args.token_weighted_cost,
            )
            for tier in TIERS
        ]
        sweep = threshold_sweep(
            rows,
            costs=costs,
            token_weighted_cost=args.token_weighted_cost,
            step=args.threshold_step,
        )
        frontier = pareto_frontier(sweep)
        gates = evaluate_gates(
            router,
            row_count=len(rows),
            min_rows=args.min_rows,
            min_quality_retention=args.min_quality_retention,
            max_false_fast_rate=args.max_false_fast_rate,
            max_cost_ratio=args.max_cost_ratio,
            require_measured_quality=args.require_measured_quality,
        )

        args.output_dir.mkdir(parents=True, exist_ok=True)
        plots: list[str] = []
        if not args.no_plots:
            plots = _plots(
                args.output_dir,
                rows=rows,
                selected=selected,
                baselines=baselines,
                router=router,
                sweep=sweep,
                frontier=frontier,
                synthetic=args.synthetic,
            )
        summary = {
            "schema_version": 1,
            "smart_router_version": "0.4.0",
            "synthetic": bool(args.synthetic),
            "input": str(args.input),
            "rows": len(rows),
            "cost_kind": cost_kind,
            "costs": costs,
            "token_weighted_cost": bool(args.token_weighted_cost),
            "quality_kind": router.quality_kind,
            "router": router.as_dict(),
            "baselines": [metrics.as_dict() for metrics in baselines],
            "threshold_points": len(sweep),
            "pareto_points": len(frontier),
            "gates_passed": not gates,
            "gate_failures": gates,
        }
        _write_json(args.output_dir / "summary.json", summary)
        _write_frontier(args.output_dir / "frontier.csv", frontier)
        write_report(
            args.output_dir,
            input_path=args.input,
            rows=rows,
            router=router,
            baselines=baselines,
            frontier=frontier,
            cost_kind=cost_kind,
            synthetic=args.synthetic,
            gates=gates,
            plots=plots,
        )

        print(f"rows={len(rows)}")
        print(f"quality_kind={router.quality_kind}")
        print(f"quality_retention_vs_strong={router.quality_retention_vs_strong:.4f}")
        print(f"cost_ratio_vs_strong={router.cost_ratio_vs_strong:.4f}")
        print(f"cost_savings_vs_strong={router.cost_savings_vs_strong:.4f}")
        print(f"false_fast_rate={router.false_fast_rate:.4f}")
        print(f"output={args.output_dir}")
        if gates:
            for failure in gates:
                print(f"GATE FAILED: {failure}", file=sys.stderr)
            return 2
        return 0
    except (BenchmarkError, OSError, json.JSONDecodeError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
