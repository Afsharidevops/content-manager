from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

TIERS = ("fast", "standard", "strong")


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class TierPrice:
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None

    def cost(self, usage: Usage) -> float:
        cached = max(0, min(usage.input_tokens, usage.cached_input_tokens))
        uncached = max(0, usage.input_tokens - cached)
        cached_rate = (
            self.cached_input_per_million
            if self.cached_input_per_million is not None
            else self.input_per_million
        )
        return (
            uncached * self.input_per_million
            + cached * cached_rate
            + usage.output_tokens * self.output_per_million
        ) / 1_000_000.0


class PricingCatalog:
    """Optional USD/token pricing used for measured cost accounting.

    Expected JSON shape::

        {
          "currency": "USD",
          "tiers": {
            "fast": {"input_per_million": 0.1, "output_per_million": 0.4},
            "standard": {"input_per_million": 0.5, "output_per_million": 1.5},
            "strong": {"input_per_million": 2.0, "output_per_million": 8.0}
          }
        }

    ``costs`` is accepted as an alias for ``tiers`` so the same rate file can be
    reused by the v0.4 benchmark command.
    """

    def __init__(self, rates: Mapping[str, TierPrice] | None = None, source: str | None = None):
        self.rates = dict(rates or {})
        self.source = source

    @classmethod
    def load(cls, path: str | Path | None) -> "PricingCatalog":
        if not path:
            return cls()
        file_path = Path(path).expanduser()
        if not file_path.exists():
            return cls(source=str(file_path))
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("pricing file must contain a JSON object")
        if str(raw.get("currency", "USD")).upper() != "USD":
            raise ValueError("v0.5 dashboard currently supports USD pricing only")
        source = raw.get("tiers", raw.get("costs", raw))
        if not isinstance(source, Mapping):
            raise ValueError("pricing tiers/costs must be a JSON object")
        rates: dict[str, TierPrice] = {}
        for tier in TIERS:
            item = source.get(tier)
            if not isinstance(item, Mapping):
                continue
            inp = _optional_nonnegative(item.get("input_per_million"))
            out = _optional_nonnegative(item.get("output_per_million"))
            cached = _optional_nonnegative(item.get("cached_input_per_million"))
            if inp is None or out is None:
                continue
            rates[tier] = TierPrice(inp, out, cached)
        return cls(rates=rates, source=str(file_path))

    def complete(self) -> bool:
        return all(tier in self.rates for tier in TIERS)

    def price(self, tier: str) -> TierPrice | None:
        return self.rates.get(tier)


class CostLedger:
    """Persistent measured-usage ledger for the built-in dashboard.

    The ledger never invents token usage. If the upstream response does not
    include usage (common for some streaming paths), the request is stored with
    ``usage_complete=0`` and excluded from USD savings calculations. Dashboard
    coverage makes this explicit.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        pricing: PricingCatalog | None = None,
        enabled: bool = True,
        error: str | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.pricing = pricing or PricingCatalog()
        self.enabled = enabled
        self.error = error
        if self.enabled:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    @classmethod
    def from_env(cls, *, default_database_path: str | Path) -> "CostLedger":
        enabled = _bool_env("SMART_ROUTER_COST_LEDGER_ENABLED", True)
        default_path = Path(default_database_path)
        ledger_path = os.getenv("SMART_ROUTER_COST_DATABASE_PATH")
        if not ledger_path:
            ledger_path = str(default_path.with_name("cost-ledger.sqlite3"))
        pricing_path = os.getenv("SMART_ROUTER_PRICING_FILE")
        try:
            pricing = PricingCatalog.load(pricing_path)
            return cls(ledger_path, pricing=pricing, enabled=enabled)
        except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
            # Cost visibility must never take the routing API down. Expose the
            # startup error through the dashboard summary instead.
            return cls(
                ledger_path,
                pricing=PricingCatalog(source=pricing_path),
                enabled=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS smart_router_cost_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    tier TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cached_input_tokens INTEGER,
                    actual_cost_usd REAL,
                    strong_baseline_cost_usd REAL,
                    client_output_limit INTEGER,
                    effective_output_limit INTEGER,
                    budget_tokens_avoided INTEGER NOT NULL DEFAULT 0,
                    usage_complete INTEGER NOT NULL,
                    pricing_complete INTEGER NOT NULL,
                    streaming INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_smart_router_cost_events_ts "
                "ON smart_router_cost_events(ts)"
            )

    def record_response(
        self,
        response: Any,
        *,
        tier: str,
        model: str,
        client_output_limit: int | None,
        effective_output_limit: int | None,
        streaming: bool,
    ) -> None:
        if not self.enabled:
            return
        if int(getattr(response, "status_code", 500)) >= 400:
            return
        usage = None if streaming else usage_from_response(response)
        self.record(
            tier=tier,
            model=model,
            usage=usage,
            client_output_limit=client_output_limit,
            effective_output_limit=effective_output_limit,
            streaming=streaming,
        )

    def record(
        self,
        *,
        tier: str,
        model: str,
        usage: Usage | None,
        client_output_limit: int | None = None,
        effective_output_limit: int | None = None,
        streaming: bool = False,
        timestamp: float | None = None,
    ) -> None:
        if not self.enabled:
            return
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {', '.join(TIERS)}")

        actual_cost: float | None = None
        strong_cost: float | None = None
        pricing_complete = False
        if usage is not None:
            selected_price = self.pricing.price(tier)
            strong_price = self.pricing.price("strong")
            if selected_price is not None and strong_price is not None:
                actual_cost = selected_price.cost(usage)
                strong_cost = strong_price.cost(usage)
                pricing_complete = True

        avoided = 0
        if (
            isinstance(client_output_limit, int)
            and isinstance(effective_output_limit, int)
            and client_output_limit > effective_output_limit
        ):
            # This is a budget-cap delta, not a claim about tokens a different
            # model would actually have generated.
            avoided = client_output_limit - effective_output_limit

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO smart_router_cost_events (
                    ts, tier, model, input_tokens, output_tokens,
                    cached_input_tokens, actual_cost_usd,
                    strong_baseline_cost_usd, client_output_limit,
                    effective_output_limit, budget_tokens_avoided,
                    usage_complete, pricing_complete, streaming
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    float(timestamp if timestamp is not None else time.time()),
                    tier,
                    model,
                    usage.input_tokens if usage else None,
                    usage.output_tokens if usage else None,
                    usage.cached_input_tokens if usage else None,
                    actual_cost,
                    strong_cost,
                    client_output_limit,
                    effective_output_limit,
                    avoided,
                    int(usage is not None),
                    int(pricing_complete),
                    int(streaming),
                ),
            )

    def summary(self, *, hours: float = 24.0) -> dict[str, Any]:
        if not self.enabled:
            result = _empty_summary(enabled=False, pricing=self.pricing)
            result["error"] = self.error
            return result
        hours = max(0.01, min(float(hours), 24.0 * 3650.0))
        since = time.time() - hours * 3600.0
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS requests,
                    COALESCE(SUM(usage_complete), 0) AS usage_requests,
                    COALESCE(SUM(pricing_complete), 0) AS priced_requests,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                    COALESCE(SUM(actual_cost_usd), 0.0) AS actual_cost_usd,
                    COALESCE(SUM(strong_baseline_cost_usd), 0.0) AS strong_baseline_cost_usd,
                    COALESCE(SUM(budget_tokens_avoided), 0) AS budget_tokens_avoided,
                    COALESCE(SUM(streaming), 0) AS streaming_requests
                FROM smart_router_cost_events
                WHERE ts >= ?
                """,
                (since,),
            ).fetchone()
            tier_rows = connection.execute(
                """
                SELECT tier, COUNT(*) AS count
                FROM smart_router_cost_events
                WHERE ts >= ?
                GROUP BY tier
                """,
                (since,),
            ).fetchall()

        requests = int(row["requests"])
        usage_requests = int(row["usage_requests"])
        priced_requests = int(row["priced_requests"])
        actual = float(row["actual_cost_usd"])
        strong = float(row["strong_baseline_cost_usd"])
        savings = strong - actual if priced_requests else 0.0
        savings_pct = savings / strong if priced_requests and strong > 0 else None
        tiers = {tier: 0 for tier in TIERS}
        for item in tier_rows:
            if item["tier"] in tiers:
                tiers[item["tier"]] = int(item["count"])

        return {
            "enabled": True,
            "window_hours": hours,
            "requests": requests,
            "usage_requests": usage_requests,
            "usage_coverage": usage_requests / requests if requests else 0.0,
            "priced_requests": priced_requests,
            "cost_coverage": priced_requests / requests if requests else 0.0,
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "cached_input_tokens": int(row["cached_input_tokens"]),
            "total_tokens": int(row["input_tokens"]) + int(row["output_tokens"]),
            "actual_cost_usd": actual,
            "strong_same_token_cost_usd": strong,
            "savings_usd": savings,
            "savings_pct": savings_pct,
            "budget_tokens_avoided": int(row["budget_tokens_avoided"]),
            "streaming_requests": int(row["streaming_requests"]),
            "tier_counts": tiers,
            "pricing_configured": self.pricing.complete(),
            "pricing_source": self.pricing.source,
            "cost_baseline": "same-token strong-only",
            "token_reduction_kind": "output-budget-cap delta",
            "error": self.error,
        }

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT ts, tier, model, input_tokens, output_tokens,
                       actual_cost_usd, strong_baseline_cost_usd,
                       budget_tokens_avoided, usage_complete, pricing_complete,
                       streaming
                FROM smart_router_cost_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def usage_from_response(response: Any) -> Usage | None:
    try:
        body = getattr(response, "body")
        if isinstance(body, memoryview):
            body = body.tobytes()
        if isinstance(body, bytearray):
            body = bytes(body)
        if not isinstance(body, bytes):
            return None
        payload = json.loads(body)
        if not isinstance(payload, Mapping):
            return None
        raw_usage = payload.get("usage")
        if not isinstance(raw_usage, Mapping):
            return None
        input_value = raw_usage.get("prompt_tokens", raw_usage.get("input_tokens"))
        output_value = raw_usage.get("completion_tokens", raw_usage.get("output_tokens"))
        details = raw_usage.get("prompt_tokens_details", raw_usage.get("input_tokens_details", {}))
        cached_value = details.get("cached_tokens", 0) if isinstance(details, Mapping) else 0
        if not _token_number(input_value) or not _token_number(output_value):
            return None
        return Usage(int(input_value), int(output_value), int(cached_value or 0))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _token_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def _optional_nonnegative(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"pricing value must be numeric or null; got {value!r}") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"pricing value must be finite and >= 0; got {value!r}")
    return number


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _empty_summary(*, enabled: bool, pricing: PricingCatalog) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "window_hours": 24.0,
        "requests": 0,
        "usage_requests": 0,
        "usage_coverage": 0.0,
        "priced_requests": 0,
        "cost_coverage": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "total_tokens": 0,
        "actual_cost_usd": 0.0,
        "strong_same_token_cost_usd": 0.0,
        "savings_usd": 0.0,
        "savings_pct": None,
        "budget_tokens_avoided": 0,
        "streaming_requests": 0,
        "tier_counts": {tier: 0 for tier in TIERS},
        "pricing_configured": pricing.complete(),
        "pricing_source": pricing.source,
        "cost_baseline": "same-token strong-only",
        "token_reduction_kind": "output-budget-cap delta",
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read the Hermes Smart Router measured-cost ledger")
    parser.add_argument("--database", default=os.getenv("SMART_ROUTER_COST_DATABASE_PATH", "/data/cost-ledger.sqlite3"))
    parser.add_argument("--pricing", default=os.getenv("SMART_ROUTER_PRICING_FILE"))
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--recent", type=int, default=0)
    args = parser.parse_args()
    ledger = CostLedger(args.database, pricing=PricingCatalog.load(args.pricing))
    payload: dict[str, Any] = {"summary": ledger.summary(hours=args.hours)}
    if args.recent:
        payload["recent"] = ledger.recent(limit=args.recent)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
