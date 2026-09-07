from __future__ import annotations

from types import SimpleNamespace

from starlette.requests import Request
from starlette.testclient import TestClient

from smart_router.control_plane import ControlPlane


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
    monkeypatch.setenv("SMART_ROUTER_RAG_MODE", "hybrid")
    monkeypatch.setenv("SMART_ROUTER_GUARDRAILS_MODE", "audit")
    monkeypatch.delenv("SMART_ROUTER_REDIS_URL", raising=False)
    return ControlPlane(_settings())


def _headers():
    return {"Authorization": "Bearer admin-test-key"}


def test_groups_and_teams_have_disable_enable_and_permanent_delete(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        group = client.post("/api/groups", headers=_headers(), json={"name": "ops", "members": []}).json()[0]
        assert client.delete(f"/api/groups/{group['id']}", headers=_headers()).status_code == 200
        assert next(x for x in client.get("/api/groups", headers=_headers()).json() if x["id"] == group["id"])["active"] is False
        assert client.put(f"/api/groups/{group['id']}", headers=_headers(), json={"active": True}).status_code == 200
        assert next(x for x in client.get("/api/groups", headers=_headers()).json() if x["id"] == group["id"])["active"] is True

        agent_rows = client.post("/api/agents", headers=_headers(), json={"name": "worker", "knowledge": [], "skills": [], "plugins": []}).json()
        agent = next(x for x in agent_rows if x["name"] == "worker")
        team_rows = client.post("/api/teams", headers=_headers(), json={"name": "ops-team", "strategy": "parallel", "agent_ids": [agent["id"]], "synthesis_tier": "strong"}).json()
        team = next(x for x in team_rows if x["name"] == "ops-team")
        assert client.delete(f"/api/teams/{team['id']}", headers=_headers()).status_code == 200
        assert next(x for x in client.get("/api/teams", headers=_headers()).json() if x["id"] == team["id"])["active"] is False
        assert client.put(f"/api/teams/{team['id']}", headers=_headers(), json={"active": True}).status_code == 200
        assert client.delete(f"/api/teams/{team['id']}?purge=true", headers=_headers()).status_code == 200
        assert all(x["id"] != team["id"] for x in client.get("/api/teams", headers=_headers()).json())
        assert client.delete(f"/api/groups/{group['id']}?purge=true", headers=_headers()).status_code == 200


def test_group_permanent_delete_protects_acl_references(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        group = client.post("/api/groups", headers=_headers(), json={"name": "network-operators", "members": []}).json()[0]
        assert client.post("/api/acls", headers=_headers(), json={
            "subject_type": "group", "subject_value": "network-operators", "resource_type": "knowledge",
            "resource_id": "1", "permission": "knowledge.read", "effect": "allow",
        }).status_code == 200
        protected = client.delete(f"/api/groups/{group['id']}?purge=true", headers=_headers())
        assert protected.status_code == 409
        assert protected.json()["error"]["code"] == "group_in_use"
        assert client.delete(f"/api/groups/{group['id']}?purge=true&cascade=true", headers=_headers()).status_code == 200


def test_platform_crud_for_pipelines_workflows_prompts_and_evaluations(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        pipeline = client.post("/api/router-pipelines", headers=_headers(), json={
            "name": "cheap-then-safe", "priority": 10,
            "definition": {"stages": [
                {"type": "condition", "when": {"profile": "coding"}},
                {"type": "route", "tier": "strong"},
                {"type": "retry", "attempts": 2},
                {"type": "fallback", "models": ["combo-standard"]},
            ]},
        })
        assert pipeline.status_code == 200
        assert any(x["name"] == "cheap-then-safe" for x in pipeline.json())

        workflow = client.post("/api/workflows", headers=_headers(), json={
            "name": "research-team", "graph": {
                "nodes": [{"id": "a", "type": "agent", "label": "Research"}, {"id": "b", "type": "agent", "label": "Review"}],
                "edges": [{"from": "a", "to": "b"}],
            },
        })
        assert workflow.status_code == 200
        assert any(x["name"] == "research-team" for x in workflow.json())

        p1 = client.post("/api/prompts", headers=_headers(), json={"name": "router-system", "content": "version one"})
        p2 = client.post("/api/prompts", headers=_headers(), json={"name": "router-system", "content": "version two"})
        assert p1.status_code == p2.status_code == 200
        versions = [x for x in p2.json() if x["name"] == "router-system"]
        assert [x["version"] for x in versions] == [2, 1]
        assert versions[0]["active"] is True and versions[1]["active"] is False
        assert client.put(f"/api/prompts/{versions[1]['id']}", headers=_headers(), json={"activate": True}).status_code == 200

        datasets = client.post("/api/datasets", headers=_headers(), json={"name": "routing-gold", "description": "golden route labels"})
        dataset = next(x for x in datasets.json() if x["name"] == "routing-gold")
        items = client.post(f"/api/datasets/{dataset['id']}/items", headers=_headers(), json={
            "input": {"messages": [{"role": "user", "content": "write python"}]}, "expected": {"tier": "strong"}
        })
        assert items.status_code == 200 and len(items.json()) == 1
        evaluation = client.post("/api/evaluations", headers=_headers(), json={
            "dataset_id": dataset["id"], "name": "heuristic-v-calibrated", "variant_a": "heuristic", "variant_b": "calibrated"
        })
        assert evaluation.status_code == 200
        assert evaluation.json()[0]["status"] == "draft"


def test_guardrail_and_trace_surfaces(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        created = client.post("/api/guardrails", headers=_headers(), json={
            "name": "block-internal-token", "category": "content", "action": "block", "pattern": "INTERNAL_TOKEN"
        })
        assert created.status_code == 200
        assert created.json()["status"]["mode"] == "audit"
        assert any(x["name"] == "block-internal-token" for x in created.json()["rules"])

        request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": []})
        request.state.v51_request_id = "trace-test-1"
        request.state.v56_trace_seq = 0
        cp.trace(request, "request", detail={"model": "auto", "authorization": "secret", "prompt": "do not store me"})
        traces = client.get("/api/traces", headers=_headers()).json()
        row = next(x for x in traces if x["request_id"] == "trace-test-1")
        assert row["stage"] == "request"
        # Sensitive/raw prompt-like keys are omitted/redacted by trace serialization.
        assert "secret" not in str(row["detail"])
        detail = client.get("/api/traces/trace-test-1", headers=_headers()).json()
        assert len(detail) == 1


def test_onboarding_identity_and_hybrid_rag_status(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        onboarding = client.get("/api/onboarding", headers=_headers())
        assert onboarding.status_code == 200
        assert "discover_models" in onboarding.json()["steps"]
        assert client.put("/api/onboarding", headers=_headers(), json={"complete": True, "checklist": {"upstream": True}}).status_code == 200
        assert client.get("/api/onboarding", headers=_headers()).json()["complete"] is True
        identity = client.get("/api/identity", headers=_headers()).json()
        assert identity["ldap"]["status"] in {"not_configured", "connector_foundation"}
        system = client.get("/api/system", headers=_headers()).json()
        assert system["knowledge_retrieval"] == "hybrid"
        assert system["knowledge_vector"]["mode"] == "hybrid"
        assert system["guardrails"]["mode"] == "audit"
