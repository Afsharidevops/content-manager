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
    media_studio_url: str = ""
    media_studio_token: str = ""
    media_job_timeout_seconds: int = 1200
    image_driver: str = "api-image"
    video_driver: str = "flow-video"
    video_edit_driver: str = "video-edit"
    search_enabled: bool = True
    topic_drafts_enabled: bool = True
    search_max_results: int = 5
    search_timeout: int = 25
    video_character: str = ""
    video_character_prompt: str = ""
    video_aspect: str = "9:16 vertical"
    video_segment_seconds: int = 10
    instagram_business_id: str = ""
    instagram_access_token: str = ""
    instagram_media_public_base_url: str = ""
    instagram_api_base: str = "https://graph.facebook.com"
    instagram_api_version: str = "v26.0"
    instagram_poll_timeout_seconds: int = 600
    platforms_enabled: bool = False

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
            media_studio_url=_env("CONTENT_MEDIA_STUDIO_URL"),
            media_studio_token=_env("CONTENT_MEDIA_STUDIO_TOKEN"),
            media_job_timeout_seconds=_env_int("CONTENT_MEDIA_JOB_TIMEOUT_SECONDS", 1200),
            image_driver=_env("CONTENT_MEDIA_IMAGE_DRIVER", "api-image"),
            video_driver=_env("CONTENT_MEDIA_VIDEO_DRIVER", "flow-video"),
            video_edit_driver=_env("CONTENT_MEDIA_VIDEO_EDIT_DRIVER", "video-edit"),
            search_enabled=_env_bool("CONTENT_SEARCH_ENABLED", True),
            topic_drafts_enabled=_env_bool("CONTENT_TOPIC_DRAFTS_ENABLED", True),
            search_max_results=_env_int("CONTENT_SEARCH_MAX_RESULTS", 5),
            search_timeout=_env_int("CONTENT_SEARCH_TIMEOUT", 25),
            video_character=_env("CONTENT_VIDEO_CHARACTER"),
            video_character_prompt=_env("CONTENT_VIDEO_CHARACTER_PROMPT"),
            video_aspect=_env("CONTENT_VIDEO_ASPECT", "9:16 vertical"),
            video_segment_seconds=_env_int("CONTENT_VIDEO_SEGMENT_SECONDS", 10),
            instagram_business_id=_env("INSTAGRAM_BUSINESS_ID"),
            instagram_access_token=_env("INSTAGRAM_ACCESS_TOKEN"),
            instagram_media_public_base_url=_env(
                "INSTAGRAM_MEDIA_PUBLIC_BASE_URL"
            ),
            instagram_api_base=_env(
                "INSTAGRAM_API_BASE", "https://graph.facebook.com"
            ),
            instagram_api_version=_env("INSTAGRAM_API_VERSION", "v26.0"),
            instagram_poll_timeout_seconds=_env_int(
                "INSTAGRAM_POLL_TIMEOUT_SECONDS", 600
            ),
            platforms_enabled=_env_bool("CONTENT_PLATFORMS_ENABLED", False),
        )

    @property
    def video_character_enabled(self) -> bool:
        """True when a saved Flow character can carry the reel narration."""
        return bool(self.video_character)

    @property
    def instagram_enabled(self) -> bool:
        """True when enough Graph API configuration exists to publish."""
        return bool(self.instagram_business_id and self.instagram_access_token)
