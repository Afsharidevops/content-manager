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


@dataclass(frozen=True)
class Settings:
    data_dir: str = "/data"
    bind_ip: str = "127.0.0.1"
    port: int = 8850
    api_token: str = ""
    drivers: tuple[str, ...] = ("api-image", "flow-video", "video-edit")
    writer_base_url: str = ""
    writer_api_key: str = ""
    writer_model: str = ""
    image_size: str = "1024x1024"
    brand_label: str = "Locallab"
    brand_position: str = "bottom-right"
    ffmpeg_binary: str = ""
    video_edit_max_side: int = 1920
    video_edit_max_seconds: int = 0
    video_edit_timeout_seconds: int = 900
    upload_ttl_seconds: int = 86400
    session_mode: str = "cdp"
    cdp_url: str = "http://127.0.0.1:9222"
    headless: bool = True
    block_geo_redirect: bool = True
    freeze_on_ready: bool = True
    job_timeout_seconds: int = 900
    step_timeout_seconds: int = 30
    locale: str = "en-US"
    timezone_id: str = "UTC"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        drivers = tuple(
            part.strip()
            for part in _env(
                "MEDIA_STUDIO_DRIVERS", "api-image,flow-video,video-edit"
            ).split(",")
            if part.strip()
        )
        raw_brand = os.environ.get("MEDIA_STUDIO_BRAND_LABEL")
        brand_label = raw_brand.strip() if raw_brand is not None else "Locallab"
        return cls(
            data_dir=_env("MEDIA_STUDIO_DATA_DIR", "/data"),
            bind_ip=_env("MEDIA_STUDIO_BIND_IP", "127.0.0.1"),
            port=_env_int("MEDIA_STUDIO_PORT", 8850),
            api_token=_env("MEDIA_STUDIO_API_TOKEN"),
            drivers=drivers or ("api-image", "video-edit"),
            writer_base_url=_env("MEDIA_STUDIO_WRITER_BASE_URL") or _env("CONTENT_WRITER_BASE_URL"),
            writer_api_key=_env("MEDIA_STUDIO_WRITER_API_KEY") or _env("CONTENT_WRITER_API_KEY"),
            writer_model=_env("MEDIA_STUDIO_WRITER_MODEL") or _env("CONTENT_WRITER_MODEL", "auto"),
            image_size=_env("MEDIA_STUDIO_IMAGE_SIZE", "1024x1024"),
            brand_label=brand_label,
            brand_position=_env("MEDIA_STUDIO_BRAND_POSITION", "bottom-right"),
            ffmpeg_binary=_env("MEDIA_STUDIO_FFMPEG"),
            video_edit_max_side=_env_int("MEDIA_STUDIO_VIDEO_EDIT_MAX_SIDE", 1920),
            video_edit_max_seconds=_env_int("MEDIA_STUDIO_VIDEO_EDIT_MAX_SECONDS", 0),
            video_edit_timeout_seconds=_env_int(
                "MEDIA_STUDIO_VIDEO_EDIT_TIMEOUT_SECONDS", 900
            ),
            upload_ttl_seconds=_env_int("MEDIA_STUDIO_UPLOAD_TTL_SECONDS", 86400),
            session_mode=_env("MEDIA_STUDIO_SESSION_MODE", "cdp").lower(),
            cdp_url=_env("MEDIA_STUDIO_CDP_URL", "http://127.0.0.1:9222"),
            headless=_env_bool("MEDIA_STUDIO_HEADLESS", True),
            block_geo_redirect=_env_bool("MEDIA_STUDIO_BLOCK_GEO_REDIRECT", True),
            freeze_on_ready=_env_bool("MEDIA_STUDIO_FREEZE_ON_READY", True),
            job_timeout_seconds=_env_int("MEDIA_STUDIO_JOB_TIMEOUT_SECONDS", 900),
            step_timeout_seconds=_env_int("MEDIA_STUDIO_STEP_TIMEOUT_SECONDS", 30),
            locale=_env("MEDIA_STUDIO_LOCALE", "en-US"),
            timezone_id=_env("MEDIA_STUDIO_TIMEZONE", "UTC"),
            log_level=_env("MEDIA_STUDIO_LOG_LEVEL", "INFO").upper(),
        )
