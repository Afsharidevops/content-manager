from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
from starlette.testclient import TestClient

from smart_router.control_plane import ControlPlane
from smart_router.panel_v58 import PANEL_HTML


def _settings():
    tier = lambda model: SimpleNamespace(model=model)
    return SimpleNamespace(
        hmac_secret="h" * 48,
        client_api_key="legacy-secret",
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


def _cp(tmp_path, monkeypatch):
    db = tmp_path / "control-v064.sqlite3"
    monkeypatch.setenv("SMART_ROUTER_CONTROL_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("SMART_ROUTER_ADMIN_API_KEY", "admin-test-key")
    monkeypatch.delenv("SMART_ROUTER_REDIS_URL", raising=False)
    monkeypatch.delenv("SMART_ROUTER_MEDIA_STUDIO_URL", raising=False)
    return ControlPlane(_settings())


def _headers():
    return {"Authorization": "Bearer admin-test-key"}


def _storyboard():
    return {
        "title": "Container Networking 101",
        "hook": "Packets travel further than you think.",
        "scenes": [
            {
                "index": 1,
                "duration": 5,
                "narration": "Welcome to container networking.",
                "visual": "Docker logo on dark background",
                "emotion": "curious",
                "transition": "cut",
                "animation": "zoom-in",
                "asset_type": "text",
            }
        ],
        "total_duration": 5,
    }


def _timeline():
    return {
        "version": 1,
        "meta": {
            "title": "Container Networking 101",
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "fps": 30,
            "subtitle": True,
            "brand": {"label": "", "position": "bottom-right", "style": "aurora"},
        },
        "scenes": [
            {
                "id": 1,
                "duration": 5,
                "narration": "Welcome to container networking.",
                "visual": "Docker logo",
                "asset_type": "text",
                "transition": "cut",
                "animation": "zoom-in",
                "emotion": "curious",
            }
        ],
    }


def _make_chat_stub(responses: list[str]):
    calls = list(responses)

    async def stub(body, profile="auto"):
        text = calls.pop(0) if calls else "{}"
        return {"choices": [{"message": {"content": text}}]}

    return stub


def test_production_job_lifecycle(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    cp._local_chat = _make_chat_stub(
        [
            json.dumps(_storyboard()),
            json.dumps(_timeline()),
        ]
    )

    ms_calls = []

    def ms_handler(request: httpx.Request) -> httpx.Response:
        ms_calls.append((request.method, str(request.url)))
        if request.method == "POST" and "/jobs" in str(request.url):
            return httpx.Response(202, json={"job": {"id": "ms-job-1", "status": "queued"}})
        if request.method == "GET" and "/jobs/ms-job-1" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "job": {
                        "id": "ms-job-1",
                        "status": "done",
                        "artifacts": [{"name": "output.mp4"}],
                    }
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    monkeypatch.setenv("SMART_ROUTER_MEDIA_STUDIO_URL", "http://ms.invalid")
    cp.media_studio_url = "http://ms.invalid"

    with TestClient(cp.app) as client:
        # Create job
        r = client.post(
            "/api/production/jobs",
            headers=_headers(),
            json={"topic": "Container networking", "aspect_ratio": "9:16", "platform": "youtube"},
        )
        assert r.status_code == 201, r.text
        job = r.json()
        job_id = job["id"]
        assert job["status"] == "draft"
        assert job["aspect_ratio"] == "9:16"

        # GET single job
        r = client.get(f"/api/production/jobs/{job_id}", headers=_headers())
        assert r.status_code == 200
        assert r.json()["id"] == job_id

        # Run storyboard
        r = client.post(f"/api/production/jobs/{job_id}/storyboard", headers=_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "storyboard_created"
        assert body["storyboard"]["title"] == "Container Networking 101"

        # Run timeline
        r = client.post(f"/api/production/jobs/{job_id}/timeline", headers=_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "timeline_ready"
        assert body["timeline"]["version"] == 1

        # Submit render using mocked Media Studio HTTP calls
        class MockAsyncClient:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, url, **kwargs):
                return ms_handler(httpx.Request("POST", url))

            async def get(self, url, **kwargs):
                return ms_handler(httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

        r = client.post(f"/api/production/jobs/{job_id}/render", headers=_headers())
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "rendering"
        assert body["ms_job_id"] == "ms-job-1"

        # Poll render status
        r = client.get(f"/api/production/jobs/{job_id}/render", headers=_headers())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "done"
        assert body["ms_artifact_name"] == "output.mp4"

        # List jobs — should include our job
        r = client.get("/api/production/jobs", headers=_headers())
        assert r.status_code == 200
        ids = [j["id"] for j in r.json()]
        assert job_id in ids

        # Delete job
        r = client.delete(f"/api/production/jobs/{job_id}", headers=_headers())
        assert r.status_code == 200
        r = client.get(f"/api/production/jobs/{job_id}", headers=_headers())
        assert r.status_code == 404


def test_production_job_list_filter_by_status(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)

    with TestClient(cp.app) as client:
        draft = client.post(
            "/api/production/jobs",
            headers=_headers(),
            json={"topic": "Job A", "aspect_ratio": "16:9"},
        )
        assert draft.status_code == 201
        draft_id = draft.json()["id"]

        # list all
        all_jobs = client.get("/api/production/jobs", headers=_headers()).json()
        assert any(j["id"] == draft_id for j in all_jobs)

        # filter by status=draft
        filtered = client.get("/api/production/jobs?status=draft", headers=_headers()).json()
        assert any(j["id"] == draft_id for j in filtered)

        # filter by nonexistent status
        empty = client.get("/api/production/jobs?status=done", headers=_headers()).json()
        assert not any(j["id"] == draft_id for j in empty)


def test_production_render_missing_media_studio_url(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)
    cp._local_chat = _make_chat_stub([json.dumps(_storyboard()), json.dumps(_timeline())])

    with TestClient(cp.app) as client:
        r = client.post(
            "/api/production/jobs",
            headers=_headers(),
            json={"topic": "render test", "aspect_ratio": "9:16"},
        )
        job_id = r.json()["id"]

        client.post(f"/api/production/jobs/{job_id}/storyboard", headers=_headers())
        client.post(f"/api/production/jobs/{job_id}/timeline", headers=_headers())

        r = client.post(f"/api/production/jobs/{job_id}/render", headers=_headers())
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "media_studio_unavailable"


def test_production_job_bad_aspect_ratio(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)

    with TestClient(cp.app) as client:
        r = client.post(
            "/api/production/jobs",
            headers=_headers(),
            json={"topic": "bad ratio", "aspect_ratio": "4:3"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_production_aspect_ratio"


def test_production_storyboard_missing_job(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)

    with TestClient(cp.app) as client:
        r = client.post("/api/production/jobs/99999/storyboard", headers=_headers())
        assert r.status_code == 404


def test_production_timeline_requires_storyboard(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)

    with TestClient(cp.app) as client:
        r = client.post(
            "/api/production/jobs",
            headers=_headers(),
            json={"topic": "timeline test", "aspect_ratio": "9:16"},
        )
        job_id = r.json()["id"]

        r = client.post(f"/api/production/jobs/{job_id}/timeline", headers=_headers())
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "production_storyboard_required"


def test_production_job_requires_topic_or_script(tmp_path, monkeypatch):
    cp = _cp(tmp_path, monkeypatch)

    with TestClient(cp.app) as client:
        r = client.post(
            "/api/production/jobs",
            headers=_headers(),
            json={"topic": "", "script": "", "aspect_ratio": "9:16"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_production_job"


def test_v058_panel_contains_video_studio():
    assert "Video Studio" in PANEL_HTML
    assert "async function pageVideoStudio" in PANEL_HTML
    assert "production/jobs" in PANEL_HTML
    assert "productionStatusBadge" in PANEL_HTML
    assert "downloadProductionArtifact" in PANEL_HTML
