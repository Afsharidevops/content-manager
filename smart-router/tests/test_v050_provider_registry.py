from __future__ import annotations

from smart_router.provider_registry import env_map, validate_profile


def test_provider_profile_is_gateway_agnostic():
    profile = {
        "name": "generic",
        "upstream_base_url": "http://gateway:8000/v1",
        "upstream_health_url": "http://gateway:8000/health",
        "tiers": {
            "fast": {"model": "cheap", "supports_tools": False, "supports_vision": False, "max_context": 32000},
            "standard": {"model": "mid", "supports_tools": True, "supports_vision": False, "max_context": 128000},
            "strong": {"model": "best", "supports_tools": True, "supports_vision": True, "max_context": 200000},
        },
    }
    validate_profile(profile)
    values = env_map(profile)
    assert values["SMART_ROUTER_UPSTREAM_BASE_URL"] == "http://gateway:8000/v1"
    assert values["SMART_ROUTER_FAST_MODEL"] == "cheap"
    assert values["SMART_ROUTER_STRONG_SUPPORTS_VISION"] == "true"
