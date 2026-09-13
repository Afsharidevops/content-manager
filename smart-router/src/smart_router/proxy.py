from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

STATIC_HOP_BY_HOP = {
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"te", b"trailer", b"transfer-encoding", b"upgrade", b"host", b"content-length",
}
SINGLETON_CREDENTIALS = {b"authorization", b"x-api-key", b"x-goog-api-key"}
ROUTER_PRIVATE = {b"x-router-session", b"x-router-tier", b"x-router-reset", b"x-router-cache"}


def forward_headers(
    headers: list[tuple[bytes, bytes]], *, upstream_api_key: str | None = None,
    consume_client_credentials: bool = False,
) -> list[tuple[bytes, bytes]]:
    excluded = _excluded_headers(headers)
    credential_counts: dict[bytes, int] = {}
    result: list[tuple[bytes, bytes]] = []
    for name, value in headers:
        lower = name.lower()
        if lower in SINGLETON_CREDENTIALS:
            credential_counts[lower] = credential_counts.get(lower, 0) + 1
            if credential_counts[lower] > 1:
                raise ValueError(f"duplicate credential header: {lower.decode()}")
            if consume_client_credentials:
                continue
        if lower not in excluded and lower not in ROUTER_PRIVATE:
            result.append((name, value))
    if upstream_api_key:
        result = [pair for pair in result if pair[0].lower() not in SINGLETON_CREDENTIALS]
        result.append((b"authorization", f"Bearer {upstream_api_key}".encode()))
    return result


def response_header_pairs(headers: httpx.Headers) -> list[tuple[bytes, bytes]]:
    raw = list(headers.raw)
    excluded = _excluded_headers(raw)
    return [(name, value) for name, value in raw if name.lower() not in excluded]


async def proxy_buffered(
    client: httpx.AsyncClient, method: str, url: str,
    headers: list[tuple[bytes, bytes]], content: bytes | None = None,
) -> Response:
    request = client.build_request(method, url, headers=headers, content=content)
    upstream = await client.send(request, stream=True)
    try:
        body = upstream.content if upstream.is_stream_consumed else b"".join(
            [chunk async for chunk in upstream.aiter_raw()]
        )
        response = Response(body, status_code=upstream.status_code)
        response.raw_headers = response_header_pairs(upstream.headers)
        return response
    finally:
        await upstream.aclose()


async def proxy_streaming(
    client: httpx.AsyncClient, method: str, url: str,
    headers: list[tuple[bytes, bytes]], content: bytes,
    on_complete: Callable[[bool], None] | None = None,
) -> StreamingResponse:
    request = client.build_request(method, url, headers=headers, content=content)
    upstream = await client.send(request, stream=True)

    async def chunks() -> AsyncIterator[bytes]:
        completed = False
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
            completed = True
        finally:
            await upstream.aclose()
            if on_complete:
                on_complete(completed)

    response = StreamingResponse(
        chunks(), status_code=upstream.status_code, media_type=None,
        background=BackgroundTask(upstream.aclose),
    )
    response.raw_headers = response_header_pairs(upstream.headers)
    return response


def collapse_chat_stream(text: str) -> dict[str, Any] | None:
    """Rebuild one Chat Completions payload from a streamed SSE body.

    Upstreams may answer with ``text/event-stream`` even when the client asked
    for a buffered response. Collapsing the deltas keeps that client on the
    OpenAI contract instead of leaking raw SSE frames. Returns ``None`` when
    the body holds no completion chunk.
    """
    content: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    last: dict[str, Any] = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        last = chunk
        choice = choices[0]
        delta = choice.get("delta") if isinstance(choice, dict) else None
        if not isinstance(delta, dict):
            continue
        piece = delta.get("content")
        if isinstance(piece, str):
            content.append(piece)
        for call in delta.get("tool_calls") or []:
            if isinstance(call, dict):
                _merge_tool_call(tool_calls, call)
    if not last:
        return None
    choices = last.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    payload = {key: value for key, value in last.items() if key != "choices"}
    payload["object"] = "chat.completion"
    payload["choices"] = [{
        "index": choice.get("index", 0),
        "message": message,
        "finish_reason": choice.get("finish_reason"),
    }]
    return payload


def _merge_tool_call(acc: dict[int, dict[str, Any]], delta: dict[str, Any]) -> None:
    index = int(delta.get("index") or 0)
    entry = acc.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    if delta.get("id"):
        entry["id"] = str(delta["id"])
    if delta.get("type"):
        entry["type"] = str(delta["type"])
    function = delta.get("function")
    if isinstance(function, dict):
        if function.get("name"):
            entry["function"]["name"] = str(function["name"])
        if isinstance(function.get("arguments"), str):
            entry["function"]["arguments"] += function["arguments"]


def _excluded_headers(headers: list[tuple[bytes, bytes]]) -> set[bytes]:
    excluded = set(STATIC_HOP_BY_HOP)
    for name, value in headers:
        if name.lower() == b"connection":
            excluded.update(token.strip().lower() for token in value.split(b",") if token.strip())
    return excluded
