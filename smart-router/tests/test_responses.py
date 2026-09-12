"""Tests for the Responses API endpoint used by Codex."""

from __future__ import annotations

import json

import httpx
from starlette.testclient import TestClient

from smart_router.main import create_app


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
            b'data: {"id":"chatcmpl-1","model":"m","choices":[{"index":0,"delta":{"content":"salam "}}]}\n\n',
            b'data: {"id":"chatcmpl-1","model":"m","choices":[{"index":0,"delta":{"content":"donya"}}]}\n\n',
            b'data: {"id":"chatcmpl-1","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n',
            b"data: [DONE]\n\n",
        ]
        self.reply = reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
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
                "id": "chatcmpl-1",
                "model": data["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "salam donya"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            },
        )


def client_for(settings, recorder: Recorder) -> TestClient:
    app = create_app(settings, httpx.MockTransport(recorder))
    return TestClient(app)


def sse_events(raw: bytes) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event name, JSON payload) pairs."""
    events: list[tuple[str, dict]] = []
    name = ""
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
            if payload and payload != "[DONE]":
                events.append((name, json.loads(payload)))
    return events


def test_instructions_and_input_become_chat_messages(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        response = client.post(
            "/v1/responses",
            json={
                "model": "gpt-explicit",
                "instructions": "You are terse.",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    }
                ],
                "max_output_tokens": 128,
                "reasoning": {"effort": "high"},
            },
        )
    assert response.status_code == 200
    body = recorder.bodies[0]
    assert body["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "hello"},
    ]
    assert body["max_tokens"] == 128
    assert body["reasoning_effort"] == "high"
    assert body["model"] == "gpt-explicit"


def test_response_translation_returns_message_items(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        payload = client.post(
            "/v1/responses", json={"model": "gpt-explicit", "input": "hi"}
        ).json()
    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["output_text"] == "salam donya"
    item = payload["output"][0]
    assert item["type"] == "message"
    assert item["content"][0] == {
        "type": "output_text",
        "text": "salam donya",
        "annotations": [],
    }
    assert payload["usage"]["input_tokens"] == 11
    assert payload["usage"]["output_tokens"] == 7
    assert payload["usage"]["total_tokens"] == 18


def test_tool_definitions_are_converted_and_server_tools_dropped(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/responses",
            json={
                "model": "gpt-explicit",
                "input": "hi",
                "tools": [
                    {
                        "type": "function",
                        "name": "get_weather",
                        "description": "Look it up",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    {"type": "web_search"},
                ],
                "tool_choice": {"type": "function", "name": "get_weather"},
            },
        )
    body = recorder.bodies[0]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Look it up",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}}


def test_function_call_history_round_trips(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/responses",
            json={
                "model": "gpt-explicit",
                "input": [
                    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "run it"}]},
                    {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{\"cmd\":\"ls\"}"},
                    {"type": "function_call_output", "call_id": "call_1", "output": "a.txt"},
                    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
                ],
            },
        )
    messages = recorder.bodies[0]["messages"]
    assert messages[0] == {"role": "user", "content": "run it"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "shell"
    assert messages[1]["tool_calls"][0]["id"] == "call_1"
    assert messages[2] == {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"}
    assert messages[3] == {"role": "assistant", "content": "done"}


def test_tool_calls_come_back_as_function_call_items(settings):
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
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    recorder = Recorder(reply=reply)
    with client_for(settings, recorder) as client:
        payload = client.post(
            "/v1/responses", json={"model": "gpt-explicit", "input": "list files"}
        ).json()
    assert [item["type"] for item in payload["output"]] == ["function_call"]
    call = payload["output"][0]
    assert call["call_id"] == "call_7"
    assert call["name"] == "shell"
    assert call["arguments"] == '{"cmd":"ls"}'
    assert payload["output_text"] == ""


def test_streaming_emits_responses_events(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/responses",
            json={"model": "auto", "input": "hi", "stream": True},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            raw = b"".join(response.iter_bytes())
    events = sse_events(raw)
    names = [name for name, _ in events]
    assert names[0] == "response.created"
    assert "response.output_text.delta" in names
    assert names[-1] == "response.completed"
    payloads = [payload for _, payload in events]
    deltas = [
        item["delta"] for item in payloads if item["type"] == "response.output_text.delta"
    ]
    assert "".join(deltas) == "salam donya"
    completed = payloads[-1]["response"]
    assert completed["status"] == "completed"
    assert completed["output"][0]["content"][0]["text"] == "salam donya"
    assert completed["usage"]["input_tokens"] == 3
    assert all("sequence_number" in item for item in payloads)


def test_streaming_tool_calls_emit_function_call_arguments(settings):
    parts = [
        b'data: {"id":"chatcmpl-3","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_2","function":{"name":"shell","arguments":"{\\"cmd\\":"}}]}}]}\n\n',
        b'data: {"id":"chatcmpl-3","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"ls\\"}"}}]}}]}\n\n',
        b'data: {"id":"chatcmpl-3","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    recorder = Recorder(stream_parts=parts)
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/responses",
            json={"model": "auto", "input": "list", "stream": True},
        ) as response:
            raw = b"".join(response.iter_bytes())
    events = sse_events(raw)
    names = [name for name, _ in events]
    assert "response.function_call_arguments.delta" in names
    assert names[-1] == "response.completed"
    payloads = [payload for _, payload in events]
    done = [item for item in payloads if item["type"] == "response.output_item.done"]
    item = done[0]["item"]
    assert item["type"] == "function_call"
    assert item["call_id"] == "call_2"
    assert item["name"] == "shell"
    assert item["arguments"] == '{"cmd":"ls"}'
    completed = payloads[-1]["response"]
    assert [entry["type"] for entry in completed["output"]] == ["function_call"]


def test_broken_upstream_stream_reports_a_failed_response(settings):
    parts = [
        b'data: {"id":"chatcmpl-4","model":"m","choices":[{"index":0,"delta":{"content":"half"}}]}\n\n'
    ]
    recorder = Recorder(stream_parts=parts, stream_error=RuntimeError("upstream went away"))
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/responses",
            json={"model": "auto", "input": "hi", "stream": True},
        ) as response:
            raw = b"".join(response.iter_bytes())
    events = sse_events(raw)
    names = [name for name, _ in events]
    assert "response.completed" not in names
    assert names[-1] == "response.failed"
    failed = events[-1][1]["response"]
    assert failed["status"] == "failed"
    assert "upstream went away" in failed["error"]["message"]
    assert failed["output"][0]["content"][0]["text"] == "half"


def assert_codex_stream_contract(raw: bytes) -> list[tuple[str, dict]]:
    """Validate the stream against the schema the Codex client parses.

    Codex deserializes every frame into a tagged event struct, parses
    ``response.output_item.done`` payloads into ``ResponseItem`` and treats
    ``response.completed`` as the terminal frame, so a frame it cannot parse
    either loses content or aborts the turn.
    """
    events = sse_events(raw)
    assert events, "the stream produced no events"
    for sequence, (name, payload) in enumerate(events, start=1):
        assert payload["type"] == name
        assert payload["sequence_number"] == sequence
    names = [name for name, _ in events]
    assert names[0] == "response.created"
    assert isinstance(events[0][1]["response"]["id"], str)
    assert names[-1] == "response.completed"
    completed = events[-1][1]["response"]
    assert isinstance(completed["id"], str)
    usage = completed["usage"]
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        assert isinstance(usage[key], int)
    assert isinstance(usage["input_tokens_details"]["cached_tokens"], int)
    assert isinstance(usage["output_tokens_details"]["reasoning_tokens"], int)
    for name, payload in events:
        if name != "response.output_item.done":
            continue
        item = payload["item"]
        if item["type"] == "function_call":
            assert isinstance(item["id"], str)
            assert isinstance(item["call_id"], str)
            assert isinstance(item["name"], str)
            assert isinstance(item["arguments"], str)
        elif item["type"] == "message":
            assert isinstance(item["id"], str)
            assert item["role"] == "assistant"
            for part in item["content"]:
                assert part["type"] == "output_text"
                assert isinstance(part["text"], str)
    return events


def test_codex_wire_contract_for_text_and_tools(settings):
    parts = [
        b'data: {"id":"chatcmpl-8","model":"m","choices":[{"index":0,"delta":{"content":"let me check"}}]}\n\n',
        b'data: {"id":"chatcmpl-8","model":"m","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_9","function":{"name":"shell","arguments":"{\\"cmd\\":\\"ls\\"}"}}]}}]}\n\n',
        b'data: {"id":"chatcmpl-8","model":"m","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":5,"completion_tokens":6,"total_tokens":11,"prompt_tokens_details":{"cached_tokens":2},"completion_tokens_details":{"reasoning_tokens":1}}}\n\n',
        b"data: [DONE]\n\n",
    ]
    recorder = Recorder(stream_parts=parts)
    with client_for(settings, recorder) as client:
        with client.stream(
            "POST",
            "/v1/responses",
            json={"model": "auto", "input": "list", "stream": True},
        ) as response:
            raw = b"".join(response.iter_bytes())
    events = assert_codex_stream_contract(raw)
    done = [
        payload["item"]
        for name, payload in events
        if name == "response.output_item.done"
    ]
    call = [item for item in done if item["type"] == "function_call"][0]
    assert call["call_id"] == "call_9"
    assert call["name"] == "shell"
    assert json.loads(call["arguments"]) == {"cmd": "ls"}
    completed = events[-1][1]["response"]
    assert completed["usage"]["total_tokens"] == 11
    assert completed["usage"]["input_tokens_details"]["cached_tokens"] == 2


def test_codex_wire_contract_for_translated_history(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/responses",
            json={
                "model": "auto",
                "instructions": "be terse",
                "input": [
                    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "run"}]},
                    {"type": "function_call", "call_id": "call_1", "name": "shell", "arguments": "{}"},
                    {"type": "function_call_output", "call_id": "call_1", "output": "done"},
                    {"type": "reasoning", "summary": [], "encrypted_content": "zzz"},
                ],
                "tools": [{"type": "function", "name": "shell", "parameters": {"type": "object"}}],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
                "store": False,
                "include": ["reasoning.encrypted_content"],
            },
        )
    body = recorder.bodies[0]
    assert body["messages"][0] == {"role": "system", "content": "be terse"}
    assert body["messages"][1] == {"role": "user", "content": "run"}
    assert body["messages"][2]["role"] == "assistant"
    assert body["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert body["messages"][3]["role"] == "tool"
    assert body["messages"][3]["tool_call_id"] == "call_1"
    assert len([item for item in body["messages"] if item["role"] == "assistant"]) == 1
    assert body["tool_choice"] == "auto"
    assert body["parallel_tool_calls"] is False


def test_auto_alias_is_routed_to_a_tier_model(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post("/v1/responses", json={"model": "auto", "input": "hi"})
    assert recorder.bodies[0]["model"] == "combo-fast"


def test_compressed_upstream_stream_is_requested_as_identity(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        client.post(
            "/v1/responses",
            json={"model": "gpt-explicit", "input": "hi", "stream": True},
            headers={"accept-encoding": "gzip, br"},
        )
    assert recorder.headers[0]["accept-encoding"] == "identity"


def test_invalid_requests_are_rejected(settings):
    recorder = Recorder()
    with client_for(settings, recorder) as client:
        assert client.post("/v1/responses", json={"input": "hi"}).status_code == 400
        bad = client.post(
            "/v1/responses",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert bad.status_code == 400
    assert recorder.bodies == []
