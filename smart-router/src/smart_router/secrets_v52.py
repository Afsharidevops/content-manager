from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def env_or_file(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Read NAME or NAME_FILE with unambiguous, fail-closed semantics.

    *_FILE is compatible with Docker/Kubernetes secrets. If both are populated we
    refuse to guess which one the operator intended. Secret values are never logged.
    """
    direct = os.getenv(name)
    file_name = os.getenv(f"{name}_FILE")
    direct_set = direct is not None and direct.strip() != ""
    file_set = file_name is not None and file_name.strip() != ""
    if direct_set and file_set:
        raise ValueError(f"set only one of {name} or {name}_FILE")
    if file_set:
        path = Path(file_name.strip())
        if not path.is_file():
            raise ValueError(f"{name}_FILE does not reference a readable regular file")
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    elif direct_set:
        value = direct.strip()
    else:
        value = default
    if required and (value is None or not str(value).strip()):
        raise ValueError(f"{name} or {name}_FILE must be set")
    return value


def json_env(name: str, default: dict | None = None) -> dict:
    value = env_or_file(name)
    if not value:
        return dict(default or {})
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return parsed


def redacted_url(value: str) -> str:
    """Return a URL safe for status output by removing password/userinfo."""
    try:
        parts = urlsplit(value)
        if "@" not in parts.netloc:
            return value
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    except Exception:
        return "configured"
