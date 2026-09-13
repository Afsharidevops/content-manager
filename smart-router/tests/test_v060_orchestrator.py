from __future__ import annotations

import json
from types import SimpleNamespace

from starlette.testclient import TestClient

from smart_router.control_db import Agent, ControlDB
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
    monkeypatch.delenv("SMART_ROUTER_REDIS_URL", raising=False)
    monkeypatch.delenv("SMART_ROUTER_ORCHESTRATOR_APPROVAL_MODE", raising=False)
    return ControlPlane(_settings())


def _headers():
    return {"Authorization": "Bearer admin-test-key"}


def _add_agent(cp, name, description="", plugins=None):
    with cp.db.session() as session:
        agent = Agent(
            name=name,
            description=description,
            system_prompt=f"You are {name}.",
            tier="auto",
            profile="auto",
            knowledge_json="[]",
            plugins_json=json.dumps(plugins or []),
            permissions_json="[]",
            active=True,
        )
        session.add(agent)
        session.commit()
        session.refresh(agent)
        return agent.id


def _chat_stub(plan, review=None, prompts=None):
    async def fake(body, profile="auto"):
        messages = body.get("messages") or []
        system = messages[0]["content"] if messages else ""
        if "Planner Agent" in system:
            if prompts is not None:
                prompts.append(messages[-1]["content"])
            return {"choices": [{"message": {"content": json.dumps(plan)}}]}
        if "Reviewer Agent" in system:
            return {"choices": [{"message": {"content": json.dumps(review or {"status": "success", "summary": "verified", "findings": [], "rollback_suggestion": ""})}}]}
        return {"choices": [{"message": {"content": "unexpected"}}]}
    return fake


def _executor(results, calls):
    async def fake(agent_id, task, messages):
        calls.append({"agent_id": agent_id, "task": task})
        outcome = results.pop(0) if results else {"choices": [{"message": {"content": f"done by {agent_id}"}}]}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    return fake


def test_plan_preview_resolves_agent_names_and_rejects_unknown(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    devops = _add_agent(cp, "devops-agent")
    security = _add_agent(cp, "security-agent")
    cp._local_chat = _chat_stub(
        {
            "goal": "harden the host",
            "steps": [
                {"title": "Audit ssh", "agent": "Security-Agent", "action": "List weak ssh settings"},
                {"title": "Patch", "agent": "devops agent", "action": "Apply the fix"},
            ],
        }
    )
    with TestClient(cp.app) as client:
        ok = client.post("/api/orchestrations/plan", headers=_headers(), json={"task": "harden the host"})
        assert ok.status_code == 200
        body = ok.json()
        assert body["goal"] == "harden the host"
        assert [step["agent_id"] for step in body["steps"]] == [security, devops]
        assert body["steps"][0]["agent_name"] == "security-agent"
        assert body["planner"]["fallback"] is False

        bad = client.post(
            "/api/orchestrations/plan",
            headers=_headers(),
            json={"task": "harden the host", "agent_ids": [devops]},
        )
    assert bad.status_code == 422
    error = bad.json()["error"]
    assert error["code"] == "invalid_plan_agent"
    assert error["details"]["available"] == ["devops-agent"]


def test_planner_fallback_plan_keeps_the_run_usable(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "solo-agent")
    cp._local_chat = _chat_stub({})  # not a plan: no goal and no steps

    async def broken(body, profile="auto"):
        return {"choices": [{"message": {"content": "I could not build a plan."}}]}

    cp._local_chat = broken
    with TestClient(cp.app) as client:
        r = client.post("/api/orchestrations", headers=_headers(), json={"task": "restart the worker", "auto_execute": False})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "planned"
    assert len(body["steps"]) == 1
    assert body["plan"]["steps"][0]["agent_name"] == "solo-agent"
    assert any("fallback" in note for note in body["plan"]["notes"])


def test_run_executes_steps_in_order_and_reviews(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    alpha = _add_agent(cp, "alpha-agent")
    beta = _add_agent(cp, "beta-agent")
    cp._local_chat = _chat_stub(
        {
            "goal": "ship the change",
            "steps": [
                {"title": "Build", "agent": "alpha-agent", "action": "Build the artifact"},
                {"title": "Deploy", "agent": "beta-agent", "action": "Deploy the artifact"},
            ],
        },
        review={"status": "success", "summary": "both steps verified", "findings": ["artifact exists"], "rollback_suggestion": ""},
    )
    calls: list[dict] = []
    cp._run_agent = _executor([], calls)
    with TestClient(cp.app) as client:
        r = client.post("/api/orchestrations", headers=_headers(), json={"task": "ship the change"})
        assert r.status_code == 201
        run = r.json()
        detail = client.get(f"/api/orchestrations/{run['id']}", headers=_headers()).json()
    assert run["status"] == "completed"
    assert [step["status"] for step in run["steps"]] == ["done", "done"]
    assert [call["agent_id"] for call in calls] == [alpha, beta]
    assert "done by 1" in calls[1]["task"]  # previous result travels to the next agent
    assert detail["result"]["review"]["status"] == "success"
    assert detail["progress"] == {"total": 2, "done": 2, "awaiting": None}
    with cp.db.session() as session:
        actions = [row.action for row in session.scalars(__import__("sqlalchemy").select(__import__("smart_router.control_db", fromlist=["AuditEvent"]).AuditEvent))]
    assert "orchestration.create" in actions


def test_dangerous_step_pauses_for_approval_then_resumes(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    _add_agent(cp, "beta-agent")
    cp._local_chat = _chat_stub(
        {
            "goal": "clean the cluster",
            "steps": [
                {"title": "Inspect", "agent": "alpha-agent", "action": "Read the pod state"},
                {"title": "Remove pod", "agent": "beta-agent", "action": "kubectl delete pod api-0 -n prod"},
            ],
        }
    )
    calls: list[dict] = []
    cp._run_agent = _executor([], calls)
    with TestClient(cp.app) as client:
        r = client.post("/api/orchestrations", headers=_headers(), json={"task": "clean the cluster"})
        assert r.status_code == 201
        run = r.json()
        assert run["status"] == "awaiting_approval"
        assert [step["status"] for step in run["steps"]] == ["done", "awaiting_approval"]
        assert "kubectl delete" in run["steps"][1]["approval_reason"]
        assert len(calls) == 1

        approved = client.post(f"/api/orchestrations/{run['id']}/approve", headers=_headers())
        assert approved.status_code == 200
        resumed = approved.json()
    assert resumed["status"] == "completed"
    assert [step["status"] for step in resumed["steps"]] == ["done", "done"]
    assert len(calls) == 2


def test_reject_stops_the_run_without_executing_the_step(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    cp._local_chat = _chat_stub(
        {
            "goal": "drop the table",
            "steps": [{"title": "Drop", "agent": "alpha-agent", "action": "DROP TABLE sessions"}],
        }
    )
    calls: list[dict] = []
    cp._run_agent = _executor([], calls)
    with TestClient(cp.app) as client:
        run = client.post("/api/orchestrations", headers=_headers(), json={"task": "drop the table"}).json()
        assert run["status"] == "awaiting_approval"
        rejected = client.post(f"/api/orchestrations/{run['id']}/reject", headers=_headers()).json()
    assert rejected["status"] == "rejected"
    assert rejected["steps"][0]["status"] == "rejected"
    assert calls == []


def test_approval_mode_always_gates_every_step(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    cp._local_chat = _chat_stub({"goal": "read state", "steps": [{"title": "Read", "agent": "alpha-agent", "action": "Read the state"}]})
    calls: list[dict] = []
    cp._run_agent = _executor([], calls)
    with TestClient(cp.app) as client:
        run = client.post(
            "/api/orchestrations",
            headers=_headers(),
            json={"task": "read state", "approval_mode": "always"},
        ).json()
    assert run["status"] == "awaiting_approval"
    assert run["steps"][0]["approval_reason"] == "approval mode is always"
    assert calls == []


def test_unknown_tool_requires_approval(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    cp._local_chat = _chat_stub(
        {"goal": "touch the fleet", "steps": [{"title": "Roll", "agent": "alpha-agent", "action": "Restart the workers", "tools": ["terraform"]}]}
    )
    with TestClient(cp.app) as client:
        run = client.post("/api/orchestrations", headers=_headers(), json={"task": "touch the fleet", "auto_execute": False}).json()
    assert run["steps"][0]["approval_required"] is True
    assert "not in the plugin registry" in run["steps"][0]["approval_reason"]


def test_failed_step_retries_then_succeeds(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    cp._local_chat = _chat_stub({"goal": "run", "steps": [{"title": "Work", "agent": "alpha-agent", "action": "Do the work"}]})
    calls: list[dict] = []
    cp._run_agent = _executor(
        [
            {"error": {"message": "upstream unavailable"}, "_http_status": 503},
            {"choices": [{"message": {"content": "recovered"}}]},
        ],
        calls,
    )
    with TestClient(cp.app) as client:
        run = client.post("/api/orchestrations", headers=_headers(), json={"task": "run"}).json()
    assert run["status"] == "completed"
    assert run["steps"][0]["attempts"] == 2
    assert run["steps"][0]["status"] == "done"


def test_exhausted_retries_fail_the_run_and_the_step(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    cp._local_chat = _chat_stub({"goal": "run", "steps": [{"title": "Work", "agent": "alpha-agent", "action": "Do the work", "max_attempts": 2}]})
    calls: list[dict] = []
    cp._run_agent = _executor([RuntimeError("agent exploded"), RuntimeError("agent exploded")], calls)
    with TestClient(cp.app) as client:
        run = client.post("/api/orchestrations", headers=_headers(), json={"task": "run"}).json()
        detail = client.get(f"/api/orchestrations/{run['id']}", headers=_headers()).json()
    assert run["status"] == "failed"
    assert run["steps"][0]["status"] == "failed"
    assert run["steps"][0]["attempts"] == 2
    assert "agent exploded" in detail["steps"][0]["error"]
    assert len(calls) == 2


def test_planner_reuses_completed_run_history(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    prompts: list[str] = []
    cp._local_chat = _chat_stub(
        {
            "goal": "first goal",
            "steps": [{"title": "One", "agent": "alpha-agent", "action": "Do one"}],
        },
        review={"status": "success", "summary": "first run solved it", "findings": [], "rollback_suggestion": ""},
        prompts=prompts,
    )
    calls: list[dict] = []
    cp._run_agent = _executor([], calls)
    with TestClient(cp.app) as client:
        first = client.post("/api/orchestrations", headers=_headers(), json={"task": "first goal"}).json()
        assert first["status"] == "completed"

        cp._local_chat = _chat_stub(
            {"goal": "second goal", "steps": [{"title": "Two", "agent": "alpha-agent", "action": "Do two"}]},
            prompts=prompts,
        )
        second = client.post("/api/orchestrations", headers=_headers(), json={"task": "second goal"}).json()
    assert second["status"] == "completed"
    assert "Recent completed runs" in prompts[-1]
    assert "first goal" in prompts[-1]
    assert "first run solved it" in prompts[-1]


def test_requires_an_active_agent_and_authentication(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    with TestClient(cp.app) as client:
        unauthorized = client.get("/api/orchestrations")
        assert unauthorized.status_code == 401
        empty = client.post("/api/orchestrations", headers=_headers(), json={"task": "do something"})
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "no_agents"


def test_run_history_and_delete_lifecycle(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    _add_agent(cp, "alpha-agent")
    cp._local_chat = _chat_stub({"goal": "goal", "steps": [{"title": "One", "agent": "alpha-agent", "action": "Do"}]})
    cp._run_agent = _executor([], [])
    with TestClient(cp.app) as client:
        run = client.post("/api/orchestrations", headers=_headers(), json={"task": "goal", "auto_execute": False}).json()
        listed = client.get("/api/orchestrations", headers=_headers()).json()
        assert [row["id"] for row in listed] == [run["id"]]
        assert listed[0]["steps_total"] == 1
        assert listed[0]["status"] == "planned"

        removed = client.delete(f"/api/orchestrations/{run['id']}", headers=_headers())
        assert removed.status_code == 200
        missing = client.get(f"/api/orchestrations/{run['id']}", headers=_headers())
        assert missing.status_code == 404


def test_schema_adds_orchestration_tables_without_changing_the_filename(tmp_path):
    db = ControlDB(f"sqlite:///{tmp_path/'control-v0.5.2.sqlite3'}")
    tables = set(db.engine.dialect.get_table_names(db.engine.connect()))
    assert {"v60_agent_runs", "v60_agent_run_steps"} <= tables


def test_panel_exposes_the_orchestrator_console():
    from smart_router.panel_v58 import PANEL_HTML

    for marker in ("'Orchestrator'", "pageOrchestrator", "orchestrationDetail", "orchestrationAct", "orch_task"):
        assert marker in PANEL_HTML
