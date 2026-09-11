"""OpenAI Responses API endpoint for Smart Router.

Translates ``POST /v1/responses`` requests to the upstream Chat Completions
API and translates the response back, so clients connected through the
``wire_api = "responses"`` protocol (Codex) are supported.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .proxy import proxy_buffered, _excluded_headers


def responses_to_chat(body: dict) -> dict:
    """Convert a Responses API request body to a Chat Completions body.

    Fields with no equivalent in the Chat API are silently dropped.
    ``instructions`` becomes a ``system``-role message prepended to
    ``messages``.
    """
    chat: dict = {}
    chat["model"] = body.get("model", "auto")
    chat["messages"] = _input_to_messages(body.get("input"), body.get("instructions", ""))
    chat["stream"] = body.get("stream", False)

    _mv(body, chat, "max_output_tokens", "max_tokens")
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty",
                 "seed", "tools", "tool_choice", "metadata", "n", "stop"):
        _mv(body, chat, key, key)

    reasoning = body.get("reasoning", {})
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        chat["reasoning_effort"] = reasoning["effort"]

    return chat


def chat_to_responses(chat_body: dict, upstream_id: str, model: str, usage: dict) -> dict:
    """Translate a Chat Completions response body to Responses API format."""
    output: list[dict] = []
    for choice in chat_body.get("choices", []):
        msg = choice.get("message", {})
        content_parts: list[dict] = []
        text = msg.get("content") or ""
        if text:
            content_parts.append({"type": "output_text", "text": text, "annotations": []})
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            content_parts.append({
                "type": "function_call",
                "id": tc.get("id"),
                "name": fn.get("name", ""),
                "arguments": fn.get("arguments", "{}"),
            })
        if not content_parts:
            content_parts.append({"type": "output_text", "text": "", "annotations": []})
        finish = choice.get("finish_reason", "stop")
        output.append({
            "type": "message",
            "role": msg.get("role", "assistant"),
            "content": content_parts,
            "status": "completed" if finish == "stop" else finish,
            "finish_reason": finish,
        })

    if not output:
        output.append({
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "", "annotations": []}],
            "status": "completed", "finish_reason": "stop",
        })

    resp_id = upstream_id if upstream_id.startswith("resp_") else f"resp_{upstream_id}"
    return {
        "id": resp_id,
        "object": "response",
        "created": int(time.time()),
        "model": model,
        "output": output,
        "usage": usage or {},
        "status": "completed",
    }


def _input_to_messages(input_data, instructions: str) -> list[dict]:
    """Translate Response API ``input`` + ``instructions`` to Chat ``messages``."""
    messages: list[dict] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if isinstance(input_data, str):
        messages.append({"role": "user", "content": input_data})
    elif isinstance(input_data, list):
        seen_roles = False
        for item in input_data:
            if not isinstance(item, dict):
                messages.append({"role": "user", "content": str(item)})
                continue
            role = item.get("role", "user")
            seen_roles = True
            content = item.get("content", "")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
            elif isinstance(content, list):
                texts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "input_text"
                ]
                texts = [t for t in texts if t]
                messages.append({"role": role, "content": " ".join(texts) if texts else ""})
        if not seen_roles and input_data:
            messages.append({"role": "user", "content": json.dumps(input_data, ensure_ascii=False)})
    else:
        messages.append({"role": "user", "content": str(input_data) if input_data is not None else ""})
    return messages


def _mv(src: dict, dst: dict, src_key: str, dst_key: str) -> None:
    """Move one field from *src* to *dst*, renaming if necessary."""
    if src_key in src:
        dst[dst_key] = src[src_key]


# ---------------------------------------------------------------------------
# SSE stream translation (Chat SSE -> Responses SSE)

def _process_chat_chunk(raw: bytes, state: dict) -> list[bytes]:
    """Parse one raw chunks from the upstream Chat SSE and emit Responses SSE events.

    *state* accumulates ``id``, ``model``, ``has_content``, and ``usage``
    across chunks.
    """
    out: list[bytes] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("data: "):
            payload = stripped[6:]
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                out.append(((line + "\n").encode("utf-8")))
                continue

            delta = (obj.get("choices") or [{}])[0]
            delta_dict = delta.get("delta", {}) if isinstance(delta, dict) else {}
            content = delta_dict.get("content", "") if isinstance(delta_dict, dict) else ""
            index = delta.get("index", 0) if isinstance(delta, dict) else 0
            finish_reason = delta.get("finish_reason") if isinstance(delta, dict) else None
            if obj.get("model"):
                state["model"] = obj["model"]
            if obj.get("id"):
                state["id"] = obj["id"]

            if content:
                state["has_content"] = True
                out.append(b"event: response.output_text.delta\n")
                out.append(json.dumps({"type": "output_text.delta", "delta": content, "index": index}).encode())
                out.append(b"\n\n")

            if finish_reason:
                state["finish_reason"] = finish_reason
                if state.get("has_content"):
                    out.append(b"event: response.output_text.done\n")
                    out.append(json.dumps({"type": "output_text.done", "index": index}).encode())
                    out.append(b"\n\n")

            usage = obj.get("usage")
            if usage:
                state["usage"] = usage

    return out


async def transform_stream(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: list[tuple[bytes, bytes]],
    content: bytes,
) -> StreamingResponse:
    """Send a request to the upstream Chat API and translate its SSE stream."""
    request = client.build_request(method, url, headers=headers, content=content)
    upstream = await client.send(request, stream=True)
    state: dict = {}

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for raw in upstream.aiter_raw():
                for ev in _process_chat_chunk(raw, state):
                    if ev:
                        yield ev
        finally:
            await upstream.aclose()

        # Emit the final response.completed event
        usage = state.get("usage") or {}
        model = state.get("model", "")
        upstream_id = state.get("id", "")
        resp_id = upstream_id if upstream_id.startswith("resp_") else f"resp_{upstream_id}"
        final = {
            "type": "response.completed",
            "response": {
                "id": resp_id,
                "object": "response",
                "created": int(time.time()),
                "model": model,
                "output": [],
                "usage": usage,
                "status": "completed",
            },
        }
        yield b"event: response.completed\n"
        yield json.dumps(final).encode()
        yield b"\n\n"
        yield b"data: [DONE]\n\n"

    resp = StreamingResponse(
        chunks(),
        status_code=upstream.status_code,
        media_type="text/event-stream",
        background=BackgroundTask(upstream.aclose),
    )
    raw_hdrs = list(upstream.headers.raw)
    excluded = _excluded_headers(raw_hdrs)
    resp.raw_headers = [(n, v) for n, v in raw_hdrs if n.lower() not in excluded]
    return resp


# ---------------------------------------------------------------------------
# Endpoint handler

async def handle(request: Request) -> Response:
    """``POST /v1/responses`` — OpenAI Responses API proxy.

    Authenticates like the Chat endpoint, delegates to the upstream Chat
    Completions API, and translates the response back to the Responses API
    format.  Streaming is supported and translated on-the-fly.
    """
    from .main import _client_auth_error, _bounded_body, _forward_headers, _openai_error
    from .config import Settings

    settings: Settings = request.app.state.settings
    auth_error = _client_auth_error(request, settings)
    if auth_error:
        return auth_error

    try:
        raw = await _bounded_body(request, settings.max_request_bytes)
    except ValueError:
        return _openai_error("request body too large", "request_too_large", 413)
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _openai_error("invalid JSON body", "invalid_json", 400)
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        return _openai_error("model is required", "invalid_model", 400)

    try:
        headers = _forward_headers(request, settings)
    except ValueError as error:
        return _openai_error(str(error), "duplicate_credential_header", 400)

    stream = body.get("stream", False)
    chat_body = responses_to_chat(body)
    outbound = json.dumps(chat_body, ensure_ascii=False, separators=(",", ":")).encode()
    url = settings.upstream_base_url.rstrip("/") + "/chat/completions"
    if request.scope["query_string"]:
        url += "?" + request.scope["query_string"].decode("ascii")

    if stream:
        return await transform_stream(
            request.app.state.client, "POST", url, headers, outbound,
        )
    # Non-streaming: proxy, translate back
    response = await proxy_buffered(
        request.app.state.client, "POST", url, headers, outbound,
    )
    if response.status_code >= 400:
        return response
    try:
        upstream_body = json.loads(response.body)
    except (json.JSONDecodeError, ValueError):
        return response
    responses_body = chat_to_responses(
        upstream_body,
        upstream_id=upstream_body.get("id", ""),
        model=upstream_body.get("model", ""),
        usage=upstream_body.get("usage"),
    )
    return JSONResponse(responses_body)
