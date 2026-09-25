"""Runtime configuration loaded from the container environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


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


def _parse_tags(raw: str) -> tuple[str, ...]:
    """Return the comma- or space-separated platform tags of one env value."""
    tags: list[str] = []
    for part in re.split(r"[,\n]", raw or ""):
        tag = " ".join(part.split()).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tuple(tags)


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
    writer_model: str = "auto-strong"
    writer_max_tokens: int = 4000
    writer_reasoning_effort: str = ""
    policy_dir: str = "/policy"
    data_dir: str = "/data"
    scheduler_enabled: bool = True
    media_studio_url: str = ""
    media_studio_token: str = ""
    media_job_timeout_seconds: int = 1200
    notebooklm_url: str = ""
    notebooklm_token: str = ""
    notebooklm_enabled: bool = True
    notebooklm_timeout: int = 1800
    notebooklm_default_profile: str = "technical_fa"
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
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_media_public_base_url: str = ""
    instagram_api_base: str = "https://graph.facebook.com"
    instagram_api_version: str = "v26.0"
    instagram_poll_timeout_seconds: int = 600
    instagram_disable_refresh: bool = False
    instagram_auto_publish: bool = True
    platforms_enabled: bool = False
    bale_token: str = ""
    bale_chat_id: str = ""
    bale_api_base: str = "https://tapi.bale.ai"
    eitaa_token: str = ""
    eitaa_chat_id: str = ""
    eitaa_api_base: str = "https://eitaayar.ir/api"
    social_accounts_file: str = "social-accounts.yaml"
    adapt_enabled: bool = True
    prompt_dir: str = ""
    aparat_token: str = ""
    aparat_cookie: str = ""
    aparat_api_base: str = "https://www.aparat.com"
    aparat_category: str = "10"
    aparat_tags: tuple[str, ...] = ()
    aparat_watermark: str = "1"
    aparat_video_pass: str = "0"
    aparat_label: str = ""
    aparat_timeout: int = 120
    aparat_chunk_bytes: int = 3 * 1024 * 1024
    linkedin_api_base: str = "https://api.linkedin.com"
    linkedin_api_version: str = "202601"
    linkedin_timeout: int = 60
    linkedin_retries: int = 3

    @classmethod
    def from_env(cls) -> "BotSettings":
        return cls(
            bot_token=_env("CONTENT_BOT_TOKEN"),
            telegram_channel=_env("CONTENT_TELEGRAM_CHANNEL"),
            telegram_users=_parse_user_ids(_env("CONTENT_TELEGRAM_USERS")),
            telegram_api_base=_env("CONTENT_TELEGRAM_API_BASE", "https://api.telegram.org"),
            writer_base_url=_env("CONTENT_WRITER_BASE_URL"),
            writer_api_key=_env("CONTENT_WRITER_API_KEY"),
            writer_model=_env("CONTENT_WRITER_MODEL", "auto-strong"),
            writer_max_tokens=_env_int("CONTENT_WRITER_MAX_TOKENS", 4000),
            writer_reasoning_effort=_env("CONTENT_WRITER_REASONING_EFFORT"),
            policy_dir=_env("CONTENT_POLICY_DIR", "/policy"),
            data_dir=_env("CONTENT_DATA_DIR", "/data"),
            scheduler_enabled=_env_bool("CONTENT_SCHEDULER_ENABLED", True),
            media_studio_url=_env("CONTENT_MEDIA_STUDIO_URL"),
            media_studio_token=_env("CONTENT_MEDIA_STUDIO_TOKEN"),
            media_job_timeout_seconds=_env_int("CONTENT_MEDIA_JOB_TIMEOUT_SECONDS", 1200),
            notebooklm_url=_env("CONTENT_NOTEBOOKLM_URL"),
            notebooklm_token=_env("CONTENT_NOTEBOOKLM_TOKEN"),
            notebooklm_enabled=_env_bool("CONTENT_NOTEBOOKLM_ENABLED", True),
            notebooklm_timeout=_env_int("CONTENT_NOTEBOOKLM_TIMEOUT", 1800),
            notebooklm_default_profile=_env(
                "CONTENT_NOTEBOOKLM_DEFAULT_PROFILE", "technical_fa"
            ),
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
            instagram_app_id=_env("INSTAGRAM_APP_ID"),
            instagram_app_secret=_env("INSTAGRAM_APP_SECRET"),
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
            instagram_disable_refresh=_env_bool("INSTAGRAM_DISABLE_REFRESH", False),
            instagram_auto_publish=_env_bool("INSTAGRAM_AUTO_PUBLISH", True),
            platforms_enabled=_env_bool("CONTENT_PLATFORMS_ENABLED", False),
            bale_token=_env("CONTENT_BALE_TOKEN"),
            bale_chat_id=_env("CONTENT_BALE_CHAT_ID"),
            bale_api_base=_env("CONTENT_BALE_API_BASE", "https://tapi.bale.ai"),
            eitaa_token=_env("CONTENT_EITAA_TOKEN"),
            eitaa_chat_id=_env("CONTENT_EITAA_CHAT_ID"),
            eitaa_api_base=_env("CONTENT_EITAA_API_BASE", "https://eitaayar.ir/api"),
            social_accounts_file=_env(
                "CONTENT_SOCIAL_ACCOUNTS_FILE", "social-accounts.yaml"
            ),
            adapt_enabled=_env_bool("CONTENT_ADAPT_ENABLED", True),
            prompt_dir=_env("CONTENT_PROMPT_DIR"),
            aparat_token=_env("CONTENT_APARAT_TOKEN"),
            aparat_cookie=_env("CONTENT_APARAT_COOKIE"),
            aparat_api_base=_env("CONTENT_APARAT_API_BASE", "https://www.aparat.com"),
            aparat_category=_env("CONTENT_APARAT_CATEGORY", "10"),
            aparat_tags=_parse_tags(_env("CONTENT_APARAT_TAGS")),
            aparat_watermark=_env("CONTENT_APARAT_WATERMARK", "1"),
            aparat_video_pass=_env("CONTENT_APARAT_VIDEO_PASS", "0"),
            aparat_label=_env("CONTENT_APARAT_LABEL"),
            aparat_timeout=_env_int("CONTENT_APARAT_TIMEOUT", 120),
            aparat_chunk_bytes=_env_int(
                "CONTENT_APARAT_CHUNK_BYTES", 3 * 1024 * 1024
            ),
            linkedin_api_base=_env(
                "CONTENT_LINKEDIN_API_BASE", "https://api.linkedin.com"
            ),
            linkedin_api_version=_env("CONTENT_LINKEDIN_API_VERSION", "202601"),
            linkedin_timeout=_env_int("CONTENT_LINKEDIN_TIMEOUT", 60),
            linkedin_retries=_env_int("CONTENT_LINKEDIN_RETRIES", 3),
        )

    @property
    def prompt_templates_dir(self) -> str:
        """Directory that holds deployment prompt template overrides."""
        return self.prompt_dir or str(Path(self.policy_dir) / "prompt-templates")

    @property
    def video_character_enabled(self) -> bool:
        """True when a saved Flow character can carry the reel narration."""
        return bool(self.video_character)

    @property
    def aparat_enabled(self) -> bool:
        """True when an Aparat browser session is stored."""
        return bool(self.aparat_token or self.aparat_cookie)

    @property
    def instagram_enabled(self) -> bool:
        """True when enough Graph API configuration exists to publish."""
        return bool(self.instagram_business_id and self.instagram_access_token)

    @property
    def instagram_publish_enabled(self) -> bool:
        """True when the bot may publish to Instagram without a human step.

        Credentials alone are not enough: ``INSTAGRAM_AUTO_PUBLISH`` lets the
        operator keep the Instagram API buttons off (for example while Meta
        reviews the account) and hand the operator a copy-ready post package
        for a manual upload instead.
        """
        return bool(self.instagram_enabled and self.instagram_auto_publish)
