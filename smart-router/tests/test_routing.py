import pytest
from dataclasses import replace

from smart_router.routing import decide


def test_simple_translation_is_fast(settings):
    decision = decide({"model": "auto", "messages": [{"role": "user", "content": "Translate hello to French"}]}, settings)
    assert decision.proposed_tier == "fast"


def test_tools_upgrade_forced_fast_to_standard(settings):
    body = {
        "model": "auto-fast",
        "messages": [{"role": "user", "content": "Read the repository"}],
        "tools": [{"type": "function", "function": {"name": "read"}}],
    }
    decision = decide(body, settings, requested_tier="fast")
    assert decision.proposed_tier == "standard"
    assert decision.capability_upgrade == "tools"


def test_vision_upgrades_to_strong(settings):
    body = {
        "model": "auto",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}]}],
    }
    decision = decide(body, settings)
    assert decision.proposed_tier == "strong"
    assert decision.capability_upgrade == "vision"


def test_context_gate_is_authoritative(settings):
    small = replace(settings, fast=replace(settings.fast, max_context=100), standard=replace(settings.standard, max_context=200))
    body = {"model": "auto-fast", "messages": [{"role": "user", "content": "x" * 1000}], "max_tokens": 300}
    decision = decide(body, small, requested_tier="fast")
    assert decision.proposed_tier == "strong"
    assert decision.capability_upgrade == "context"


def test_auto_strong_stays_strong(settings):
    body = {"model": "auto-strong", "messages": [{"role": "user", "content": "hi"}]}
    assert decide(body, settings, requested_tier="strong").proposed_tier == "strong"


def test_auto_standard_stays_at_least_standard(settings):
    body = {"model": "auto-standard", "messages": [{"role": "user", "content": "hi"}]}
    assert decide(body, settings, requested_tier="standard").proposed_tier == "standard"


def test_settings_reject_non_monotonic_tool_capabilities(monkeypatch, tmp_path):
    from smart_router.config import Settings
    monkeypatch.setenv("SMART_ROUTER_HMAC_SECRET", "test-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    monkeypatch.setenv("SMART_ROUTER_UPSTREAM_BASE_URL", "http://upstream.test/v1")
    monkeypatch.setenv("SMART_ROUTER_DATABASE_PATH", str(tmp_path / "router.sqlite3"))
    monkeypatch.setenv("SMART_ROUTER_FAST_SUPPORTS_TOOLS", "false")
    monkeypatch.setenv("SMART_ROUTER_STANDARD_SUPPORTS_TOOLS", "true")
    monkeypatch.setenv("SMART_ROUTER_STRONG_SUPPORTS_TOOLS", "false")
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "supports_tools" in str(exc)
    else:
        raise AssertionError("non-monotonic tool capability config must fail startup")


def test_settings_reject_non_monotonic_context(monkeypatch, tmp_path):
    from smart_router.config import Settings
    monkeypatch.setenv("SMART_ROUTER_HMAC_SECRET", "test-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    monkeypatch.setenv("SMART_ROUTER_UPSTREAM_BASE_URL", "http://upstream.test/v1")
    monkeypatch.setenv("SMART_ROUTER_DATABASE_PATH", str(tmp_path / "router.sqlite3"))
    monkeypatch.setenv("SMART_ROUTER_FAST_MAX_CONTEXT", "32000")
    monkeypatch.setenv("SMART_ROUTER_STANDARD_MAX_CONTEXT", "128000")
    monkeypatch.setenv("SMART_ROUTER_STRONG_MAX_CONTEXT", "64000")
    try:
        Settings.from_env()
    except ValueError as exc:
        assert "MAX_CONTEXT" in str(exc)
    else:
        raise AssertionError("non-monotonic context config must fail startup")


def test_settings_require_explicit_upstream(monkeypatch):
    from smart_router.config import Settings

    monkeypatch.setenv(
        "SMART_ROUTER_HMAC_SECRET",
        "test-secret-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    )
    monkeypatch.delenv("SMART_ROUTER_UPSTREAM_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="SMART_ROUTER_UPSTREAM_BASE_URL must be set"):
        Settings.from_env()
