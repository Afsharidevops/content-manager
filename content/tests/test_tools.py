"""Tool registry loader tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from content_pipeline.tools import (
    SCHEMA_VERSION,
    ToolRegistryError,
    apply_env,
    describe_tools,
    load_registry,
    parse_registry,
)

ROOT = Path(__file__).resolve().parents[2]
SHIPPED = ROOT / "content" / "config" / "tools.json"


def _document(tools: list[dict] | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tools": tools
        if tools is not None
        else [
            {
                "id": "media-studio",
                "title": "Media Studio",
                "kind": "openapi",
                "base_url": "http://media-studio:8850",
                "spec_url": "http://media-studio:8850/openapi.json",
                "auth": {"type": "bearer", "env": "MEDIA_STUDIO_API_TOKEN"},
                "capabilities": ["media.image"],
                "consumers": ["bot", "n8n"],
                "notes": "media jobs",
            }
        ],
    }


class RegistryDocumentTests(unittest.TestCase):
    def test_shipped_registry_is_valid(self):
        registry = load_registry(SHIPPED)
        ids = [tool.id for tool in registry.tools]
        self.assertIn("media-studio", ids)
        self.assertIn("bot", registry.for_consumer("bot")[0].consumers)
        self.assertEqual(registry.warnings, ())

    def test_consumer_filter_and_lookup(self):
        registry = parse_registry(_document())
        self.assertEqual([tool.id for tool in registry.for_consumer("bot")], ["media-studio"])
        self.assertEqual(registry.for_consumer("router"), ())
        self.assertIsNotNone(registry.find("MEDIA-STUDIO"))
        self.assertIsNone(registry.find("nope"))

    def test_rejects_wrong_schema_version(self):
        with self.assertRaises(ToolRegistryError):
            parse_registry({"schema_version": 99, "tools": []})

    def test_rejects_duplicate_ids(self):
        entry = _document()["tools"][0]
        with self.assertRaises(ToolRegistryError) as caught:
            parse_registry(_document([entry, dict(entry)]))
        self.assertIn("duplicate", str(caught.exception))

    def test_rejects_unknown_kind(self):
        entry = dict(_document()["tools"][0], kind="grpc")
        with self.assertRaises(ToolRegistryError):
            parse_registry(_document([entry]))

    def test_requires_an_address_per_kind(self):
        with self.assertRaises(ToolRegistryError):
            parse_registry(_document([{"id": "x", "kind": "mcp"}]))
        with self.assertRaises(ToolRegistryError):
            parse_registry(_document([{"id": "x", "kind": "openapi"}]))

    def test_requires_an_env_name_when_auth_is_not_none(self):
        entry = dict(_document()["tools"][0], auth={"type": "bearer"})
        with self.assertRaises(ToolRegistryError):
            parse_registry(_document([entry]))

    def test_warns_about_unknown_consumers(self):
        entry = dict(_document()["tools"][0], consumers=["bot", "spaceship"])
        registry = parse_registry(_document([entry]))
        self.assertTrue(any("spaceship" in warning for warning in registry.warnings))

    def test_rejects_invalid_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tools.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ToolRegistryError):
                load_registry(path)

    def test_missing_file_is_reported(self):
        with self.assertRaises(ToolRegistryError):
            load_registry("/nonexistent/tools.json")

    def test_placeholders_resolve_from_the_environment(self):
        entry = dict(
            _document()["tools"][0],
            base_url="${MEDIA_HOST}",
            spec_url="${MEDIA_HOST}/openapi.json",
        )
        registry = parse_registry(_document([entry]))
        resolved = apply_env(registry, {"MEDIA_HOST": "http://media.example:8850"})
        self.assertEqual(resolved.find("media-studio").spec_url, "http://media.example:8850/openapi.json")
        blanked = apply_env(registry, {})
        self.assertEqual(blanked.find("media-studio").base_url, "")

    def test_missing_credentials_skips_keyless_tools(self):
        registry = parse_registry(
            _document(
                [
                    _document()["tools"][0],
                    {
                        "id": "web-search",
                        "kind": "http",
                        "base_url": "https://example.test/search",
                        "auth": {"type": "none"},
                        "consumers": ["bot"],
                    },
                ]
            )
        )
        missing = registry.missing_credentials({})
        self.assertEqual(missing, [("media-studio", "MEDIA_STUDIO_API_TOKEN")])
        self.assertEqual(
            registry.missing_credentials({"MEDIA_STUDIO_API_TOKEN": "token"}), []
        )

    def test_describe_tools_lists_ids_and_credentials(self):
        registry = parse_registry(_document())
        text = describe_tools(registry.tools)
        self.assertIn("media-studio", text)
        self.assertIn("MEDIA_STUDIO_API_TOKEN", text)


if __name__ == "__main__":
    unittest.main()
