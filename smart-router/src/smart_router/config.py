from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .secrets_v52 import env_or_file


@dataclass(frozen=True)
class TierConfig:
    model: str
    max_output: int
    supports_tools: bool
    supports_vision: bool
    max_context: int


@dataclass(frozen=True)
class Settings:
    mode: str
    policy: str
    upstream_base_url: str
    upstream_health_url: str
    upstream_api_key: str | None
    client_api_key: str | None
    database_path: str
    observation_file: str | None
    hmac_secret: str
    policy_version: str
    calibration_file: str
    observe_model: str
    session_ttl_seconds: int
    max_session_age_seconds: int
    demotion_turns: int
    connect_timeout_seconds: float
    read_timeout_seconds: float
    max_request_bytes: int
    preferred_token_field: str
    learned_model_file: str
    learned_metadata_file: str
    learned_min_confidence: float
    learned_fallback: str
    learned_error_fallback: str
    fast: TierConfig
    standard: TierConfig
    strong: TierConfig
    context_token_safety_factor: float = 1.15
    allow_tier_overrides: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("SMART_ROUTER_MODE", "observe").lower()
        if mode not in {"observe", "route"}:
            raise ValueError("SMART_ROUTER_MODE must be observe or route")

        policy = os.getenv("SMART_ROUTER_POLICY", "heuristic").lower()
        if policy not in {"heuristic", "calibrated", "learned"}:
            raise ValueError("SMART_ROUTER_POLICY must be heuristic, calibrated, or learned")

        preferred = os.getenv("SMART_ROUTER_PREFERRED_TOKEN_FIELD", "max_tokens")
        if preferred not in {"max_tokens", "max_completion_tokens"}:
            raise ValueError("SMART_ROUTER_PREFERRED_TOKEN_FIELD is invalid")

        secret = env_or_file("SMART_ROUTER_HMAC_SECRET", "") or ""
        if (
            len(secret.strip()) < 32
            or secret.startswith("CHANGE_ME")
            or len(set(secret.strip())) < 8
        ):
            raise ValueError(
                "SMART_ROUTER_HMAC_SECRET must be a strong secret of at least 32 characters"
            )

        learned_min_confidence = _probability(
            "SMART_ROUTER_LEARNED_MIN_CONFIDENCE", 0.70
        )
        learned_fallback = os.getenv(
            "SMART_ROUTER_LEARNED_FALLBACK", "standard"
        ).lower()
        if learned_fallback not in {"fast", "standard", "strong"}:
            raise ValueError(
                "SMART_ROUTER_LEARNED_FALLBACK must be fast, standard, or strong"
            )
        learned_error_fallback = os.getenv(
            "SMART_ROUTER_LEARNED_ERROR_FALLBACK", "heuristic"
        ).lower()
        if learned_error_fallback not in {"heuristic", "calibrated"}:
            raise ValueError(
                "SMART_ROUTER_LEARNED_ERROR_FALLBACK must be heuristic or calibrated"
            )

        upstream_base_url = _required_env("SMART_ROUTER_UPSTREAM_BASE_URL").rstrip("/")
        upstream_health_url = _optional_text("SMART_ROUTER_UPSTREAM_HEALTH_URL") or (
            upstream_base_url.removesuffix("/v1") + "/health"
        )

        settings = cls(
            mode=mode,
            policy=policy,
            upstream_base_url=upstream_base_url,
            upstream_health_url=upstream_health_url,
            upstream_api_key=env_or_file("SMART_ROUTER_UPSTREAM_API_KEY"),
            client_api_key=env_or_file("SMART_ROUTER_CLIENT_API_KEY"),
            database_path=os.getenv(
                "SMART_ROUTER_DATABASE_PATH", "/data/router.sqlite3"
            ),
            observation_file=_optional_text("SMART_ROUTER_OBSERVATION_FILE"),
            hmac_secret=secret,
            policy_version=_required_text("SMART_ROUTER_POLICY_VERSION", "4"),
            calibration_file=os.getenv(
                "SMART_ROUTER_CALIBRATION_FILE", "/policy/calibrated.json"
            ),
            observe_model=_required_text("SMART_ROUTER_OBSERVE_MODEL", "ai"),
            session_ttl_seconds=_positive_int(
                "SMART_ROUTER_SESSION_TTL_SECONDS", 2700
            ),
            max_session_age_seconds=_positive_int(
                "SMART_ROUTER_MAX_SESSION_AGE_SECONDS", 43200
            ),
            demotion_turns=_positive_int("SMART_ROUTER_DEMOTION_TURNS", 5),
            connect_timeout_seconds=_positive_float(
                "SMART_ROUTER_CONNECT_TIMEOUT_SECONDS", 10
            ),
            read_timeout_seconds=_positive_float(
                "SMART_ROUTER_READ_TIMEOUT_SECONDS", 600
            ),
            max_request_bytes=_positive_int(
                "SMART_ROUTER_MAX_REQUEST_BYTES", 10485760
            ),
            preferred_token_field=preferred,
            context_token_safety_factor=_positive_float(
                "SMART_ROUTER_CONTEXT_TOKEN_SAFETY_FACTOR", 1.15
            ),
            allow_tier_overrides=_bool_env(
                "SMART_ROUTER_ALLOW_TIER_OVERRIDES", False
            ),
            learned_model_file=os.getenv(
                "SMART_ROUTER_LEARNED_MODEL_FILE", "/policy/learned-v4.joblib"
            ),
            learned_metadata_file=os.getenv(
                "SMART_ROUTER_LEARNED_METADATA_FILE", "/policy/learned-v4.json"
            ),
            learned_min_confidence=learned_min_confidence,
            learned_fallback=learned_fallback,
            learned_error_fallback=learned_error_fallback,
            fast=_tier("FAST", "combo-fast", 1024, False, False, 32000),
            standard=_tier(
                "STANDARD", "combo-standard", 4096, True, False, 128000
            ),
            strong=_tier("STRONG", "combo-strong", 6144, True, True, 200000),
        )
        if settings.context_token_safety_factor < 1.0:
            raise ValueError(
                "SMART_ROUTER_CONTEXT_TOKEN_SAFETY_FACTOR must be >= 1.0"
            )
        if not any(
            tier.supports_tools
            for tier in (settings.fast, settings.standard, settings.strong)
        ):
            raise ValueError("at least one tier must support tools")
        if not any(
            tier.supports_vision
            for tier in (settings.fast, settings.standard, settings.strong)
        ):
            raise ValueError("at least one tier must support vision")
        _validate_tier_order(settings)
        return settings

    def tier(self, name: str) -> TierConfig:
        return {"fast": self.fast, "standard": self.standard, "strong": self.strong}[
            name
        ]



def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def _validate_tier_order(settings: Settings) -> None:
    tiers = (settings.fast, settings.standard, settings.strong)
    names = ("fast", "standard", "strong")
    for capability in ("supports_tools", "supports_vision"):
        values = [bool(getattr(tier, capability)) for tier in tiers]
        if values != sorted(values):
            raise ValueError(
                f"{capability} must be monotonic across fast -> standard -> strong"
            )
    contexts = [tier.max_context for tier in tiers]
    if contexts != sorted(contexts):
        raise ValueError(
            "SMART_ROUTER_*_MAX_CONTEXT must be non-decreasing across "
            + " -> ".join(names)
        )


def _tier(
    prefix: str,
    default_model: str,
    default_output: int,
    default_tools: bool,
    default_vision: bool,
    default_context: int,
) -> TierConfig:
    return TierConfig(
        model=_required_text(f"SMART_ROUTER_{prefix}_MODEL", default_model),
        max_output=_positive_int(
            f"SMART_ROUTER_{prefix}_MAX_TOKENS", default_output
        ),
        supports_tools=_bool_env(
            f"SMART_ROUTER_{prefix}_SUPPORTS_TOOLS", default_tools
        ),
        supports_vision=_bool_env(
            f"SMART_ROUTER_{prefix}_SUPPORTS_VISION", default_vision
        ),
        max_context=_positive_int(
            f"SMART_ROUTER_{prefix}_MAX_CONTEXT", default_context
        ),
    )


def _required_text(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _optional_text(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return value


def _probability(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0")
    return value


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")
