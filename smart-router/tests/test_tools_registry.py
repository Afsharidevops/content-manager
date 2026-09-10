from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_router.tools_registry import (
    SCHEMA_VERSION,
    ToolsRegistryError,
    load_tools,
)


def _document(tools: list[dict]) -> dict:
    return {"schema_version": SCHEMA_VERSION, "tools": tools}


def _entry(**overrides) -> dict:
    entry = {
        "id": "media-studio",
        "title": "Media Studio",
        "kind": "openapi",
        "base_url": "http://media-studio:8850",
        "spec_url": "http://media-studio:8850/openapi.json",
        "auth": {"type": "bearer", "env": "MEDIA_STUDIO_API_TOKEN"},
        "consumers": ["bot", "router"],
        "capabilities": ["media.image"],
    }
    entry.update(overrides)
    return entry


def _write(tmp_path: Path, document: dict) -> Path:
    path = tmp_path / "tools.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_missing_path_returns_no_tools():
    assert load_tools("") == ()
    assert load_tools("/nonexistent/tools.json") == ()


def test_only_enabled_consumers_are_returned(tmp_path):
    path = _write(
        tmp_path,
        _document(
            [
                _entry(),
                _entry(id="n8n-only", consumers=["n8n"], kind="mcp", url="http://n8n/mcp"),
            ]
        ),
    )
    tools = load_tools(path)
    assert [tool.id for tool in tools] == ["media-studio"]
    assert tools[0].endpoint == "http://media-studio:8850"
    assert tools[0].capabilities == ("media.image",)


def test_placeholders_resolve_from_the_environment(tmp_path):
    path = _write(
        tmp_path,
        _document([_entry(base_url="${MEDIA_HOST}", spec_url="${MEDIA_HOST}/openapi.json")]),
    )
    tools = load_tools(path, {"MEDIA_HOST": "http://media.internal:8850"})
    assert tools[0].spec_url == "http://media.internal:8850/openapi.json"


def test_invalid_document_raises(tmp_path):
    path = tmp_path / "tools.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ToolsRegistryError):
        load_tools(path)
    wrong_version = _write(tmp_path, {"schema_version": 99, "tools": []})
    with pytest.raises(ToolsRegistryError):
        load_tools(wrong_version)
