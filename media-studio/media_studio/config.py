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
    drivers: tuple[str, ...] = ("api-image", "api-video", "flow-video", "video-edit", "timeline-video")
    writer_base_url: str = ""
    writer_api_key: str = ""
    writer_model: str = ""
    video_base_url: str = ""
    video_api_key: str = ""
    video_model: str = ""
    video_provider: str = ""
    video_duration: str = ""
    video_aspect_ratio: str = ""
    video_resolution: str = ""
    video_poll_seconds: int = 10
    video_timeout_seconds: int = 900
    image_size: str = "1024x1024"
    scene_image_enabled: bool = True
    scene_image_size: str = "1024x1792"
    scene_image_max: int = 8
    brand_label: str = "Locallab"
    brand_position: str = "bottom-right"
    brand_style: str = "aurora"
    ffmpeg_binary: str = ""
    video_edit_max_side: int = 1920
    video_edit_max_seconds: int = 0
    video_edit_timeout_seconds: int = 900
    timeline_timeout_seconds: int = 1800
    upload_ttl_seconds: int = 86400
    session_mode: str = "persistent"
    cdp_url: str = "http://127.0.0.1:9222"
    headless: bool = False
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
                "MEDIA_STUDIO_DRIVERS", "api-image,api-video,flow-video,video-edit,timeline-video"
            ).split(",")
            if part.strip()
        )
        raw_brand = os.environ.get("MEDIA_STUDIO_BRAND_LABEL")
        brand_label = raw_brand.strip() if raw_brand is not None else "Locallab"
        writer_base_url = _env("MEDIA_STUDIO_WRITER_BASE_URL") or _env("CONTENT_WRITER_BASE_URL")
        writer_api_key = _env("MEDIA_STUDIO_WRITER_API_KEY") or _env("CONTENT_WRITER_API_KEY")
        # Video jobs reuse the writer endpoint and key unless the operator
        # points them at a different gateway: one router can serve chat,
        # images, and video, while the video provider may live elsewhere.
        video_base_url = _env("MEDIA_STUDIO_VIDEO_BASE_URL") or writer_base_url
        video_api_key = _env("MEDIA_STUDIO_VIDEO_API_KEY") or writer_api_key
        return cls(
            data_dir=_env("MEDIA_STUDIO_DATA_DIR", "/data"),
            bind_ip=_env("MEDIA_STUDIO_BIND_IP", "127.0.0.1"),
            port=_env_int("MEDIA_STUDIO_PORT", 8850),
            api_token=_env("MEDIA_STUDIO_API_TOKEN"),
            drivers=drivers or ("api-image", "video-edit"),
            writer_base_url=writer_base_url,
            writer_api_key=writer_api_key,
            writer_model=_env("MEDIA_STUDIO_WRITER_MODEL") or _env("CONTENT_WRITER_MODEL", "auto"),
            video_base_url=video_base_url,
            video_api_key=video_api_key,
            video_model=_env("MEDIA_STUDIO_VIDEO_MODEL"),
            video_provider=_env("MEDIA_STUDIO_VIDEO_PROVIDER"),
            video_duration=_env("MEDIA_STUDIO_VIDEO_DURATION"),
            video_aspect_ratio=_env("MEDIA_STUDIO_VIDEO_ASPECT_RATIO"),
            video_resolution=_env("MEDIA_STUDIO_VIDEO_RESOLUTION"),
            video_poll_seconds=_env_int("MEDIA_STUDIO_VIDEO_POLL_SECONDS", 10),
            video_timeout_seconds=_env_int("MEDIA_STUDIO_VIDEO_TIMEOUT_SECONDS", 900),
            image_size=_env("MEDIA_STUDIO_IMAGE_SIZE", "1024x1024"),
            scene_image_enabled=_env_bool("MEDIA_STUDIO_SCENE_IMAGES", True),
            scene_image_size=_env("MEDIA_STUDIO_SCENE_IMAGE_SIZE", "1024x1792"),
            scene_image_max=_env_int("MEDIA_STUDIO_SCENE_IMAGE_MAX", 8),
            brand_label=brand_label,
            brand_position=_env("MEDIA_STUDIO_BRAND_POSITION", "bottom-right"),
            brand_style=_env("MEDIA_STUDIO_BRAND_STYLE", "aurora"),
            ffmpeg_binary=_env("MEDIA_STUDIO_FFMPEG"),
            video_edit_max_side=_env_int("MEDIA_STUDIO_VIDEO_EDIT_MAX_SIDE", 1920),
            video_edit_max_seconds=_env_int("MEDIA_STUDIO_VIDEO_EDIT_MAX_SECONDS", 0),
            video_edit_timeout_seconds=_env_int(
                "MEDIA_STUDIO_VIDEO_EDIT_TIMEOUT_SECONDS", 900
            ),
            timeline_timeout_seconds=_env_int(
                "MEDIA_STUDIO_TIMELINE_TIMEOUT_SECONDS", 1800
            ),
            upload_ttl_seconds=_env_int("MEDIA_STUDIO_UPLOAD_TTL_SECONDS", 86400),
            session_mode=_env("MEDIA_STUDIO_SESSION_MODE", "persistent").lower(),
            cdp_url=_env("MEDIA_STUDIO_CDP_URL", "http://127.0.0.1:9222"),
            headless=_env_bool("MEDIA_STUDIO_HEADLESS", False),
            block_geo_redirect=_env_bool("MEDIA_STUDIO_BLOCK_GEO_REDIRECT", True),
            freeze_on_ready=_env_bool("MEDIA_STUDIO_FREEZE_ON_READY", True),
            job_timeout_seconds=_env_int("MEDIA_STUDIO_JOB_TIMEOUT_SECONDS", 900),
            step_timeout_seconds=_env_int("MEDIA_STUDIO_STEP_TIMEOUT_SECONDS", 30),
            locale=_env("MEDIA_STUDIO_LOCALE", "en-US"),
            timezone_id=_env("MEDIA_STUDIO_TIMEZONE", "UTC"),
            log_level=_env("MEDIA_STUDIO_LOG_LEVEL", "INFO").upper(),
        )
