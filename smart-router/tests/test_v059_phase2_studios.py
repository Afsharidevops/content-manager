from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from smart_router.control_db import Agent, AgentGraph, AgentSkillLink, KnowledgeBase, Plugin, Skill
from smart_router.control_plane import ControlPlane
from smart_router.graph_v59 import normalize_graph, port_definitions, router_graph_plan
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


def test_agent_and_router_studios_expose_shared_typed_ports():
    assert port_definitions("agent", "agent")["inputs"] == [
        {"id": "default", "type": "flow"},
        {"id": "context", "type": "knowledge"},
        {"id": "tools", "type": "tool"},
    ]
    assert port_definitions("router", "route")["outputs"] == [{"id": "default", "type": "route_flow"}]


def test_router_graph_supports_named_branch_dag():
    graph = normalize_graph(
        {
            "nodes": [
                {"id": "health", "type": "health_filter"},
                {"id": "route", "type": "route"},
                {"id": "fallback", "type": "fallback"},
            ],
            "edges": [
                {"source_node": "health", "source_port": "healthy", "target_node": "route", "target_port": "default"},
                {"source_node": "health", "source_port": "unhealthy", "target_node": "fallback", "target_port": "default"},
            ],
        },
        studio="router",
        max_nodes=100,
        max_edges=200,
    )
    plan = router_graph_plan(graph)
    assert plan["entry"] == "health"
    assert plan["transitions"]["health"] == {"healthy": "route", "unhealthy": "fallback"}

    duplicate_port = normalize_graph(
        {
            "nodes": [
                {"id": "health", "type": "health_filter"},
                {"id": "route", "type": "route"},
                {"id": "fallback", "type": "fallback"},
            ],
            "edges": [
                {"source_node": "health", "source_port": "healthy", "target_node": "route", "target_port": "default"},
                {"source_node": "health", "source_port": "healthy", "target_node": "fallback", "target_port": "default"},
            ],
        },
        studio="router",
        max_nodes=100,
        max_edges=200,
    )
    with pytest.raises(ValueError, match="output port"):
        router_graph_plan(duplicate_port)


def test_agent_studio_graph_is_backward_synthesized_and_becomes_authoritative(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with cp.db.session() as session:
        kb = KnowledgeBase(name="Ops KB", description="")
        plugin = Plugin(name="observer", kind="mcp", description="", endpoint="", manifest_json="{}", risk="medium", enabled=True)
        skill = Skill(name="Linux Ops", description="", category="ops", source="manual", commercial=False, license_note="", instructions="", manifest_json="{}", enabled=True)
        session.add_all([kb, plugin, skill])
        session.commit()
        session.refresh(kb); session.refresh(plugin); session.refresh(skill)
        agent = Agent(
            name="Selen", description="", system_prompt="", tier="auto", profile="auto",
            knowledge_json=json.dumps([kb.id]), plugins_json=json.dumps([plugin.id]), permissions_json="[]", active=True,
        )
        session.add(agent); session.commit(); session.refresh(agent)
        session.add(AgentSkillLink(agent_id=agent.id, skill_id=skill.id)); session.commit()
        aid, kb_id, plugin_id, skill_id = agent.id, kb.id, plugin.id, skill.id

    with TestClient(cp.app) as client:
        rows = client.get("/api/agents", headers=_headers()).json()
        row = next(x for x in rows if x["id"] == aid)
        graph = row["graph"]
        assert graph["version"] == 2
        assert {n["type"] for n in graph["nodes"]} >= {"input", "agent", "output", "knowledge", "plugin", "skill"}
        assert any(e["source_port"] == "context" and e["target_port"] == "context" for e in graph["edges"])
        assert any(e["source_port"] == "tools" and e["target_port"] == "tools" for e in graph["edges"])

        # Remove the plugin node/edge. The graph is the authoritative attachment set when saved from Agent Studio.
        graph["nodes"] = [n for n in graph["nodes"] if not (n["type"] == "plugin" and n["ref_id"] == plugin_id)]
        graph["edges"] = [e for e in graph["edges"] if not str(e["source_node"]).startswith("plugin-")]
        saved = client.put(f"/api/agents/{aid}", headers=_headers(), json={"graph": graph})
        assert saved.status_code == 200, saved.text

        row = next(x for x in client.get("/api/agents", headers=_headers()).json() if x["id"] == aid)
        assert row["knowledge"] == [kb_id]
        assert row["plugins"] == []
        assert row["skills"] == [skill_id]
        assert all(n["type"] != "plugin" for n in row["graph"]["nodes"])

    with cp.db.session() as session:
        persisted = session.get(AgentGraph, aid)
        assert persisted is not None
        assert json.loads(persisted.graph_json)["version"] == 2


def test_agent_studio_rejects_missing_core_edge_and_duplicate_resource(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with cp.db.session() as session:
        kb = KnowledgeBase(name="KB", description="")
        session.add(kb); session.commit(); session.refresh(kb)
        agent = Agent(name="A", description="", system_prompt="", tier="auto", profile="auto", knowledge_json="[]", plugins_json="[]", permissions_json="[]", active=True)
        session.add(agent); session.commit(); session.refresh(agent)
        aid, kb_id = agent.id, kb.id

    graph = {
        "nodes": [
            {"id": "input", "type": "input"},
            {"id": "agent", "type": "agent", "ref_id": aid},
            {"id": "output", "type": "output"},
            {"id": "kb1", "type": "knowledge", "ref_id": kb_id},
            {"id": "kb2", "type": "knowledge", "ref_id": kb_id},
        ],
        "edges": [
            {"source_node": "input", "source_port": "default", "target_node": "agent", "target_port": "default"},
            {"source_node": "agent", "source_port": "result", "target_node": "output", "target_port": "answer"},
            {"source_node": "kb1", "source_port": "context", "target_node": "agent", "target_port": "context"},
            {"source_node": "kb2", "source_port": "context", "target_node": "agent", "target_port": "context"},
        ],
    }
    with TestClient(cp.app) as client:
        bad = client.put(f"/api/agents/{aid}", headers=_headers(), json={"graph": graph})
        assert bad.status_code == 422
        assert "duplicate knowledge" in bad.json()["error"]["message"]

        graph["nodes"] = [n for n in graph["nodes"] if n["id"] != "kb2"]
        graph["edges"] = [e for e in graph["edges"] if e["source_node"] not in {"kb2", "input"}]
        bad = client.put(f"/api/agents/{aid}", headers=_headers(), json={"graph": graph})
        assert bad.status_code == 422
        assert "Input to Agent" in bad.json()["error"]["message"]


def test_router_pipeline_graph_derives_runtime_stage_order(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    graph = {
        "nodes": [
            {"id": "health", "type": "health_filter", "config": {"ui": {"x": 20, "y": 20}}},
            {"id": "retry", "type": "retry", "config": {"retries": 3, "ui": {"x": 400, "y": 20}}},
            {"id": "route", "type": "route", "config": {"profile": "coding", "ui": {"x": 200, "y": 20}}},
        ],
        "edges": [
            {"from": "health", "to": "route"},
            {"from": "route", "to": "retry"},
        ],
    }
    with TestClient(cp.app) as client:
        created = client.post(
            "/api/router-pipelines",
            headers=_headers(),
            json={"name": "visual-order", "priority": 50, "definition": {"description": "phase2", "graph": graph}},
        )
        assert created.status_code == 200, created.text
        row = next(x for x in created.json() if x["name"] == "visual-order")
        assert row["definition"]["version"] == 3
        assert [x["id"] for x in row["definition"]["stages"]] == ["health", "route", "retry"]
        assert row["definition"]["stages"][1]["profile"] == "coding"
        assert row["definition"]["stages"][2]["retries"] == 3
        assert "ui" not in row["definition"]["stages"][0]
        assert row["definition"]["graph"]["version"] == 2

        branch_graph = {
            "nodes": [
                {"id": "health", "type": "health_filter"},
                {"id": "route", "type": "route", "config": {"profile": "coding"}},
                {"id": "fallback", "type": "fallback", "config": {"fallback": ["strong"]}},
            ],
            "edges": [
                {"source_node": "health", "source_port": "healthy", "target_node": "route", "target_port": "default"},
                {"source_node": "health", "source_port": "unhealthy", "target_node": "fallback", "target_port": "default"},
            ],
        }
        branched = client.post(
            "/api/router-pipelines",
            headers=_headers(),
            json={"name": "health-branch", "definition": {"graph": branch_graph}},
        )
        assert branched.status_code == 200, branched.text
        row = next(x for x in branched.json() if x["name"] == "health-branch")
        assert row["definition"]["version"] == 3
        assert row["definition"]["entry"] == "health"
        assert row["definition"]["transitions"]["health"] == {"healthy": "route", "unhealthy": "fallback"}


def test_phase2_panel_uses_shared_graph_canvas_for_agent_and_router():
    for marker in (
        "graphStudioShell(a.name,AGENT_TYPES)",
        "graphNormalizeClientGraph(graph,'agent')",
        "routerGraphFromStages",
        "graphStudioShell(graphStudio.name,STAGE_TYPES)",
        "named classifier, condition, health, capability, and approval outputs",
        "actual resource attachment set",
    ):
        assert marker in PANEL_HTML


def test_router_condition_named_branch_controls_runtime_path(tmp_path, monkeypatch):
    from starlette.requests import Request

    cp = _cp(tmp_path, monkeypatch)
    graph = {
        "nodes": [
            {"id": "condition", "type": "condition", "config": {"when": {"prompt_contains": "code"}}},
            {"id": "coding-retry", "type": "retry", "config": {"retries": 2}},
            {"id": "default-retry", "type": "retry", "config": {"retries": 4}},
        ],
        "edges": [
            {"source_node": "condition", "source_port": "true", "target_node": "coding-retry", "target_port": "default"},
            {"source_node": "condition", "source_port": "false", "target_node": "default-retry", "target_port": "default"},
        ],
    }
    with TestClient(cp.app) as client:
        created = client.post(
            "/api/router-pipelines",
            headers=_headers(),
            json={"name": "runtime-branch", "priority": 1, "definition": {"graph": graph}},
        )
        assert created.status_code == 200, created.text

    identity = SimpleNamespace(role="operator", team="")
    request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": []})
    result = cp._apply_router_pipelines(
        request,
        {"messages": [{"role": "user", "content": "please debug this code"}]},
        identity,
        "standard",
        "standard",
        "model-a",
    )
    assert result[2] == 2

    request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": []})
    result = cp._apply_router_pipelines(
        request,
        {"messages": [{"role": "user", "content": "write a short greeting"}]},
        identity,
        "standard",
        "standard",
        "model-a",
    )
    assert result[2] == 4
