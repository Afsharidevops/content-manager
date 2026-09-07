from __future__ import annotations

import json
from dataclasses import replace

import httpx
from starlette.testclient import TestClient

from smart_router.main import create_app


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts
    async def __aiter__(self):
        for part in self.parts:
            yield part


def upstream(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/v1/models":
        return httpx.Response(200, json={"object": "list", "data": [{"id": "real-model"}]})
    if request.url.path == "/v1/chat/completions":
        data = json.loads(request.content)
        if data.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BytesStream([b'data: {\"x\":1}\n\n', b'data: [DONE]\n\n']))
        return httpx.Response(200, json={"id": "x", "model": data["model"], "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "echo": data})
    return httpx.Response(404)


def test_models_inject_aliases(settings):
    app = create_app(settings, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        payload = client.get("/v1/models").json()
    ids = {item["id"] for item in payload["data"]}
    assert "auto" in ids
    assert {"auto-fast", "auto-standard", "auto-strong"}.isdisjoint(ids)

    # Trusted deployments may explicitly opt in to client tier overrides.
    trusted = replace(settings, allow_tier_overrides=True)
    app = create_app(trusted, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        payload = client.get("/v1/models").json()

    trusted_ids = {item["id"] for item in payload["data"]}
    assert {"auto", "auto-fast", "auto-standard", "auto-strong"} <= trusted_ids


def test_explicit_model_is_preserved(settings):
    app = create_app(settings, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "gpt-explicit", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 9999})
    echoed = response.json()["echo"]
    assert echoed["model"] == "gpt-explicit"
    assert echoed["max_tokens"] == 9999


def test_auto_routes_to_tier_and_applies_budget(settings):
    app = create_app(settings, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"role": "user", "content": "translate hello"}], "max_tokens": 9999})
    echoed = response.json()["echo"]
    assert echoed["model"] == "combo-fast"
    assert echoed["max_tokens"] <= settings.fast.max_output


def test_client_api_key_is_terminated_at_router(settings):
    captured = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return upstream(request)
    secured = replace(settings, client_api_key="client-secret", upstream_api_key="upstream-secret")
    app = create_app(secured, httpx.MockTransport(handler))
    with TestClient(app) as client:
        denied = client.post("/v1/chat/completions", json={"model": "auto", "messages": []})
        ok = client.post("/v1/chat/completions", headers={"Authorization": "Bearer client-secret"}, json={"model": "auto", "messages": [{"role": "user", "content": "translate hi"}]})
    assert denied.status_code == 401
    assert ok.status_code == 200
    assert captured["authorization"] == "Bearer upstream-secret"


def test_streaming_sse_bytes_are_preserved(settings):
    app = create_app(settings, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        with client.stream("POST", "/v1/chat/completions", json={"model": "gpt-explicit", "messages": [], "stream": True}) as response:
            raw = b"".join(response.iter_raw())
    assert raw == b'data: {"x":1}\n\ndata: [DONE]\n\n'


def test_ready_uses_configured_upstream_health_url(settings):
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if str(request.url) == settings.upstream_health_url:
            return httpx.Response(200, json={"status": "ok"})
        return upstream(request)
    app = create_app(settings, httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert settings.upstream_health_url in seen


def test_sticky_tier_cannot_bypass_tool_capability(settings):
    # Deliberately construct an invalid non-monotonic object after startup validation
    # to exercise the runtime belt-and-suspenders safety check.
    unsafe = replace(
        settings,
        allow_tier_overrides=True,
        strong=replace(settings.strong, supports_tools=False),
    )
    app = create_app(unsafe, httpx.MockTransport(upstream))
    headers = {"x-router-session": "sticky-capability-test"}
    with TestClient(app) as client:
        first = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "auto-strong", "messages": [{"role": "user", "content": "hard task"}]},
        )
        second = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "use a tool"}],
                "tools": [{"type": "function", "function": {"name": "read"}}],
            },
        )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["echo"]["model"] == unsafe.standard.model
