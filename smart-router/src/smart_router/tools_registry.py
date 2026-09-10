"""Read the shared tool registry (content/config/tools.json).

The content stack keeps one registry file that lists every remote tool source
(MCP servers, OpenAPI services, plain HTTP endpoints) together with the
services allowed to call it. The Content Bot reads it directly, n8n reads it
from the bootstrap script, and the router exposes the entries it may use on
``GET /v1/tools``. The file is plain JSON so every runtime parses it without
extra dependencies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

SCHEMA_VERSION = 1
CONSUMER = "router"
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ToolsRegistryError(ValueError):
    """Raised when the registry file is unreadable or invalid."""


@dataclass(frozen=True)
class Tool:
    id: str
    title: str
    kind: str
    endpoint: str
    spec_url: str = ""
    transport: str = ""
    auth_type: str = "none"
    auth_env: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "endpoint": self.endpoint,
            "spec_url": self.spec_url,
            "transport": self.transport,
            "auth": {"type": self.auth_type, "env": self.auth_env},
            "capabilities": list(self.capabilities),
            "notes": self.notes,
        }


def _resolve(value: str, env: Mapping[str, str]) -> str:
    return _PLACEHOLDER.sub(lambda match: str(env.get(match.group(1)) or ""), str(value or ""))


def load_tools(path: str | Path, env: Mapping[str, str] | None = None) -> tuple[Tool, ...]:
    """Return the registry entries this router may call.

    A missing path means the deployment ships no registry, which is not an
    error: the caller reports an empty list.
    """
    location = Path(str(path or ""))
    if not str(path or "").strip() or not location.is_file():
        return ()
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ToolsRegistryError(f"{location} could not be read: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ToolsRegistryError(f"{location} is not a schema_version {SCHEMA_VERSION} registry")
    entries = payload.get("tools")
    if not isinstance(entries, list):
        raise ToolsRegistryError(f"{location} has no tools list")
    source = env if env is not None else {}
    tools: list[Tool] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        consumers = [str(item) for item in (entry.get("consumers") or [])]
        if CONSUMER not in consumers:
            continue
        tool_id = str(entry.get("id") or "").strip()
        if not tool_id:
            continue
        auth = entry.get("auth") if isinstance(entry.get("auth"), dict) else {}
        tools.append(
            Tool(
                id=tool_id,
                title=str(entry.get("title") or tool_id),
                kind=str(entry.get("kind") or "").strip().lower(),
                endpoint=_resolve(str(entry.get("url") or entry.get("base_url") or ""), source),
                spec_url=_resolve(str(entry.get("spec_url") or ""), source),
                transport=str(entry.get("transport") or ""),
                auth_type=str(auth.get("type") or "none"),
                auth_env=str(auth.get("env") or ""),
                capabilities=tuple(
                    str(item) for item in (entry.get("capabilities") or []) if str(item).strip()
                ),
                notes=str(entry.get("notes") or ""),
            )
        )
    return tuple(tools)
