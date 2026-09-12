"""Anthropic Messages API endpoint for Smart Router.

``POST /v1/messages`` lets Claude Code (and any other Anthropic-compatible
client) use the router as if it were the Anthropic API: the request is
translated to Chat Completions, routed through the same tier/budget path as the
other endpoints, and the answer is translated back to the Messages format,
including streaming and tool use.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import AsyncIterator

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

MESSAGES_SSE = "text/event-stream"
DROP_HEADERS = {b"content-encoding", b"content-length", b"transfer-encoding"}
CLIENT_ONLY_HEADERS = {b"anthropic-version", b"anthropic-beta", b"x-app"}
CLIENT_ONLY_PREFIXES = (b"x-stainless-", b"anthropic-")
ESTIMATED_CHARS_PER_TOKEN = 4
BILLING_HEADER_PREFIX = "x-anthropic-billing-header:"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _clean_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    return [(name, value) for name, value in headers if name.lower() not in DROP_HEADERS]


def _strip_client_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Drop the headers that belong to the Anthropic SDK, not to the backend.

    ``anthropic-version``/``anthropic-beta`` and the whole ``x-stainless-*``
    family describe the caller's SDK; the upstream Chat Completions backend has
    no use for them.
    """
    return [
        (name, value)
        for name, value in headers
        if name.lower() not in CLIENT_ONLY_HEADERS
        and not name.lower().startswith(CLIENT_ONLY_PREFIXES)
    ]


def _text_from_blocks(blocks) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n\n".join(part for part in parts if part)


def _system_to_text(system) -> str:
    """Join the system blocks, dropping the client billing header line.

    Claude Code prefixes its system prompt with an ``x-anthropic-billing-header``
    line that the Anthropic API strips before inference. Forwarding it would
    push that bookkeeping into the upstream model's system prompt.
    """
    text = _text_from_blocks(system).strip()
    if text.startswith(BILLING_HEADER_PREFIX):
        _, _, remainder = text.partition("\n")
        text = remainder.strip()
    return text


def _image_block(block: dict) -> dict | None:
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if str(source.get("type") or "") == "base64":
        media_type = str(source.get("media_type") or "image/png")
        data = str(source.get("data") or "")
        if not data:
            return None
        return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}
    if str(source.get("type") or "") == "url":
        url = str(source.get("url") or "")
        if not url:
            return None
        return {"type": "image_url", "image_url": {"url": url}}
    return None


def _tool_result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") == "image":
                    parts.append("[image]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(part for part in parts if part)
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _content_to_messages(role: str, content) -> list[dict]:
    """Translate one Anthropic message into one or more Chat messages."""
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list):
        return [{"role": role, "content": "" if content is None else str(content)}]

    messages: list[dict] = []
    parts: list[dict] = []
    texts: list[str] = []
    tool_calls: list[dict] = []

    def flush() -> None:
        nonlocal parts, texts, tool_calls
        if role == "assistant":
            if tool_calls or texts:
                messages.append(
                    {
                        "role": "assistant",
                        "content": "\n".join(texts) if texts else None,
                        **({"tool_calls": tool_calls} if tool_calls else {}),
                    }
                )
        elif parts or texts:
            has_image = any(item["type"] == "image_url" for item in parts)
            messages.append(
                {"role": role, "content": parts if has_image else "\n".join(texts)}
            )
        parts, texts, tool_calls = [], [], []

    for block in content:
        if isinstance(block, str):
            texts.append(block)
            parts.append({"type": "text", "text": block})
            continue
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        if kind == "text":
            text = str(block.get("text") or "")
            if text:
                texts.append(text)
                parts.append({"type": "text", "text": text})
        elif kind == "image":
            image = _image_block(block)
            if image is not None:
                parts.append(image)
        elif kind == "tool_use":
            tool_calls.append(
                {
                    "id": str(block.get("id") or _new_id("call")),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
        elif kind == "tool_result":
            flush()
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or ""),
                    "content": _tool_result_text(block),
                }
            )
        elif kind in {"thinking", "redacted_thinking"}:
            continue
    flush()
    if not messages:
        messages.append({"role": role, "content": ""})
    return messages


def _tools_to_chat(tools) -> list[dict]:
    converted: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        if not name:
            continue
        function: dict = {"name": name}
        if tool.get("description") is not None:
            function["description"] = str(tool.get("description") or "")
        schema = tool.get("input_schema")
        function["parameters"] = (
            schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
        )
        converted.append({"type": "function", "function": function})
    return converted


def _tool_choice_to_chat(choice):
    if not isinstance(choice, dict):
        return None
    kind = str(choice.get("type") or "")
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool" and choice.get("name"):
        return {"type": "function", "function": {"name": str(choice["name"])}}
    return None


def messages_to_chat(body: dict) -> dict:
    """Convert an Anthropic Messages request body to Chat Completions."""
    messages: list[dict] = []
    system = _system_to_text(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    raw_messages = body.get("messages")
    if isinstance(raw_messages, list):
        for message in raw_messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            messages.extend(_content_to_messages(role, message.get("content")))
    if not messages:
        messages.append({"role": "user", "content": ""})

    chat: dict = {
        "model": body.get("model", "auto"),
        "messages": messages,
        "stream": body.get("stream") is True,
    }
    if "max_tokens" in body:
        chat["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p", "metadata"):
        if key in body and body[key] is not None:
            chat[key] = body[key]
    if isinstance(body.get("stop_sequences"), list):
        chat["stop"] = [str(item) for item in body["stop_sequences"]]

    tools = _tools_to_chat(body.get("tools"))
    if tools:
        chat["tools"] = tools
        choice = _tool_choice_to_chat(body.get("tool_choice"))
        if choice is not None:
            chat["tool_choice"] = choice
        disable_parallel = (body.get("tool_choice") or {}).get("disable_parallel_tool_use")
        if disable_parallel is True:
            chat["parallel_tool_calls"] = False
    return chat


STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def _usage_to_anthropic(usage) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    payload = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    if cached:
        payload["cache_read_input_tokens"] = cached
    return payload


def chat_to_anthropic(chat_body: dict, requested_model: str) -> dict:
    """Translate one Chat Completions answer into an Anthropic message."""
    content: list[dict] = []
    stop_reason = "end_turn"
    for choice in chat_body.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        finish = str(choice.get("finish_reason") or "stop")
        stop_reason = STOP_REASONS.get(finish, "end_turn")
        text = message.get("content")
        if isinstance(text, list):
            text = "".join(
                str(part.get("text") or "") for part in text if isinstance(part, dict)
            )
        if text:
            content.append({"type": "text", "text": str(text)})
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_arguments = function.get("arguments")
            try:
                parsed = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except ValueError:
                parsed = {"raw": raw_arguments}
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
            content.append(
                {
                    "type": "tool_use",
                    "id": str(call.get("id") or _new_id("toolu")),
                    "name": str(function.get("name") or ""),
                    "input": parsed,
                }
            )
    if not content:
        content.append({"type": "text", "text": ""})

    upstream_id = str(chat_body.get("id") or "")
    message_id = upstream_id if upstream_id.startswith("msg_") else f"msg_{upstream_id or _new_id('m')}"
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": str(requested_model or chat_body.get("model") or ""),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": _usage_to_anthropic(chat_body.get("usage")),
    }


# ---------------------------------------------------------------------------
# Streaming translation (Chat SSE -> Anthropic SSE)


class _MessageTranslator:
    """Emit Anthropic ``message_start`` ... ``message_stop`` for one stream."""

    def __init__(self, requested_model: str, input_tokens: int = 0):
        self.requested_model = requested_model
        self.message_id = _new_id("msg")
        self.model = requested_model or ""
        # The upstream usage only arrives with the final chunk, so the opening
        # event carries a local estimate; clients show it as the context size.
        self.input_tokens = max(0, int(input_tokens or 0))
        self.events: list[bytes] = []
        self.started = False
        self.stopped = False
        self.usage: dict = {}
        self.stop_reason = "end_turn"
        self.text_block_index: int | None = None
        self.text = ""
        self.tool_blocks: dict[int, dict] = {}
        self.tool_order: list[int] = []
        self.next_block_index = 0

    def _emit(self, event_type: str, payload: dict) -> None:
        payload = dict(payload)
        payload["type"] = event_type
        self.events.append(f"event: {event_type}\n".encode())
        self.events.append(b"data: " + json.dumps(payload, ensure_ascii=False).encode())
        self.events.append(b"\n\n")

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self._emit(
            "message_start",
            {
                "message": {
                    "id": self.message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": self.input_tokens, "output_tokens": 0},
                }
            },
        )

    def _open_text(self) -> None:
        if self.text_block_index is not None:
            return
        self.text_block_index = self.next_block_index
        self.next_block_index += 1
        self._emit(
            "content_block_start",
            {
                "index": self.text_block_index,
                "content_block": {"type": "text", "text": ""},
            },
        )

    def add_text(self, delta: str) -> None:
        self.start()
        self._open_text()
        self.text += delta
        self._emit(
            "content_block_delta",
            {"index": self.text_block_index, "delta": {"type": "text_delta", "text": delta}},
        )

    def add_tool_delta(self, chat_index: int, call_id: str, name: str, arguments: str) -> None:
        self.start()
        entry = self.tool_blocks.get(chat_index)
        if entry is None:
            entry = {
                "index": self.next_block_index,
                "id": call_id or _new_id("toolu"),
                "name": name,
                "arguments": "",
                "opened": False,
            }
            self.next_block_index += 1
            self.tool_blocks[chat_index] = entry
            self.tool_order.append(chat_index)
        else:
            if call_id:
                entry["id"] = call_id
            if name:
                entry["name"] = name
        if not entry["opened"]:
            entry["opened"] = True
            self._emit(
                "content_block_start",
                {
                    "index": entry["index"],
                    "content_block": {
                        "type": "tool_use",
                        "id": entry["id"],
                        "name": entry["name"],
                        "input": {},
                    },
                },
            )
        if arguments:
            entry["arguments"] += arguments
            self._emit(
                "content_block_delta",
                {
                    "index": entry["index"],
                    "delta": {"type": "input_json_delta", "partial_json": arguments},
                },
            )

    def _finish_blocks(self) -> None:
        if self.text_block_index is not None:
            self._emit("content_block_stop", {"index": self.text_block_index})
        for chat_index in self.tool_order:
            entry = self.tool_blocks[chat_index]
            if not entry["opened"]:
                self._emit(
                    "content_block_start",
                    {
                        "index": entry["index"],
                        "content_block": {
                            "type": "tool_use",
                            "id": entry["id"],
                            "name": entry["name"],
                            "input": {},
                        },
                    },
                )
            self._emit("content_block_stop", {"index": entry["index"]})

    def finish(self) -> None:
        if self.stopped:
            return
        self.start()
        self._finish_blocks()
        usage = _usage_to_anthropic(self.usage)
        self._emit(
            "message_delta",
            {
                "delta": {"stop_reason": self.stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": usage["output_tokens"]},
            },
        )
        self._emit("message_stop", {})
        self.stopped = True

    def fail(self, message: str) -> None:
        """Terminate a broken stream the way the Messages API does.

        An error event ends the stream; a following ``message_stop`` would let
        the client treat the truncated answer as a complete one.
        """
        if self.stopped:
            return
        self._emit("error", {"error": {"type": "api_error", "message": message}})
        self.stopped = True

    def feed(self, chunk: bytes) -> list[bytes]:
        text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)
        self.events = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(":") or not stripped.startswith("data:"):
                continue
            payload = stripped[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            if isinstance(obj, dict):
                self._consume(obj)
        return self.events

    def _consume(self, obj: dict) -> None:
        if obj.get("model"):
            self.model = str(obj["model"])
        if isinstance(obj.get("usage"), dict) and obj["usage"]:
            self.usage = obj["usage"]
        choices = obj.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.add_text(content)
        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            self.add_tool_delta(
                int(call.get("index") or 0),
                str(call.get("id") or ""),
                str(function.get("name") or ""),
                str(function.get("arguments") or ""),
            )
        if choice.get("finish_reason"):
            self.stop_reason = STOP_REASONS.get(str(choice["finish_reason"]), "end_turn")


def _wrap_stream(
    response: StreamingResponse, requested_model: str, input_tokens: int = 0
) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        translator = _MessageTranslator(requested_model, input_tokens)
        translator.start()
        for event in translator.events:
            yield event
        try:
            async for chunk in response.body_iterator:
                for event in translator.feed(chunk):
                    yield event
        except Exception as error:  # noqa: BLE001 - the client still needs an ending
            translator.fail(f"{type(error).__name__}: {error}")
            for event in translator.events:
                yield event
            return
        translator.finish()
        for event in translator.events:
            yield event

    wrapped = StreamingResponse(body(), status_code=response.status_code, media_type=MESSAGES_SSE)
    wrapped.raw_headers = _clean_headers(list(response.raw_headers))
    return wrapped


class MessagesTransform:
    """Adapter passed to the shared chat routing path."""

    def __init__(self, requested_model: str, input_tokens: int = 0):
        self.requested_model = requested_model
        self.input_tokens = input_tokens

    async def __call__(self, response: Response) -> Response:
        content_type = response.headers.get("content-type", "")
        if content_type.startswith(MESSAGES_SSE):
            return _wrap_stream(response, self.requested_model, self.input_tokens)
        if response.status_code >= 400:
            return response
        try:
            payload = json.loads(response.body.decode("utf-8", "replace"))
        except (ValueError, AttributeError):
            return response
        if not isinstance(payload, dict) or "choices" not in payload:
            return response
        return JSONResponse(
            chat_to_anthropic(payload, self.requested_model),
            status_code=response.status_code,
        )


def estimate_tokens(body: dict) -> int:
    """Rough Claude-compatible token estimate for ``/v1/messages/count_tokens``."""
    chunks: list[str] = [_system_to_text(body.get("system"))]
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if isinstance(block.get("text"), str):
                    chunks.append(block["text"])
                elif block.get("type") == "tool_use":
                    chunks.append(json.dumps(block.get("input") or {}, ensure_ascii=False))
                elif block.get("type") == "tool_result":
                    chunks.append(_tool_result_text(block))
    for tool in body.get("tools") or []:
        if isinstance(tool, dict):
            chunks.append(json.dumps(tool, ensure_ascii=False))
    characters = sum(len(chunk) for chunk in chunks)
    return max(1, math.ceil(characters / ESTIMATED_CHARS_PER_TOKEN))


# ---------------------------------------------------------------------------
# Endpoint handlers


async def count_tokens(request: Request) -> Response:
    """``POST /v1/messages/count_tokens`` - local context estimate."""
    from .main import _bounded_body, _client_auth_error, _openai_error
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
    if not isinstance(body, dict):
        return _openai_error("invalid JSON body", "invalid_json", 400)
    return JSONResponse({"input_tokens": estimate_tokens(body)})


async def handle(request: Request) -> Response:
    """``POST /v1/messages`` - Anthropic Messages API for Claude Code."""
    from .main import _bounded_body, _client_auth_error, _forward_headers, _openai_error
    from .config import Settings

    settings: Settings = request.app.state.settings
    auth_error = _client_auth_error(request, settings)
    if auth_error:
        return auth_error
    started = time.monotonic()
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
    headers = _strip_client_headers(headers)
    headers = [(name, value) for name, value in headers if name.lower() != b"accept-encoding"]
    headers.append((b"accept-encoding", b"identity"))

    chat_body = messages_to_chat(body)
    outbound = json.dumps(chat_body, ensure_ascii=False, separators=(",", ":")).encode()
    url = settings.upstream_base_url.rstrip("/") + "/chat/completions"
    route = request.app.state.route_chat
    return await route(
        request,
        chat_body,
        outbound,
        headers,
        url,
        started,
        transform=MessagesTransform(
            str(body.get("model") or ""), estimate_tokens(body)
        ),
        request_kind="messages",
    )
