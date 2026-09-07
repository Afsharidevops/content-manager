from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

TIERS = ("fast", "standard", "strong")


def load_profile(path: str | Path) -> dict[str, Any]:
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(profile, Mapping):
        raise ValueError("provider profile must be a JSON object")
    profile = dict(profile)
    validate_profile(profile)
    return profile


def validate_profile(profile: Mapping[str, Any]) -> None:
    name = str(profile.get("name", "")).strip()
    if not name:
        raise ValueError("provider profile requires a non-empty name")
    base = str(profile.get("upstream_base_url", "")).strip()
    health = str(profile.get("upstream_health_url", "")).strip()
    if not base.startswith(("http://", "https://")):
        raise ValueError("upstream_base_url must be an http(s) URL")
    if not health.startswith(("http://", "https://")):
        raise ValueError("upstream_health_url must be an http(s) URL")
    tiers = profile.get("tiers")
    if not isinstance(tiers, Mapping):
        raise ValueError("provider profile requires a tiers object")
    for tier in TIERS:
        item = tiers.get(tier)
        if not isinstance(item, Mapping):
            raise ValueError(f"provider profile is missing tiers.{tier}")
        model = str(item.get("model", "")).strip()
        if not model:
            raise ValueError(f"tiers.{tier}.model must be non-empty")
        for field in ("supports_tools", "supports_vision"):
            if field in item and not isinstance(item[field], bool):
                raise ValueError(f"tiers.{tier}.{field} must be boolean")
        if "max_context" in item and int(item["max_context"]) <= 0:
            raise ValueError(f"tiers.{tier}.max_context must be > 0")


def env_map(profile: Mapping[str, Any]) -> dict[str, str]:
    validate_profile(profile)
    result = {
        "SMART_ROUTER_UPSTREAM_BASE_URL": str(profile["upstream_base_url"]),
        "SMART_ROUTER_UPSTREAM_HEALTH_URL": str(profile["upstream_health_url"]),
    }
    tiers = profile["tiers"]
    for tier in TIERS:
        item = tiers[tier]
        prefix = f"SMART_ROUTER_{tier.upper()}"
        result[f"{prefix}_MODEL"] = str(item["model"])
        if "supports_tools" in item:
            result[f"{prefix}_SUPPORTS_TOOLS"] = str(bool(item["supports_tools"])).lower()
        if "supports_vision" in item:
            result[f"{prefix}_SUPPORTS_VISION"] = str(bool(item["supports_vision"])).lower()
        if "max_context" in item:
            result[f"{prefix}_MAX_CONTEXT"] = str(int(item["max_context"]))
        if "max_tokens" in item:
            result[f"{prefix}_MAX_TOKENS"] = str(int(item["max_tokens"]))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a provider/gateway profile and emit portable Smart Router environment settings."
    )
    parser.add_argument("profile", type=Path)
    parser.add_argument("--format", choices=("env", "json"), default="env")
    args = parser.parse_args()
    profile = load_profile(args.profile)
    values = env_map(profile)
    if args.format == "json":
        print(json.dumps(values, indent=2, sort_keys=True))
    else:
        for name in sorted(values):
            print(f"{name}={values[name]}")


if __name__ == "__main__":
    main()
