from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
from starlette.testclient import TestClient

from smart_router import content_agents as ca
from smart_router.control_db import Agent
from smart_router.control_plane import ControlPlane
from smart_router.main import create_app


def _settings():
    tier = lambda model: SimpleNamespace(model=model)  # noqa: E731
    return SimpleNamespace(
        hmac_secret="h" * 48,
        client_api_key="",
        fast=tier("fast-model"),
        standard=tier("standard-model"),
        strong=tier("strong-model"),
        upstream_base_url="http://gateway.invalid/v1",
        upstream_health_url="http://gateway.invalid/health",
        upstream_api_key="",
        mode="route",
        policy="heuristic",
        allow_tier_overrides=False,
        read_timeout_seconds=30,
    )


def _cp(tmp_path, monkeypatch) -> ControlPlane:
    db = tmp_path / "content-agents.sqlite3"
    monkeypatch.setenv("SMART_ROUTER_CONTROL_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("SMART_ROUTER_ADMIN_API_KEY", "admin-test-key")
    monkeypatch.delenv("SMART_ROUTER_REDIS_URL", raising=False)
    return ControlPlane(_settings())


def _chat(answers, calls=None):
    """Stub _local_chat, serving one answer per call."""
    async def fake(body, profile="auto"):
        if calls is not None:
            calls.append({"messages": body.get("messages"), "profile": profile})
        answer = answers.pop(0) if answers else ""
        return {"choices": [{"message": {"content": answer}}]}
    return fake


STORYBOARD_ANSWER = {
    "title": "Docker on RouterOS",
    "hook": "Your router can run containers",
    "scenes": [
        {"index": 1, "duration": 4, "narration": "First", "visual": "Title", "transition": "cut"},
        {"index": 2, "duration": 999, "narration": "Second", "transition": "not-a-transition"},
    ],
    "total_duration": 13,
}

TIMELINE_ANSWER = {
    "version": 1,
    "meta": {"title": "Docker on RouterOS", "aspect_ratio": "9:16", "brand": {"label": "Locallab"}},
    "scenes": [
        {"id": 1, "duration": 4, "narration": "اول", "visual": "شروع", "transition": "fade"},
        {"id": 2, "duration": 5, "narration": "دوم", "transition": "wipeleft", "animation": "zoom-in"},
    ],
}


def test_normalize_storyboard_clamps_scenes():
    result = ca.normalize_storyboard(dict(STORYBOARD_ANSWER))
    assert result["title"] == "Docker on RouterOS"
    assert result["scenes"][0]["transition"] == "cut"
    assert result["scenes"][1]["duration"] == 20.0
    assert result["scenes"][1]["transition"] == "fade"
    assert result["total_duration"] == 24.0


def test_normalize_storyboard_rejects_empty_answers():
    try:
        ca.normalize_storyboard({"scenes": []})
    except ValueError as error:
        assert "no scenes" in str(error)
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("empty storyboard should be rejected")


def test_normalize_timeline_document_is_render_ready():
    result = ca.normalize_timeline_document(dict(TIMELINE_ANSWER))
    assert result["meta"]["resolution"] == "1080x1920"
    assert result["meta"]["fps"] == 30
    assert result["meta"]["brand"]["label"] == "Locallab"
    assert result["scenes"][0]["transition"] == "cut"
    assert result["scenes"][1]["transition"] == "wipeleft"
    assert result["scenes"][1]["animation"] == "zoom-in"
    assert [scene["id"] for scene in result["scenes"]] == [1, 2]


def test_normalize_timeline_document_defaults_unknown_aspect():
    result = ca.normalize_timeline_document(
        {"meta": {"aspect_ratio": "3:7"}, "scenes": [{"duration": 4, "narration": "x"}]}
    )
    assert result["meta"]["aspect_ratio"] == "9:16"


def test_normalize_media_plan_sums_costs():
    result = ca.normalize_media_plan(
        {
            "assets": [
                {"scene_id": 1, "asset_type": "text", "source": "text", "estimated_cost_usd": 0},
                {"scene_id": 2, "asset_type": "image", "source": "api-image", "estimated_cost_usd": 0.04},
            ],
            "notes": "free first",
        }
    )
    assert result["total_estimated_cost_usd"] == 0.04
    assert result["assets"][1]["source"] == "api-image"
    assert result["notes"] == "free first"


def test_normalize_recovery_defaults_to_abort():
    result = ca.normalize_recovery({"diagnosis": "unknown page"})
    assert result["action"] == "abort"
    assert result["wait_seconds"] == 2.0


def test_normalize_recovery_clamps_wait_seconds():
    result = ca.normalize_recovery({"action": "wait", "wait_seconds": 999})
    assert result["action"] == "wait"
    assert result["wait_seconds"] == 120.0


def test_ensure_content_agents_is_idempotent(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    first = ca.ensure_content_agents(cp)
    assert set(first["created"]) == {definition.name for definition in ca.CONTENT_AGENTS}
    second = ca.ensure_content_agents(cp)
    assert second["created"] == []
    with cp.db.session() as session:
        names = {row.name for row in session.query(Agent).all()}
    assert {definition.name for definition in ca.CONTENT_AGENTS} <= names


def test_ensure_content_agents_keeps_operator_edits(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    ca.ensure_content_agents(cp)
    with cp.db.session() as session:
        row = session.query(Agent).filter_by(name="Video Director Agent").one()
        row.system_prompt = "operator edited prompt"
        session.commit()
    ca.ensure_content_agents(cp)
    with cp.db.session() as session:
        row = session.query(Agent).filter_by(name="Video Director Agent").one()
    assert row.system_prompt == "operator edited prompt"


def test_content_agent_ids_returns_seeded_names(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    ca.ensure_content_agents(cp)
    ids = ca.content_agent_ids(cp)
    assert set(ids) == {definition.name for definition in ca.CONTENT_AGENTS}
    assert all(isinstance(value, int) for value in ids.values())


async def _run(coro):
    return await coro


def test_build_timeline_retries_once_on_prose(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    calls = []
    cp._local_chat = _chat(["I cannot do that.", json.dumps(TIMELINE_ANSWER)], calls)
    result = ca_timeline(cp)
    assert result["meta"]["aspect_ratio"] == "9:16"
    assert len(calls) == 2
    repair = calls[1]["messages"][-1]["content"]
    assert "JSON object only" in repair


def ca_timeline(cp):
    import asyncio

    return asyncio.run(ca.build_timeline(cp, {"topic": "docker"}))


def test_build_timeline_raises_when_no_json(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    cp._local_chat = _chat(["still prose", "still prose"])
    try:
        ca_timeline(cp)
    except ca.ContentAgentError as error:
        assert error.code == "content_agent_failed"
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("a prose-only answer must raise")


def test_plan_video_chains_storyboard_into_timeline(tmp_path, monkeypatch):
    import asyncio

    cp = _cp(tmp_path, monkeypatch)
    cp._local_chat = _chat([json.dumps(STORYBOARD_ANSWER), json.dumps(TIMELINE_ANSWER)])
    plan = asyncio.run(ca.plan_video(cp, {"topic": "docker", "aspect_ratio": "9:16"}))
    assert plan["storyboard"]["title"] == "Docker on RouterOS"
    assert plan["timeline"]["scenes"][0]["transition"] == "cut"


def test_recovery_decision_returns_one_action(tmp_path, monkeypatch):
    import asyncio

    cp = _cp(tmp_path, monkeypatch)
    answer = {
        "diagnosis": "a subscriptions dialog covers the studio",
        "action": "dismiss_overlay",
        "wait_seconds": 1,
        "selector_hint": "button:has-text('close')",
        "reason": "the dialog is not the source picker",
    }
    cp._local_chat = _chat([json.dumps(answer)])
    decision = asyncio.run(ca.recovery_decision(cp, {"step": "open video overview", "attempt": 2}))
    assert decision["action"] == "dismiss_overlay"
    assert decision["selector_hint"] == "button:has-text('close')"


def _app(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_ROUTER_CONTROL_DATABASE_URL", f"sqlite:///{tmp_path / 'app.sqlite3'}")
    monkeypatch.delenv("SMART_ROUTER_REDIS_URL", raising=False)
    app = create_app(settings, httpx.MockTransport(lambda _request: httpx.Response(404)))
    return app


def test_content_endpoints_serve_the_pipeline(settings, tmp_path, monkeypatch):
    app = _app(settings, monkeypatch, tmp_path)
    cp = app.state.control_plane
    cp._local_chat = _chat(
        [
            json.dumps(TIMELINE_ANSWER),
            json.dumps(STORYBOARD_ANSWER),
            json.dumps(TIMELINE_ANSWER),
        ]
    )
    headers = {"x-hermes-internal": cp.internal_token}
    with TestClient(app) as client:
        agents = client.get("/v1/content/agents", headers=headers)
        assert agents.status_code == 200
        names = {row["name"] for row in agents.json()["data"]}
        assert "Video Director Agent" in names
        assert all(row["agent_id"] for row in agents.json()["data"])

        timeline = client.post("/v1/content/timeline", headers=headers, json={"topic": "docker"})
        assert timeline.status_code == 200
        assert timeline.json()["timeline"]["scenes"][0]["transition"] == "cut"

        plan = client.post("/v1/content/video-plan", headers=headers, json={"topic": "docker"})
        assert plan.status_code == 200
        assert set(plan.json()) == {"storyboard", "timeline"}


def test_content_endpoints_reject_bad_bodies(settings, tmp_path, monkeypatch):
    app = _app(settings, monkeypatch, tmp_path)
    cp = app.state.control_plane
    headers = {"x-hermes-internal": cp.internal_token}
    with TestClient(app) as client:
        response = client.post(
            "/v1/content/storyboard",
            headers={**headers, "content-type": "application/json"},
            content="[1, 2, 3]",
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_body"


def test_content_endpoints_require_authentication_when_a_key_is_set(settings, tmp_path, monkeypatch):
    from dataclasses import replace

    secured = replace(settings, client_api_key="legacy-secret")
    app = _app(secured, monkeypatch, tmp_path)
    with TestClient(app) as client:
        anonymous = client.get("/v1/content/agents")
        assert anonymous.status_code == 401
        authorized = client.get(
            "/v1/content/agents", headers={"Authorization": "Bearer legacy-secret"}
        )
        assert authorized.status_code == 200
