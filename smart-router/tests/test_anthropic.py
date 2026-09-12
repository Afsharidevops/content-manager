"""Tests for the Anthropic Messages endpoint used by Claude Code."""

from __future__ import annotations

import json

import httpx
from starlette.testclient import TestClient

from smart_router.main import create_app

ANTHROPIC_HEADERS = {"anthropic-version": "2023-06-01", "x-api-key": "sk-test"}


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


class BrokenStream(httpx.AsyncByteStream):
    """Upstream stream that dies after the bytes it already produced."""

    def __init__(self, parts, error):
        self.parts = parts
        self.error = error

    async def __aiter__(self):
        for part in self.parts:
            yield part
        raise self.error


class Recorder:
    """Fake upstream that records the translated Chat request."""

    def __init__(self, *, stream_parts=None, stream_error=None, reply=None):
        self.bodies: list[dict] = []
        self.headers: list[dict] = []
        self.stream_error = stream_error
        self.stream_parts = stream_parts or [
            b'data: {"id":"chatcmpl-s","model":"m","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n',
            b'data: {"id":"chatcmpl-s","model":"m","choices":[{"index":0,"delta":{"content":"salam"}}]}\n\n',
            b'data: {"id":"chatcmpl-s","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":9,"completion_tokens":4}}\n\n',
            b"data: [DONE]\n\n",
        ]
        self.reply = reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200, json={"object": "list", "data": [{"id": "real-model", "object": "model"}]}
            )
        data = json.loads(request.content)
        self.bodies.append(data)
        self.headers.append({k.lower(): v for k, v in request.headers.items()})
        if data.get("stream"):
            stream = (
                BrokenStream(self.stream_parts, self.stream_error)
                if self.stream_error is not None
                else BytesStream(self.stream_parts)
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=stream,
            )
        if self.reply is not None:
            return httpx.Response(200, json=self.reply)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-9",
                "model": data["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "salam donya"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6},
            },
        )


def client_for(settings, recorder: Recorder) -> TestClient:
    return TestClient(create_app(settings, httpx.MockTransport(recorder)))


def sse_events(raw: bytes) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event name, JSON payload) pairs."""
    events: list[tuple[str, dict]] = []
    name = ""
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
            if payload:
                events.append((name, json.loads(payload)))
    return events


def test_system_and_messages_become_chat_messages(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        response = client.post(
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={
                "model": "gpt-explicit",
                "max_tokens": 512,
                "system": [{"type": "text", "text": "be terse"}],
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "hi"},
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "shell",
                                "input": {"cmd": "ls"},
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "a.txt"}
                        ],
                    },
                ],
            },
        )
    assert response.status_code == 200
    body = recorder.bodies[0]
    assert body["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "hi",
            "tool_calls": [
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": '{"cmd": "ls"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_1", "content": "a.txt"},
    ]
    assert body["max_tokens"] == 512
    assert body["model"] == "gpt-explicit"


def test_client_billing_header_is_not_forwarded(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={
                "model": "gpt-explicit",
                "max_tokens": 16,
                "system": [
                    {
                        "type": "text",
                        "text": "x-anthropic-billing-header: cc_version=2.1.246.0ab; cc_entrypoint=sdk-cli;",
                    },
                    {"type": "text", "text": "You are a helpful agent."},
                ],
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert recorder.bodies[0]["messages"][0] == {
        "role": "system",
        "content": "You are a helpful agent.",
    }


def test_tools_and_choice_are_converted(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={
                "model": "gpt-explicit",
                "max_tokens": 100,
                "tools": [
                    {
                        "name": "shell",
                        "description": "run a command",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
                "tool_choice": {"type": "tool", "name": "shell"},
                "messages": [{"role": "user", "content": "list files"}],
            },
        )
    body = recorder.bodies[0]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "run a command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "shell"}}


def test_images_become_data_urls(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={
                "model": "gpt-explicit",
                "max_tokens": 64,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "what is this?"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "AAAA",
                                },
                            },
                        ],
                    }
                ],
            },
        )
    content = recorder.bodies[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAAA"},
    }


def test_message_translation_returns_anthropic_shape(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        payload = client.post(
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={"model": "gpt-explicit", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        ).json()
    assert payload["type"] == "message"
    assert payload["role"] == "assistant"
    assert payload["content"] == [{"type": "text", "text": "salam donya"}]
    assert payload["stop_reason"] == "end_turn"
    assert payload["usage"] == {"input_tokens": 12, "output_tokens": 6}


def test_tool_use_answer_maps_to_stop_reason_tool_use(settings):
    reply = {
        "id": "chatcmpl-2",
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_7",
                            "type": "function",
                            "function": {"name": "shell", "arguments": '{"cmd":"ls"}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4},
    }
    recorder = Recorder(reply=reply)
    with client_for(settings, recorder) as client:
        payload = client.post(
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={"model": "gpt-explicit", "max_tokens": 64, "messages": [{"role": "user", "content": "go"}]},
        ).json()
    assert payload["stop_reason"] == "tool_use"
    block = payload["content"][0]
    assert block["type"] == "tool_use"
    assert block["id"] == "call_7"
    assert block["name"] == "shell"
    assert block["input"] == {"cmd": "ls"}


def test_streaming_emits_anthropic_events(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={"model": "auto", "max_tokens": 64, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            raw = b"".join(response.iter_bytes())
    events = sse_events(raw)
    names = [name for name, _ in events]
    assert names[0] == "message_start"
    assert names[-1] == "message_stop"
    deltas = [
        payload["delta"]["text"]
        for name, payload in events
        if name == "content_block_delta" and payload["delta"]["type"] == "text_delta"
    ]
    assert "".join(deltas) == "salam"
    stop = [payload for name, payload in events if name == "message_delta"][0]
    assert stop["delta"]["stop_reason"] == "end_turn"
    assert stop["usage"]["output_tokens"] == 4


def test_broken_upstream_stream_ends_with_an_error_event(settings):
    parts = [
        b'data: {"id":"chatcmpl-e","model":"m","choices":[{"index":0,"delta":{"content":"half"}}]}\n\n'
    ]
    recorder = Recorder(stream_parts=parts, stream_error=RuntimeError("upstream went away"))
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={"model": "auto", "max_tokens": 64, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as response:
            raw = b"".join(response.iter_bytes())
    events = sse_events(raw)
    names = [name for name, _ in events]
    assert "message_stop" not in names
    assert names[-1] == "error"
    assert "upstream went away" in events[-1][1]["error"]["message"]


def test_message_start_carries_the_input_token_estimate(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={
                "model": "auto",
                "max_tokens": 64,
                "stream": True,
                "system": "be terse",
                "messages": [{"role": "user", "content": "x" * 400}],
            },
        ) as response:
            raw = b"".join(response.iter_bytes())
    events = sse_events(raw)
    start = [payload for name, payload in events if name == "message_start"][0]
    assert start["message"]["usage"]["input_tokens"] >= 100
    assert start["message"]["usage"]["output_tokens"] == 0


def test_streaming_tool_use_emits_input_json_deltas(settings):
    parts = [
        b'data: {"id":"chatcmpl-3","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"shell","arguments":"{\\"cmd\\":"}}]}}]}\n\n',
        b'data: {"id":"chatcmpl-3","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"ls\\"}"}}]}}]}\n\n',
        b'data: {"id":"chatcmpl-3","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    recorder = Recorder(stream_parts=parts)
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={"model": "auto", "max_tokens": 64, "stream": True, "messages": [{"role": "user", "content": "go"}]},
        ) as response:
            raw = b"".join(response.iter_bytes())
    events = sse_events(raw)
    names = [name for name, _ in events]
    assert "content_block_start" in names
    assert "content_block_stop" in names
    start = [
        payload["content_block"]
        for name, payload in events
        if name == "content_block_start"
    ][0]
    assert start["type"] == "tool_use"
    assert start["id"] == "call_1"
    assert start["name"] == "shell"
    fragments = [
        payload["delta"]["partial_json"]
        for name, payload in events
        if name == "content_block_delta" and payload["delta"]["type"] == "input_json_delta"
    ]
    assert "".join(fragments) == '{"cmd":"ls"}'
    stop = [payload for name, payload in events if name == "message_delta"][0]
    assert stop["delta"]["stop_reason"] == "tool_use"


def assert_anthropic_stream_contract(raw: bytes) -> list[tuple[str, dict]]:
    """Validate the stream against the Messages API streaming contract.

    The official SDK dispatches on the ``event:`` name, expects a matching
    ``type`` inside the payload, and tracks content blocks by index, so blocks
    must open in ascending order and each one must be closed.
    """
    events = sse_events(raw)
    assert events, "the stream produced no events"
    names = [name for name, _ in events]
    assert names[0] == "message_start"
    assert names[-1] == "message_stop"
    for name, payload in events:
        assert payload["type"] == name
    start = events[0][1]["message"]
    assert start["type"] == "message"
    assert start["role"] == "assistant"
    assert isinstance(start["id"], str)
    assert isinstance(start["content"], list)
    assert isinstance(start["usage"]["input_tokens"], int)
    assert isinstance(start["usage"]["output_tokens"], int)
    opened: list[int] = []
    closed: list[int] = []
    for name, payload in events:
        if name == "content_block_start":
            opened.append(payload["index"])
            assert payload["content_block"]["type"] in {"text", "tool_use"}
        elif name == "content_block_stop":
            closed.append(payload["index"])
        elif name == "content_block_delta":
            assert payload["delta"]["type"] in {"text_delta", "input_json_delta"}
    assert opened == sorted(opened)
    assert opened == sorted(closed) and len(opened) == len(closed)
    delta = [payload for name, payload in events if name == "message_delta"][0]
    assert delta["delta"]["stop_reason"] in {"end_turn", "max_tokens", "tool_use", "stop_sequence"}
    assert isinstance(delta["usage"]["output_tokens"], int)
    return events


def test_anthropic_wire_contract_for_text_and_tools(settings):
    parts = [
        b'data: {"id":"chatcmpl-w","model":"m","choices":[{"index":0,"delta":{"content":"checking"}}]}\n\n',
        b'data: {"id":"chatcmpl-w","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_5","function":{"name":"shell","arguments":"{\\"cmd\\":\\"ls\\"}"}}]}}]}\n\n',
        b'data: {"id":"chatcmpl-w","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":8,"completion_tokens":3}}\n\n',
        b"data: [DONE]\n\n",
    ]
    recorder = Recorder(stream_parts=parts)
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={"model": "auto", "max_tokens": 32, "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as response:
            raw = b"".join(response.iter_bytes())
    events = assert_anthropic_stream_contract(raw)
    tool_start = [
        payload["content_block"]
        for name, payload in events
        if name == "content_block_start" and payload["content_block"]["type"] == "tool_use"
    ][0]
    assert tool_start["id"] == "call_5"
    assert tool_start["name"] == "shell"
    partial = "".join(
        payload["delta"]["partial_json"]
        for name, payload in events
        if name == "content_block_delta" and payload["delta"]["type"] == "input_json_delta"
    )
    assert json.loads(partial) == {"cmd": "ls"}
    delta = [payload for name, payload in events if name == "message_delta"][0]
    assert delta["delta"]["stop_reason"] == "tool_use"


def test_sdk_headers_never_reach_the_backend(settings):
    recorder = Recorder()
    headers = dict(ANTHROPIC_HEADERS)
    headers.update(
        {
            "anthropic-beta": "prompt-caching-2024-07-31",
            "x-app": "cli",
            "x-stainless-lang": "js",
            "x-stainless-package-version": "0.68.0",
            "x-stainless-runtime": "node",
            "x-stainless-retry-count": "0",
        }
    )
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/messages",
            headers=headers,
            json={"model": "gpt-explicit", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        )
    sent = set(recorder.headers[0])
    assert not [name for name in sent if name.startswith("x-stainless-")]
    assert "anthropic-version" not in sent
    assert "anthropic-beta" not in sent
    assert "x-app" not in sent
    assert recorder.headers[0]["accept-encoding"] == "identity"


def test_auto_alias_is_routed_and_client_headers_are_stripped(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/messages",
            headers=ANTHROPIC_HEADERS,
            json={"model": "auto", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        )
    assert recorder.bodies[0]["model"] == "combo-fast"
    forwarded = recorder.headers[0]
    assert "anthropic-version" not in forwarded
    assert forwarded["accept-encoding"] == "identity"


def test_count_tokens_returns_an_estimate(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        payload = client.post(
            "/v1/messages/count_tokens",
            headers=ANTHROPIC_HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hello world"}],
            },
        ).json()
    assert payload["input_tokens"] >= 1
    assert recorder.bodies == []


def test_models_endpoint_serves_the_anthropic_shape(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        payload = client.get("/v1/models", headers=ANTHROPIC_HEADERS).json()
        openai_payload = client.get("/v1/models").json()
    assert payload["data"][0]["id"] == "real-model"
    assert payload["data"][0]["type"] == "model"
    assert payload["has_more"] is False
    assert "object" in openai_payload
