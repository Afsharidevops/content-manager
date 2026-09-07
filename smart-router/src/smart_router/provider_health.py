from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from sqlalchemy import select

from .control_db import ControlDB, ProviderHealthState
from .metrics import PROVIDER_CIRCUIT_STATE, PROVIDER_HEALTH_SCORE, PROVIDER_FALLBACKS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderHealthRegistry:
    """Shared route/model health and circuit-breaker state.

    State lives in the control database, so PostgreSQL HA replicas see the same
    circuit decisions. Redis remains available for hot shared counters via
    RedisCoordinator.
    """

    def __init__(self, db: ControlDB):
        self.db = db
        self.failure_threshold = max(1, int(os.getenv("SMART_ROUTER_CIRCUIT_FAILURE_THRESHOLD", "5")))
        self.cooldown = max(1, int(os.getenv("SMART_ROUTER_CIRCUIT_COOLDOWN_SECONDS", "60")))
        self.degraded_error_rate = min(1.0, max(0.0, float(os.getenv("SMART_ROUTER_PROVIDER_DEGRADED_ERROR_RATE", "0.20"))))
        self.qualifying_statuses = {408, 425, 429, 500, 502, 503, 504}

    def available(self, model: str) -> bool:
        now = time.time()
        with self.db.session() as session:
            row = session.get(ProviderHealthState, model)
            if row is None:
                return True
            if row.state == "CIRCUIT_OPEN":
                if float(row.circuit_open_until or 0) > now:
                    self._metrics(row)
                    return False
                row.state = "HALF_OPEN"
                session.commit()
                self._metrics(row)
                return True
            return True

    def record(self, model: str, status_code: int, latency_ms: float) -> None:
        qualifying_failure = int(status_code) in self.qualifying_statuses or int(status_code) >= 500
        success = 200 <= int(status_code) < 400
        now = time.time()
        with self.db.session() as session:
            row = session.get(ProviderHealthState, model)
            if row is None:
                row = ProviderHealthState(model=model)
                session.add(row)
            row.total_requests = int(row.total_requests or 0) + 1
            row.latency_ema_ms = float(latency_ms) if float(row.latency_ema_ms or 0) <= 0 else round(float(row.latency_ema_ms or 0) * 0.8 + float(latency_ms) * 0.2, 3)
            if success:
                row.successes = int(row.successes or 0) + 1
                row.consecutive_failures = 0
                row.last_success_at = _now_iso()
                if row.state in {"CIRCUIT_OPEN", "HALF_OPEN", "UNHEALTHY", "DEGRADED"}:
                    row.state = "HEALTHY"
                    row.circuit_open_until = 0.0
                    row.last_recovery_at = _now_iso()
            else:
                row.failures = int(row.failures or 0) + 1
                row.last_failure_at = _now_iso()
                if qualifying_failure:
                    row.consecutive_failures = int(row.consecutive_failures or 0) + 1
                if row.consecutive_failures >= self.failure_threshold:
                    row.state = "CIRCUIT_OPEN"
                    row.circuit_open_until = now + self.cooldown
                else:
                    error_rate = int(row.failures or 0) / max(1, int(row.total_requests or 0))
                    row.state = "DEGRADED" if error_rate >= self.degraded_error_rate else row.state
            session.commit()
            self._metrics(row)

    def fallback(self, model: str) -> None:
        with self.db.session() as session:
            row = session.get(ProviderHealthState, model)
            if row is None:
                row = ProviderHealthState(model=model)
                session.add(row)
            row.fallback_count = int(row.fallback_count or 0) + 1
            session.commit()
            PROVIDER_FALLBACKS.labels(model=model).inc()

    def snapshot(self) -> list[dict]:
        now = time.time()
        with self.db.session() as session:
            rows = list(session.scalars(select(ProviderHealthState).order_by(ProviderHealthState.model)))
        result = []
        for row in rows:
            total = max(1, row.total_requests)
            success_rate = row.successes / total
            score = max(0.0, min(100.0, success_rate * 100.0 - min(25.0, row.latency_ema_ms / 1000.0)))
            result.append({
                "model": row.model,
                "state": row.state,
                "health_score": round(score, 2),
                "success_rate": round(success_rate * 100.0, 2),
                "requests": row.total_requests,
                "failures": row.failures,
                "consecutive_failures": row.consecutive_failures,
                "latency_ema_ms": round(row.latency_ema_ms, 2),
                "circuit_open_for_seconds": max(0, int((row.circuit_open_until or 0) - now)),
                "last_failure": row.last_failure_at,
                "last_recovery": row.last_recovery_at,
                "fallback_count": row.fallback_count,
            })
        return result

    def _metrics(self, row: ProviderHealthState) -> None:
        total = max(1, row.total_requests)
        score = max(0.0, min(100.0, (row.successes / total) * 100.0 - min(25.0, row.latency_ema_ms / 1000.0)))
        PROVIDER_HEALTH_SCORE.labels(model=row.model).set(score)
        PROVIDER_CIRCUIT_STATE.labels(model=row.model).set(1 if row.state == "CIRCUIT_OPEN" else (0.5 if row.state == "HALF_OPEN" else 0))
