from __future__ import annotations

import os
import time
from dataclasses import dataclass

try:  # Optional at source-test time; installed by the v0.5.6 package image.
    import redis  # type: ignore
except Exception:  # pragma: no cover - exercised when dependency is intentionally absent
    redis = None


@dataclass(frozen=True)
class RateResult:
    requests_minute: int
    tokens_minute: int
    requests_day: int


class RedisCoordinator:
    """Small shared-state adapter used for HA counters and coordination.

    SQLite/single-node mode does not require Redis. When SMART_ROUTER_REDIS_URL is
    set, failures are surfaced so HA deployments can fail closed rather than silently
    diverging across replicas.
    """

    def __init__(self, url: str | None = None):
        self.url = (url or os.getenv("SMART_ROUTER_REDIS_URL", "")).strip()
        self.prefix = os.getenv("SMART_ROUTER_REDIS_PREFIX", "hermes:v052").strip() or "hermes:v052"
        self.client = None
        if self.url and redis is not None:
            self.client = redis.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=float(os.getenv("SMART_ROUTER_REDIS_CONNECT_TIMEOUT", "2")),
                socket_timeout=float(os.getenv("SMART_ROUTER_REDIS_SOCKET_TIMEOUT", "2")),
                health_check_interval=30,
            )

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def ping(self) -> bool:
        if not self.enabled:
            return True
        if self.client is None:
            return False
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def rate_limit(self, key_base: str, estimated_tokens: int, rpm: int | None = None, tpm: int | None = None, daily_requests: int | None = None) -> RateResult:
        if not self.enabled:
            raise RuntimeError("Redis shared state is not enabled")
        if self.client is None:
            raise RuntimeError("redis dependency is unavailable")
        now = int(time.time())
        minute = now // 60
        day = now // 86400
        m_req = f"{self.prefix}:rate:{key_base}:m:{minute}:req"
        m_tok = f"{self.prefix}:rate:{key_base}:m:{minute}:tok"
        d_req = f"{self.prefix}:rate:{key_base}:d:{day}:req"
        try:
            if rpm is None or tpm is None or daily_requests is None:
                pipe = self.client.pipeline(transaction=True)
                pipe.incr(m_req, 1); pipe.expire(m_req, 180)
                pipe.incrby(m_tok, max(0, int(estimated_tokens))); pipe.expire(m_tok, 180)
                pipe.incr(d_req, 1); pipe.expire(d_req, 172800)
                values = pipe.execute()
                return RateResult(int(values[0]), int(values[2]), int(values[4]))

            # Guarded Lua update: denied requests do not consume additional quota.
            script = r"""
local req = tonumber(redis.call('GET', KEYS[1]) or '0')
local tok = tonumber(redis.call('GET', KEYS[2]) or '0')
local day = tonumber(redis.call('GET', KEYS[3]) or '0')
local addtok = tonumber(ARGV[1])
local rpm = tonumber(ARGV[2])
local tpm = tonumber(ARGV[3])
local daily = tonumber(ARGV[4])
if req + 1 > rpm then return {0, req, tok, day, 1} end
if tok + addtok > tpm then return {0, req, tok, day, 2} end
if day + 1 > daily then return {0, req, tok, day, 3} end
local nreq = redis.call('INCR', KEYS[1]); redis.call('EXPIRE', KEYS[1], 180)
local ntok = redis.call('INCRBY', KEYS[2], addtok); redis.call('EXPIRE', KEYS[2], 180)
local nday = redis.call('INCR', KEYS[3]); redis.call('EXPIRE', KEYS[3], 172800)
return {1, nreq, ntok, nday, 0}
"""
            values = self.client.eval(script, 3, m_req, m_tok, d_req, max(0, int(estimated_tokens)), max(1, int(rpm)), max(1, int(tpm)), max(1, int(daily_requests)))
            allowed, req, tok, dreq, scope = [int(x) for x in values]
            result = RateResult(req, tok, dreq)
            if allowed:
                return result
            scope_name = {1: "rpm", 2: "tpm", 3: "daily_requests"}.get(scope, "unknown")
            error = RuntimeError(f"rate_limit_denied:{scope_name}")
            error.rate_result = result  # type: ignore[attr-defined]
            error.rate_scope = scope_name  # type: ignore[attr-defined]
            raise error
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Redis rate-limit operation failed") from exc
