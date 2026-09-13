from __future__ import annotations

import json

import httpx
from starlette.testclient import TestClient

from smart_router.main import create_app
from smart_router.proxy import collapse_chat_stream


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


def _sse(*chunks: dict) -> httpx.Response:
    body = b"".join(
        b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n" for chunk in chunks
    ) + b"data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BytesStream([body]))


def _chunk(content: str, *, model: str = "upstream-model", finish: str | None = None) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": finish}],
    }


def test_absent_stream_flag_is_materialized_upstream(settings):
    """An omitted stream flag must reach the upstream as an explicit false."""
    seen: list[dict] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "x", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]})
        return httpx.Response(200, json={"data": []})

    app = create_app(settings, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        routed = client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]})
        explicit = client.post("/v1/chat/completions", json={"model": "gpt-explicit", "messages": [{"role": "user", "content": "hi"}]})

    assert routed.status_code == 200
    assert explicit.status_code == 200
    assert [body["stream"] for body in seen] == [False, False]
    assert seen[1]["model"] == "gpt-explicit"


def test_streaming_upstream_is_collapsed_for_buffered_client(settings):
    """A buffered client must receive JSON even when the upstream streams."""

    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return _sse(_chunk("hello "), _chunk("world", finish="stop"))
        return httpx.Response(200, json={"data": []})

    app = create_app(settings, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["object"] == "chat.completion"
    assert payload["choices"][0]["message"] == {"role": "assistant", "content": "hello world"}
    assert payload["choices"][0]["finish_reason"] == "stop"
    assert payload["model"] == "upstream-model"


def test_explicit_stream_flag_still_streams(settings):
    def upstream(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return _sse(_chunk("streamed", finish="stop"))
        return httpx.Response(200, json={"data": []})

    app = create_app(settings, httpx.MockTransport(upstream))
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: " in response.text


def test_collapse_chat_stream_merges_tool_call_deltas():
    body = "\n\n".join(
        [
            'data: {"id":"c1","model":"m","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup","arguments":"{\\"q\\":"}}]}}]}',
            'data: {"id":"c1","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"x\\"}"}}]},"finish_reason":null}]}',
            'data: {"id":"c1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]
    )
    payload = collapse_chat_stream(body)
    assert payload is not None
    choice = payload["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == ""
    assert choice["message"]["tool_calls"] == [
        {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"q":"x"}'}}
    ]


def test_collapse_chat_stream_ignores_bodies_without_chunks():
    assert collapse_chat_stream("") is None
    assert collapse_chat_stream("data: [DONE]\n\n") is None
