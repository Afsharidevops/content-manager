from __future__ import annotations

import json
from pathlib import Path

from starlette.responses import JSONResponse

from smart_router.costs import CostLedger, PricingCatalog, Usage, usage_from_response


def _pricing(tmp_path: Path) -> PricingCatalog:
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps(
            {
                "currency": "USD",
                "tiers": {
                    "fast": {"input_per_million": 1.0, "output_per_million": 2.0},
                    "standard": {"input_per_million": 2.0, "output_per_million": 4.0},
                    "strong": {"input_per_million": 10.0, "output_per_million": 20.0},
                },
            }
        )
    )
    return PricingCatalog.load(path)


def test_usage_parser_supports_openai_usage():
    response = JSONResponse(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "prompt_tokens_details": {"cached_tokens": 20},
            }
        }
    )
    assert usage_from_response(response) == Usage(100, 25, 20)


def test_cost_ledger_measures_same_token_strong_baseline(tmp_path: Path):
    ledger = CostLedger(tmp_path / "cost.sqlite3", pricing=_pricing(tmp_path))
    ledger.record(
        tier="fast",
        model="fast-model",
        usage=Usage(1_000_000, 100_000),
        client_output_limit=1000,
        effective_output_limit=600,
    )
    summary = ledger.summary(hours=24)
    assert summary["requests"] == 1
    assert summary["usage_coverage"] == 1.0
    assert summary["cost_coverage"] == 1.0
    assert summary["actual_cost_usd"] == 1.2
    assert summary["strong_same_token_cost_usd"] == 12.0
    assert summary["savings_pct"] == 0.9
    assert summary["budget_tokens_avoided"] == 400
    assert summary["tier_counts"]["fast"] == 1


def test_missing_stream_usage_reduces_coverage(tmp_path: Path):
    ledger = CostLedger(tmp_path / "cost.sqlite3", pricing=_pricing(tmp_path))
    ledger.record(tier="standard", model="m", usage=None, streaming=True)
    summary = ledger.summary(hours=24)
    assert summary["requests"] == 1
    assert summary["usage_coverage"] == 0.0
    assert summary["cost_coverage"] == 0.0
    assert summary["streaming_requests"] == 1
