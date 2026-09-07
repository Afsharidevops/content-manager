from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient

from smart_router.control_db import AccessGroup, ControlDB, Skill, User
from smart_router.control_plane import ControlPlane
from smart_router.security_v51 import Identity


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


def test_control_schema_upgrades_in_place(tmp_path):
    db = ControlDB(f"sqlite:///{tmp_path/'control-v0.5.2.sqlite3'}")
    assert db.schema_version() == "0.5.9"
    # v0.5.5, v0.5.8, and v0.5.9 tables are created without changing the compatibility filename.
    tables = set(db.engine.dialect.get_table_names(db.engine.connect()))
    assert {"v55_runtime_settings", "v55_access_groups", "v55_skills", "v55_agent_skills", "v56_request_traces", "v56_knowledge_embeddings", "v58_knowledge_pipelines"} <= tables


def test_runtime_mode_policy_persist_and_reset(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        r = client.put("/api/system", headers=_headers(), json={"router_mode": "observe", "router_policy": "calibrated"})
        assert r.status_code == 200
        body = r.json()
        assert body["router_mode"] == "observe"
        assert body["router_policy"] == "calibrated"
        assert body["config_source"]["router_mode"] == "operations_db"

        # Re-instantiation uses the same DB overrides.
        cp2 = ControlPlane(_settings())
        assert cp2.settings.mode == "observe"
        assert cp2.settings.policy == "calibrated"

        rr = client.delete("/api/system", headers=_headers())
        assert rr.status_code == 200
        assert rr.json()["router_mode"] == "route"
        assert rr.json()["router_policy"] == "heuristic"


def test_ha_enable_requires_redis(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        r = client.put("/api/system", headers=_headers(), json={"ha_mode": True})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "ha_requires_redis"


def test_agent_create_validates_knowledge_and_supports_edit_delete(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        bad = client.post("/api/agents", headers=_headers(), json={"name": "Selen", "tier": "auto", "profile": "auto", "knowledge": [1]})
        assert bad.status_code == 422
        assert bad.json()["error"]["code"] == "invalid_agent_knowledge"

        created = client.post("/api/agents", headers=_headers(), json={"name": "Selen", "tier": "auto", "profile": "auto", "knowledge": []})
        assert created.status_code == 200
        agent = next(x for x in created.json() if x["name"] == "Selen")

        updated = client.put(f"/api/agents/{agent['id']}", headers=_headers(), json={"description": "network assistant", "skills": []})
        assert updated.status_code == 200
        rows = client.get("/api/agents", headers=_headers()).json()
        assert next(x for x in rows if x["id"] == agent["id"])["description"] == "network assistant"

        deleted = client.delete(f"/api/agents/{agent['id']}?purge=true", headers=_headers())
        assert deleted.status_code == 200
        assert all(x["id"] != agent["id"] for x in client.get("/api/agents", headers=_headers()).json())


def test_group_subject_acl_matches_group_member(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with cp.db.session() as session:
        session.add(User(username="alice", password_hash="unused", role="user", team="default"))
        session.commit()
    with TestClient(cp.app) as client:
        group = client.post("/api/groups", headers=_headers(), json={"name": "network-operators", "members": ["alice"]})
        assert group.status_code == 200
        rule = client.post("/api/acls", headers=_headers(), json={
            "subject_type": "group", "subject_value": "network-operators", "resource_type": "knowledge",
            "resource_id": "1", "permission": "knowledge.read", "effect": "deny",
        })
        assert rule.status_code == 200
    assert not cp.acl.allowed(Identity("alice", "user"), "knowledge", 1, "knowledge.read")


def test_skill_catalog_install_and_agent_assignment(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        catalog = client.get("/api/skills/catalog", headers=_headers())
        assert catalog.status_code == 200
        assert any(x["catalog_id"] == "linux-operations" for x in catalog.json())
        install = client.post("/api/skills/install", headers=_headers(), json={"catalog_id": "linux-operations"})
        assert install.status_code == 200
        sid = install.json()["id"]
        created = client.post("/api/agents", headers=_headers(), json={"name": "ops", "skills": [sid], "knowledge": [], "plugins": []})
        assert created.status_code == 200
        agent = next(x for x in created.json() if x["name"] == "ops")
        assert agent["skills"] == [sid]


def test_direct_create_endpoints_do_not_detach_rows_before_audit(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        user = client.post(
            "/api/users",
            headers=_headers(),
            json={"username": "audit-user", "password": "long-enough-password", "role": "user"},
        )
        assert user.status_code == 200
        assert any(x["username"] == "audit-user" for x in user.json())

        policy = client.post(
            "/api/policies",
            headers=_headers(),
            json={"name": "audit-policy", "rule": {}, "action": {}},
        )
        assert policy.status_code == 200
        assert any(x["name"] == "audit-policy" for x in policy.json())

        plugin = client.post(
            "/api/plugins",
            headers=_headers(),
            json={"name": "audit-plugin", "kind": "mcp", "enabled": False},
        )
        assert plugin.status_code == 200
        assert any(x["name"] == "audit-plugin" for x in plugin.json())


def test_plugin_catalog_install_is_registry_only(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        r = client.post("/api/plugins/install", headers=_headers(), json={"catalog_id": "github-mcp"})
        assert r.status_code == 200
        plugins = client.get("/api/plugins", headers=_headers()).json()
        plugin = next(x for x in plugins if x["name"] == "github-mcp")
        assert plugin["enabled"] is False
        assert plugin["endpoint"] == ""
