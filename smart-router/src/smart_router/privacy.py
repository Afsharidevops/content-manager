from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping


def session_identity(
    headers: Mapping[str, str], body: dict[str, Any], secret: str
) -> tuple[str | None, str]:
    raw: str | None = None
    source = "none"
    for header in ("x-router-session", "x-task-id", "x-session-id"):
        value = headers.get(header)
        if value:
            raw = value
            source = header
            break
    if raw is None:
        metadata = body.get("metadata")
        if isinstance(metadata, dict):
            for key in ("session_id", "conversation_id", "task_id"):
                value = metadata.get(key)
                if isinstance(value, (str, int)) and str(value):
                    raw = str(value)
                    source = f"metadata.{key}"
                    break
    if raw is None:
        return None, source
    digest = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return digest, source


def privacy_safe_json(payload: dict[str, Any]) -> str:
    """Serialize already-derived routing metadata; rejects obvious unsafe keys."""
    forbidden = {
        "prompt",
        "messages",
        "system_message",
        "tool_arguments",
        "authorization",
        "api_key",
        "bearer",
        "response_text",
    }
    lowered = {str(key).lower() for key in _walk_keys(payload)}
    unsafe = sorted(lowered & forbidden)
    if unsafe:
        raise ValueError(f"unsafe observation keys: {', '.join(unsafe)}")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)
