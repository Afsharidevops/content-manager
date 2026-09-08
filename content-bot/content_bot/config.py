"""Runtime configuration loaded from the container environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_user_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return frozenset(ids)


@dataclass(frozen=True)
class BotSettings:
    bot_token: str
    telegram_channel: str = ""
    telegram_users: frozenset[int] = frozenset()
    telegram_api_base: str = "https://api.telegram.org"
    writer_base_url: str = ""
    writer_api_key: str = ""
    writer_model: str = "auto"
    writer_max_tokens: int = 1600
    writer_reasoning_effort: str = ""
    policy_dir: str = "/policy"
    data_dir: str = "/data"
    scheduler_enabled: bool = True

    @classmethod
    def from_env(cls) -> "BotSettings":
        return cls(
            bot_token=_env("CONTENT_BOT_TOKEN"),
            telegram_channel=_env("CONTENT_TELEGRAM_CHANNEL"),
            telegram_users=_parse_user_ids(_env("CONTENT_TELEGRAM_USERS")),
            telegram_api_base=_env("CONTENT_TELEGRAM_API_BASE", "https://api.telegram.org"),
            writer_base_url=_env("CONTENT_WRITER_BASE_URL"),
            writer_api_key=_env("CONTENT_WRITER_API_KEY"),
            writer_model=_env("CONTENT_WRITER_MODEL", "auto"),
            writer_max_tokens=_env_int("CONTENT_WRITER_MAX_TOKENS", 1600),
            writer_reasoning_effort=_env("CONTENT_WRITER_REASONING_EFFORT"),
            policy_dir=_env("CONTENT_POLICY_DIR", "/policy"),
            data_dir=_env("CONTENT_DATA_DIR", "/data"),
            scheduler_enabled=_env_bool("CONTENT_SCHEDULER_ENABLED", True),
        )
