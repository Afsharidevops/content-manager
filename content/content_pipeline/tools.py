"""Shared tool registry for the content stack.

One JSON file lists every remote tool source the stack may call - MCP servers,
OpenAPI services, and plain HTTP endpoints - together with the capabilities it
offers, the services allowed to use it, and the environment variable that
holds its credential. The Content Bot, the Smart Router, and the n8n
bootstrap read the same file, so a new tool is registered once.

The registry never stores secrets: entries name the environment variable
(``"auth": {"env": "MEDIA_STUDIO_API_TOKEN"}``) and URLs may use ``${NAME}``
placeholders that the loader substitutes from the process environment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

SCHEMA_VERSION = 1
KINDS = ("mcp", "openapi", "http")
TRANSPORTS = ("stdio", "sse", "streamable-http", "http")
CONSUMERS = ("bot", "router", "n8n", "media-studio")
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ToolRegistryError(ValueError):
    """Raised when a registry file is unreadable or invalid."""


@dataclass(frozen=True)
class Tool:
    """One registered tool source."""

    id: str
    title: str
    kind: str
    consumers: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    base_url: str = ""
    spec_url: str = ""
    url: str = ""
    transport: str = ""
    auth_type: str = "none"
    auth_env: str = ""
    notes: str = ""

    def endpoint(self) -> str:
        """Return the address tools are reached at."""
        return self.url or self.base_url or self.spec_url

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "consumers": list(self.consumers),
            "capabilities": list(self.capabilities),
            "base_url": self.base_url,
            "spec_url": self.spec_url,
            "url": self.url,
            "transport": self.transport,
            "auth": {"type": self.auth_type, "env": self.auth_env},
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Registry:
    """A validated registry plus the file it came from."""

    path: str = ""
    tools: tuple[Tool, ...] = ()
    warnings: tuple[str, ...] = ()

    def for_consumer(self, consumer: str) -> tuple[Tool, ...]:
        """Return the tools one service is allowed to call."""
        name = str(consumer or "").strip().lower()
        return tuple(tool for tool in self.tools if name in tool.consumers)

    def find(self, tool_id: str) -> Tool | None:
        wanted = str(tool_id or "").strip().lower()
        for tool in self.tools:
            if tool.id.lower() == wanted:
                return tool
        return None

    def missing_credentials(
        self, env: Mapping[str, str] | None = None
    ) -> list[tuple[str, str]]:
        """Return ``(tool id, env name)`` for unset credentials."""
        source = env if env is not None else {}
        missing: list[tuple[str, str]] = []
        for tool in self.tools:
            if tool.auth_type == "none" or not tool.auth_env:
                continue
            if not str(source.get(tool.auth_env) or "").strip():
                missing.append((tool.id, tool.auth_env))
        return missing


def resolve_placeholders(value: str, env: Mapping[str, str] | None = None) -> str:
    """Substitute ``${NAME}`` placeholders from the environment."""
    source = env if env is not None else {}

    def replace(match: re.Match) -> str:
        return str(source.get(match.group(1)) or "")

    return _PLACEHOLDER.sub(replace, str(value or ""))


def _text(entry: dict, key: str) -> str:
    return str(entry.get(key) or "").strip()


def _text_tuple(entry: dict, key: str) -> tuple[str, ...]:
    raw = entry.get(key) or []
    if not isinstance(raw, list):
        raise ToolRegistryError(f"tool {entry.get('id')!r}: {key} must be a list")
    return tuple(str(item).strip() for item in raw if str(item).strip())


def parse_registry(payload: dict, *, path: str = "") -> Registry:
    """Validate one decoded registry document."""
    if not isinstance(payload, dict):
        raise ToolRegistryError("the registry must be a JSON object")
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ToolRegistryError(
            f"schema_version must be {SCHEMA_VERSION}, got {version!r}"
        )
    entries = payload.get("tools")
    if not isinstance(entries, list):
        raise ToolRegistryError("tools must be a list")
    tools: list[Tool] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ToolRegistryError("every tool entry must be an object")
        tool_id = _text(entry, "id")
        if not tool_id:
            raise ToolRegistryError("every tool needs an id")
        if tool_id in seen:
            raise ToolRegistryError(f"duplicate tool id {tool_id!r}")
        seen.add(tool_id)
        kind = _text(entry, "kind").lower()
        if kind not in KINDS:
            raise ToolRegistryError(
                f"tool {tool_id!r}: kind must be one of {', '.join(KINDS)}"
            )
        consumers = _text_tuple(entry, "consumers")
        for consumer in consumers:
            if consumer not in CONSUMERS:
                warnings.append(
                    f"tool {tool_id!r}: consumer {consumer!r} is not a known service"
                )
        transport = _text(entry, "transport").lower()
        if transport and transport not in TRANSPORTS:
            raise ToolRegistryError(
                f"tool {tool_id!r}: transport must be one of {', '.join(TRANSPORTS)}"
            )
        base_url = _text(entry, "base_url")
        spec_url = _text(entry, "spec_url")
        url = _text(entry, "url")
        if kind == "mcp":
            if not url:
                raise ToolRegistryError(f"tool {tool_id!r}: an MCP tool needs url")
        elif kind == "openapi":
            if not (base_url or spec_url):
                raise ToolRegistryError(
                    f"tool {tool_id!r}: an OpenAPI tool needs base_url or spec_url"
                )
        elif not (base_url or url):
            raise ToolRegistryError(f"tool {tool_id!r}: an HTTP tool needs base_url")
        auth = entry.get("auth") or {}
        if not isinstance(auth, dict):
            raise ToolRegistryError(f"tool {tool_id!r}: auth must be an object")
        auth_type = str(auth.get("type") or "none").strip().lower()
        auth_env = str(auth.get("env") or "").strip()
        if auth_type not in {"none", "bearer", "header", "query"}:
            raise ToolRegistryError(
                f"tool {tool_id!r}: auth.type must be none, bearer, header, or query"
            )
        if auth_type != "none" and not auth_env:
            raise ToolRegistryError(
                f"tool {tool_id!r}: auth type {auth_type!r} needs an env name"
            )
        if auth_type == "none" and auth_env:
            warnings.append(
                f"tool {tool_id!r}: auth env {auth_env!r} is ignored while auth.type is none"
            )
        tools.append(
            Tool(
                id=tool_id,
                title=_text(entry, "title") or tool_id,
                kind=kind,
                consumers=consumers,
                capabilities=_text_tuple(entry, "capabilities"),
                base_url=base_url,
                spec_url=spec_url,
                url=url,
                transport=transport,
                auth_type=auth_type,
                auth_env=auth_env,
                notes=_text(entry, "notes"),
            )
        )
    return Registry(path=path, tools=tuple(tools), warnings=tuple(warnings))


def load_registry(
    path: str | Path, env: Mapping[str, str] | None = None
) -> Registry:
    """Load and validate one registry file."""
    location = Path(path)
    try:
        raw = location.read_text(encoding="utf-8")
    except OSError as error:
        raise ToolRegistryError(f"{location} could not be read: {error}") from error
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise ToolRegistryError(f"{location} is not valid JSON: {error}") from error
    registry = parse_registry(payload, path=str(location))
    return apply_env(registry, env)


def apply_env(registry: Registry, env: Mapping[str, str] | None = None) -> Registry:
    """Return the registry with ``${NAME}`` placeholders resolved."""
    source = env if env is not None else {}

    def mapped(tool: Tool) -> Tool:
        return Tool(
            id=tool.id,
            title=tool.title,
            kind=tool.kind,
            consumers=tool.consumers,
            capabilities=tool.capabilities,
            base_url=resolve_placeholders(tool.base_url, source),
            spec_url=resolve_placeholders(tool.spec_url, source),
            url=resolve_placeholders(tool.url, source),
            transport=tool.transport,
            auth_type=tool.auth_type,
            auth_env=tool.auth_env,
            notes=tool.notes,
        )

    return Registry(
        path=registry.path,
        tools=tuple(mapped(tool) for tool in registry.tools),
        warnings=registry.warnings,
    )


def describe_tools(tools: tuple[Tool, ...] | list[Tool]) -> str:
    """Render one tool list as plain text for logs and chat replies."""
    lines: list[str] = []
    for tool in tools:
        credentials = tool.auth_env or "no credential"
        lines.append(
            f"- {tool.id} [{tool.kind}] {tool.endpoint() or tool.spec_url}\n"
            f"  {tool.title}; auth: {tool.auth_type} ({credentials})"
        )
    return "\n".join(lines)
