"""Runtime configuration for the NotebookLM worker.

Every key is read from ``NOTEBOOKLM_*`` first and falls back to the
``CONTENT_NOTEBOOKLM_*`` spelling the stack ``.env`` uses for the shared
values (profile path, timeout, default profile), so the operator can set one
key and have both the worker and the Content Bot pick it up.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        value = os.environ.get(f"CONTENT_{name}")
    if value is None:
        return default
    return value.strip() or default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "").lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "")
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
    port: int = 8860
    api_token: str = ""
    enabled: bool = True
    browser_profile: str = "/data/notebooklm-browser-profile"
    session_mode: str = "persistent"
    cdp_url: str = "http://host.docker.internal:9222"
    headless: bool = True
    home_url: str = "https://notebook.google.com/"
    timeout_seconds: int = 1800
    step_timeout_seconds: int = 45
    source_timeout_seconds: int = 300
    video_timeout_seconds: int = 1500
    download_timeout_seconds: int = 300
    poll_seconds: int = 15
    default_profile: str = "technical_fa"
    locale: str = "fa-IR"
    timezone_id: str = "Asia/Tehran"
    log_level: str = "INFO"
    keep_screenshots: bool = True
    trim_last_seconds: int = 0
    upload_ttl_seconds: int = 86400
    session_import_path: str = ""
    google_email: str = ""
    google_password: str = ""
    google_totp_secret: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=_env("NOTEBOOKLM_DATA_DIR", "/data"),
            bind_ip=_env("NOTEBOOKLM_BIND_IP", "127.0.0.1"),
            port=_env_int("NOTEBOOKLM_PORT", 8860),
            api_token=_env("NOTEBOOKLM_API_TOKEN"),
            enabled=_env_bool("NOTEBOOKLM_ENABLED", True),
            browser_profile=_env(
                "NOTEBOOKLM_BROWSER_PROFILE", "/data/notebooklm-browser-profile"
            ),
            session_mode=_env("NOTEBOOKLM_SESSION_MODE", "persistent").lower(),
            cdp_url=_env("NOTEBOOKLM_CDP_URL", "http://host.docker.internal:9222"),
            session_import_path=_env("NOTEBOOKLM_SESSION_IMPORT", ""),
            google_email=_env("NOTEBOOKLM_GOOGLE_EMAIL", ""),
            google_password=_env("NOTEBOOKLM_GOOGLE_PASSWORD", ""),
            google_totp_secret=_env("NOTEBOOKLM_GOOGLE_TOTP_SECRET", ""),
            headless=_env_bool("NOTEBOOKLM_HEADLESS", True),
            home_url=_env("NOTEBOOKLM_HOME_URL", "https://notebook.google.com/"),
            timeout_seconds=_env_int("NOTEBOOKLM_TIMEOUT", 1800),
            step_timeout_seconds=_env_int("NOTEBOOKLM_STEP_TIMEOUT_SECONDS", 45),
            source_timeout_seconds=_env_int("NOTEBOOKLM_SOURCE_TIMEOUT_SECONDS", 300),
            video_timeout_seconds=_env_int("NOTEBOOKLM_VIDEO_TIMEOUT_SECONDS", 1500),
            download_timeout_seconds=_env_int(
                "NOTEBOOKLM_DOWNLOAD_TIMEOUT_SECONDS", 300
            ),
            poll_seconds=_env_int("NOTEBOOKLM_POLL_SECONDS", 15),
            default_profile=_env("NOTEBOOKLM_DEFAULT_PROFILE", "technical_fa"),
            locale=_env("NOTEBOOKLM_LOCALE", "fa-IR"),
            timezone_id=_env("NOTEBOOKLM_TIMEZONE", "Asia/Tehran"),
            log_level=_env("NOTEBOOKLM_LOG_LEVEL", "INFO").upper(),
            keep_screenshots=_env_bool("NOTEBOOKLM_KEEP_SCREENSHOTS", True),
            trim_last_seconds=_env_int("NOTEBOOKLM_TRIM_LAST_SECONDS", 0),
            upload_ttl_seconds=_env_int("NOTEBOOKLM_UPLOAD_TTL_SECONDS", 86400),
        )
