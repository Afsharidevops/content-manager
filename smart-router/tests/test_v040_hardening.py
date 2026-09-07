from __future__ import annotations

from dataclasses import replace

import pytest

from smart_router.config import Settings, TierConfig
from smart_router.main import _resolve_requested_tier
from smart_router.routing import RequestFacts, tier_satisfies_capabilities


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("SMART_ROUTER_UPSTREAM_BASE_URL", "http://upstream.test/v1")
    monkeypatch.setenv("SMART_ROUTER_HMAC_SECRET", "0123456789abcdef0123456789abcdef")
    return Settings.from_env()


def test_v040_defaults_disable_client_tier_override(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    assert settings.allow_tier_overrides is False
    with pytest.raises(PermissionError):
        _resolve_requested_tier("auto-strong", None, settings.allow_tier_overrides)
    with pytest.raises(PermissionError):
        _resolve_requested_tier("auto", "strong", settings.allow_tier_overrides)
    assert _resolve_requested_tier("auto", None, settings.allow_tier_overrides) is None


def test_v040_trusted_deployment_can_opt_in_to_override() -> None:
    assert _resolve_requested_tier("auto-fast", None, True) == "fast"
    assert _resolve_requested_tier("auto", "strong", True) == "strong"
    with pytest.raises(ValueError):
        _resolve_requested_tier("auto", "ultra", True)


def test_context_safety_factor_must_be_at_least_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_ROUTER_CONTEXT_TOKEN_SAFETY_FACTOR", "0.99")
    with pytest.raises(ValueError, match="must be >= 1.0"):
        _settings(monkeypatch)


def test_context_safety_factor_is_applied_to_hard_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    tiny = TierConfig(
        model="tiny",
        max_output=10,
        supports_tools=False,
        supports_vision=False,
        max_context=125,
    )
    settings = replace(settings, fast=tiny, context_token_safety_factor=1.15)
    facts = RequestFacts(
        estimated_message_tokens=100,
        estimated_tool_schema_tokens=0,
        estimated_tool_result_tokens=0,
        estimated_total_tokens=100,
        has_tools=False,
        has_vision=False,
        structured_output=False,
        code_blocks=0,
        referenced_files=0,
        requested_output_tokens=10,
        text="",
    )
    # Old calculation: 100 + 10 = 110 <= 125. v0.4.0: ceil(100*1.15)+10 = 125.
    assert tier_satisfies_capabilities("fast", facts, settings) is True
    settings = replace(settings, fast=replace(tiny, max_context=124))
    assert tier_satisfies_capabilities("fast", facts, settings) is False
