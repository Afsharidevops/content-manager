from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient

from smart_router.control_plane import ControlPlane
from smart_router.panel_v58 import PANEL_HTML


def _settings():
    tier = lambda model: SimpleNamespace(model=model)
    return SimpleNamespace(
        hmac_secret="h" * 48,
        client_api_key="legacy-secret",
        fast=tier("fast-model"), standard=tier("standard-model"), strong=tier("strong-model"),
        upstream_base_url="http://gateway.invalid/v1", upstream_health_url="http://gateway.invalid/health",
        upstream_api_key="", mode="route", policy="heuristic", allow_tier_overrides=False,
        read_timeout_seconds=30,
    )


def _cp(tmp_path, monkeypatch):
    db = tmp_path / "control-v0.5.2.sqlite3"
    monkeypatch.setenv("SMART_ROUTER_CONTROL_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("SMART_ROUTER_ADMIN_API_KEY", "admin-test-key")
    monkeypatch.delenv("SMART_ROUTER_REDIS_URL", raising=False)
    return ControlPlane(_settings())


def _headers():
    return {"Authorization": "Bearer admin-test-key"}


def test_v058_panel_contains_visual_studios_and_execution_diagnostics():
    assert "Workflow Studio" in PANEL_HTML
    assert "Agent Studio" in PANEL_HTML
    assert "Knowledge Pipelines" in PANEL_HTML
    assert "Publish & Monitor" in PANEL_HTML
    assert "configure-execution-admin-browser" in PANEL_HTML
    assert "key has not been validated yet" in PANEL_HTML


def test_v058_panel_docs_cover_the_operator_manual_and_api_guide():
    assert "Operations Center user manual and API guide" in PANEL_HTML
    assert "__PANEL_DOCS_JSON__" not in PANEL_HTML
    for marker in (
        "Codex client configuration",
        "Claude client configuration",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "wire_api",
        "/v1/chat/completions",
        "/v1/messages",
        "/v1/content/storyboard",
        "purge=true",
        "key_in_use",
    ):
        assert marker in PANEL_HTML, marker


def test_v058_users_and_keys_offer_revoke_and_permanent_delete():
    assert "Permanently delete API key" in PANEL_HTML
    assert "onclick=\"purgeKey(" in PANEL_HTML
    assert "Delete the referencing ACL rules and budgets too?" in PANEL_HTML
    assert "Permanently delete user" in PANEL_HTML
    assert "onclick=\"purgeUser(" in PANEL_HTML
    assert "user_in_use" in PANEL_HTML


def test_knowledge_pipeline_crud_and_validation(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    graph = {
        "nodes": [
            {"id": "source", "type": "data_source", "label": "Files", "config": {"mode": "upload"}},
            {"id": "chunk", "type": "chunk", "label": "Chunk"},
            {"id": "kb", "type": "knowledge_base", "label": "Knowledge", "ref_id": 7},
        ],
        "edges": [{"from": "source", "to": "chunk"}, {"from": "chunk", "to": "kb"}],
    }
    with TestClient(cp.app) as client:
        created = client.post("/api/knowledge-pipelines", headers=_headers(), json={"name": "docs", "graph": graph})
        assert created.status_code == 200
        row = next(x for x in created.json() if x["name"] == "docs")
        assert row["graph"]["version"] == 2
        assert row["graph"]["nodes"][2]["ref_id"] == 7

        updated = client.put(f"/api/knowledge-pipelines/{row['id']}", headers=_headers(), json={"active": False, "description": "ingestion flow"})
        assert updated.status_code == 200
        rows = client.get("/api/knowledge-pipelines", headers=_headers()).json()
        saved = next(x for x in rows if x["id"] == row["id"])
        assert saved["active"] is False
        assert saved["description"] == "ingestion flow"

        bad = client.post("/api/knowledge-pipelines", headers=_headers(), json={
            "name": "unsafe", "graph": {"nodes": [{"id": "x", "type": "shell"}], "edges": []}
        })
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "invalid_knowledge_pipeline"

        deleted = client.delete(f"/api/knowledge-pipelines/{row['id']}", headers=_headers())
        assert deleted.status_code == 200
        assert all(x["id"] != row["id"] for x in client.get("/api/knowledge-pipelines", headers=_headers()).json())
