"""OpenAI Responses API endpoint for Smart Router.

``POST /v1/responses`` accepts the wire format Codex uses, translates it to a
Chat Completions request, and translates the answer back: plain text,
streaming deltas, and function calling (``function_call`` output items plus
``function_call_output`` inputs) all round-trip. The request is handed to the
same routing/budget path as ``/v1/chat/completions``, so tier aliases such as
``auto`` keep working.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

RESPONSES_SSE = "text/event-stream"
DROP_HEADERS = {b"content-encoding", b"content-length", b"transfer-encoding"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _clean_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    return [(name, value) for name, value in headers if name.lower() not in DROP_HEADERS]


# ---------------------------------------------------------------------------
# Request translation (Responses -> Chat)


def _text_from_parts(content) -> str:
    """Join the text of one Responses content list or block."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return " ".join(parts)


def _image_part(part: dict) -> dict | None:
    raw = part.get("image_url")
    if isinstance(raw, dict):
        url = raw.get("url")
        detail = raw.get("detail")
    else:
        url = raw
        detail = part.get("detail")
    if not isinstance(url, str) or not url:
        return None
    image: dict = {"url": url}
    if isinstance(detail, str) and detail:
        image["detail"] = detail
    return {"type": "image_url", "image_url": image}


def _content_to_chat(content):
    """Chat message content for one Responses content value."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    texts: list[str] = []
    parts: list[dict] = []
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type") or "")
        if kind in {"input_text", "output_text", "text", "summary_text"}:
            text = part.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
                parts.append({"type": "text", "text": text})
        elif kind in {"input_image", "image_url", "image"}:
            image = _image_part(part)
            if image is not None:
                parts.append(image)
    if any(item["type"] == "image_url" for item in parts):
        return parts
    return "\n".join(texts)


def _instructions_to_text(instructions) -> str:
    if isinstance(instructions, str):
        return instructions.strip()
    return _text_from_parts(instructions).strip()


def _arguments_text(arguments) -> str:
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return "{}"
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _tool_call(item: dict) -> dict:
    call_id = item.get("call_id") or item.get("id") or _new_id("call")
    return {
        "id": str(call_id),
        "type": "function",
        "function": {
            "name": str(item.get("name") or ""),
            "arguments": _arguments_text(item.get("arguments")),
        },
    }


def _tool_output_text(output) -> str:
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    if isinstance(output, list):
        return _text_from_parts(output)
    try:
        return json.dumps(output, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(output)


def _input_to_messages(input_data, instructions: str) -> list[dict]:
    """Translate Responses ``input`` + ``instructions`` into Chat messages."""
    messages: list[dict] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    if input_data is None:
        return messages
    if isinstance(input_data, str):
        messages.append({"role": "user", "content": input_data})
        return messages
    if not isinstance(input_data, list):
        messages.append({"role": "user", "content": str(input_data)})
        return messages

    pending_tool_call = False
    for item in input_data:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            pending_tool_call = False
            continue
        if not isinstance(item, dict):
            messages.append({"role": "user", "content": str(item)})
            pending_tool_call = False
            continue
        kind = str(item.get("type") or "")
        if kind == "function_call":
            call = _tool_call(item)
            if pending_tool_call and messages:
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
                pending_tool_call = True
            continue
        if kind == "function_call_output":
            call_id = item.get("call_id") or item.get("id") or ""
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call_id),
                    "content": _tool_output_text(item.get("output")),
                }
            )
            pending_tool_call = False
            continue
        if kind in {"reasoning", "item_reference", "compaction"}:
            continue
        role = str(item.get("role") or "user")
        content = _content_to_chat(item.get("content"))
        if content == "" and str(item.get("type") or "") not in {"message", ""}:
            continue
        messages.append({"role": role, "content": content})
        pending_tool_call = False
    if not messages:
        messages.append({"role": "user", "content": ""})
    return messages


def _tools_to_chat(tools) -> list[dict]:
    """Responses tool definitions in Chat Completions shape.

    Server-side tools (``web_search``, ``file_search``, ``computer_use``) have
    no Chat Completions equivalent and are dropped.
    """
    converted: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if str(tool.get("type") or "") != "function":
            continue
        existing = tool.get("function")
        if isinstance(existing, dict) and existing.get("name"):
            converted.append({"type": "function", "function": dict(existing)})
            continue
        name = str(tool.get("name") or "")
        if not name:
            continue
        function: dict = {"name": name}
        if tool.get("description") is not None:
            function["description"] = str(tool.get("description") or "")
        parameters = tool.get("parameters")
        function["parameters"] = (
            parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        )
        if isinstance(tool.get("strict"), bool):
            function["strict"] = tool["strict"]
        converted.append({"type": "function", "function": function})
    return converted


def _tool_choice_to_chat(choice):
    if choice is None:
        return None
    if isinstance(choice, str):
        return choice
    if not isinstance(choice, dict):
        return None
    kind = str(choice.get("type") or "")
    if kind == "function":
        existing = choice.get("function")
        if isinstance(existing, dict) and existing.get("name"):
            return {"type": "function", "function": {"name": existing["name"]}}
        name = choice.get("name")
        if name:
            return {"type": "function", "function": {"name": name}}
    if kind in {"auto", "none", "required"}:
        return kind
    return None


def _text_format_to_response_format(text) -> dict | None:
    if not isinstance(text, dict):
        return None
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        return None
    kind = str(fmt.get("type") or "")
    if kind == "json_schema":
        schema: dict = {"name": str(fmt.get("name") or "response")}
        if isinstance(fmt.get("schema"), dict):
            schema["schema"] = fmt["schema"]
        if isinstance(fmt.get("strict"), bool):
            schema["strict"] = fmt["strict"]
        return {"type": "json_schema", "json_schema": schema}
    if kind == "json_object":
        return {"type": "json_object"}
    return None


def responses_to_chat(body: dict) -> dict:
    """Convert a Responses API request body to a Chat Completions body."""
    chat: dict = {
        "model": body.get("model", "auto"),
        "messages": _input_to_messages(
            body.get("input"), _instructions_to_text(body.get("instructions"))
        ),
        "stream": body.get("stream") is True,
    }
    if "max_output_tokens" in body:
        chat["max_tokens"] = body["max_output_tokens"]
    for key in (
        "temperature",
        "top_p",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "metadata",
        "n",
        "stop",
        "parallel_tool_calls",
    ):
        if key in body:
            chat[key] = body[key]

    tools = _tools_to_chat(body.get("tools"))
    if tools:
        chat["tools"] = tools
        choice = _tool_choice_to_chat(body.get("tool_choice"))
        if choice is not None:
            chat["tool_choice"] = choice

    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        chat["reasoning_effort"] = reasoning["effort"]

    response_format = _text_format_to_response_format(body.get("text"))
    if response_format is not None:
        chat["response_format"] = response_format
    return chat


# ---------------------------------------------------------------------------
# Response translation (Chat -> Responses)


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _text_from_parts(content)
    return ""


def _usage_to_responses(usage) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    details = usage.get("prompt_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": int(details.get("cached_tokens") or 0)},
        "output_tokens": output_tokens,
        "output_tokens_details": {
            "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0)
        },
        "total_tokens": total,
    }


def _finish_state(finish_reason: str) -> tuple[str, dict | None]:
    """Map a Chat finish reason to a response status and its details."""
    if finish_reason == "length":
        return "incomplete", {"reason": "max_output_tokens"}
    if finish_reason == "content_filter":
        return "incomplete", {"reason": "content_filter"}
    return "completed", None


def _message_item(text: str, item_id: str) -> dict:
    return {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_item(call_id: str, name: str, arguments: str, item_id: str) -> dict:
    return {
        "type": "function_call",
        "id": item_id,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
        "status": "completed",
    }


def _output_text(output: list[dict]) -> str:
    chunks = []
    for item in output:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks)


def chat_to_responses(chat_body: dict, requested_model: str) -> dict:
    """Translate one Chat Completions response into a Responses object."""
    choices = chat_body.get("choices") or []
    output: list[dict] = []
    status = "completed"
    incomplete: dict | None = None
    finish_reason = "stop"
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or {}
        finish_reason = str(choice.get("finish_reason") or "stop")
        status, incomplete = _finish_state(finish_reason)
        text = _message_text(message.get("content"))
        if text:
            output.append(_message_item(text, _new_id("msg")))
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            output.append(
                _function_item(
                    str(call.get("id") or _new_id("call")),
                    str(function.get("name") or ""),
                    _arguments_text(function.get("arguments")),
                    _new_id("fc"),
                )
            )
        if not text and not message.get("tool_calls"):
            output.append(_message_item("", _new_id("msg")))
    if not output:
        output.append(_message_item("", _new_id("msg")))

    upstream_id = str(chat_body.get("id") or "")
    response_id = upstream_id if upstream_id.startswith("resp_") else f"resp_{upstream_id or _new_id('r')}"
    payload = {
        "id": response_id,
        "object": "response",
        "created": int(chat_body.get("created") or time.time()),
        "model": str(requested_model or chat_body.get("model") or ""),
        "status": status,
        "output": output,
        "output_text": _output_text(output),
        "parallel_tool_calls": True,
        "usage": _usage_to_responses(chat_body.get("usage")),
    }
    if incomplete is not None:
        payload["incomplete_details"] = incomplete
    if finish_reason == "tool_calls":
        payload["status"] = "completed"
    return payload


# ---------------------------------------------------------------------------
# Streaming translation (Chat SSE -> Responses SSE)


class _StreamTranslator:
    """Turn one Chat Completions SSE stream into Responses API events."""

    def __init__(self, requested_model: str):
        self.requested_model = requested_model
        self.sequence = 0
        self.response_id = _new_id("resp")
        self.created = int(time.time())
        self.upstream_id = ""
        self.model = requested_model or ""
        self.finish_reason = "stop"
        self.usage: dict = {}
        self.events: list[bytes] = []
        self.message_item_id = _new_id("msg")
        self.message_output_index = 0
        self.message_started = False
        self.message_text = ""
        self.tool_items: dict[int, dict] = {}
        self.tool_order: list[int] = []
        self.output_index = 1
        self.created_sent = False
        self.completed = False

    # ------------------------------------------------------------- emitting

    def _emit(self, event_type: str, payload: dict) -> None:
        self.sequence += 1
        payload = dict(payload)
        payload["sequence_number"] = self.sequence
        payload["type"] = event_type
        self.events.append(f"event: {event_type}\n".encode())
        self.events.append(b"data: " + json.dumps(payload, ensure_ascii=False).encode())
        self.events.append(b"\n\n")

    def _response_stub(self, status: str) -> dict:
        return {
            "id": self.response_id,
            "object": "response",
            "created": self.created,
            "model": self.model,
            "status": status,
            "output": [],
            "usage": None,
        }

    def start(self) -> None:
        if self.created_sent:
            return
        self.created_sent = True
        self._emit("response.created", {"response": self._response_stub("in_progress")})
        self._emit("response.in_progress", {"response": self._response_stub("in_progress")})

    # ---------------------------------------------------------- text blocks

    def _open_message(self) -> None:
        if self.message_started:
            return
        self.message_started = True
        item = {
            "type": "message",
            "id": self.message_item_id,
            "role": "assistant",
            "status": "in_progress",
            "content": [],
        }
        self._emit(
            "response.output_item.added",
            {"output_index": self.message_output_index, "item": item},
        )
        self._emit(
            "response.content_part.added",
            {
                "item_id": self.message_item_id,
                "output_index": self.message_output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        )

    def add_text(self, delta: str) -> None:
        self.start()
        self._open_message()
        self.message_text += delta
        self._emit(
            "response.output_text.delta",
            {
                "item_id": self.message_item_id,
                "output_index": self.message_output_index,
                "content_index": 0,
                "delta": delta,
            },
        )

    # ----------------------------------------------------------- tool calls

    def _tool_entry(self, index: int, call_id: str, name: str) -> dict:
        entry = self.tool_items.get(index)
        if entry is None:
            entry = {
                "item_id": _new_id("fc"),
                "call_id": call_id or _new_id("call"),
                "name": name,
                "arguments": "",
                "output_index": self.output_index,
                "opened": False,
            }
            self.tool_items[index] = entry
            self.tool_order.append(index)
            self.output_index += 1
        else:
            if call_id:
                entry["call_id"] = call_id
            if name:
                entry["name"] = name
        return entry

    def add_tool_delta(self, index: int, call_id: str, name: str, arguments: str) -> None:
        self.start()
        entry = self._tool_entry(index, call_id, name)
        if not entry["opened"]:
            entry["opened"] = True
            self._emit(
                "response.output_item.added",
                {
                    "output_index": entry["output_index"],
                    "item": {
                        "type": "function_call",
                        "id": entry["item_id"],
                        "call_id": entry["call_id"],
                        "name": entry["name"],
                        "arguments": "",
                        "status": "in_progress",
                    },
                },
            )
        if not arguments:
            return
        entry["arguments"] += arguments
        self._emit(
            "response.function_call_arguments.delta",
            {
                "item_id": entry["item_id"],
                "output_index": entry["output_index"],
                "delta": arguments,
            },
        )

    def add_tool_call(self, call_id: str, name: str, arguments: str) -> None:
        """Record one complete tool call from a non-streaming style chunk."""
        self.start()
        index = len(self.tool_order)
        while index in self.tool_items:
            index += 1
        entry = self._tool_entry(index, call_id, name)
        if not entry["opened"]:
            entry["opened"] = True
            self._emit(
                "response.output_item.added",
                {
                    "output_index": entry["output_index"],
                    "item": {
                        "type": "function_call",
                        "id": entry["item_id"],
                        "call_id": entry["call_id"],
                        "name": entry["name"],
                        "arguments": "",
                        "status": "in_progress",
                    },
                },
            )
        for chunk in _argument_chunks(arguments):
            entry["arguments"] += chunk
            self._emit(
                "response.function_call_arguments.delta",
                {
                    "item_id": entry["item_id"],
                    "output_index": entry["output_index"],
                    "delta": chunk,
                },
            )

    # ------------------------------------------------------------ finishing

    def _finish_tools(self) -> list[dict]:
        items: list[dict] = []
        for index in self.tool_order:
            entry = self.tool_items[index]
            self._emit(
                "response.function_call_arguments.done",
                {
                    "item_id": entry["item_id"],
                    "output_index": entry["output_index"],
                    "arguments": entry["arguments"],
                },
            )
            item = _function_item(
                entry["call_id"], entry["name"], entry["arguments"], entry["item_id"]
            )
            self._emit(
                "response.output_item.done",
                {"output_index": entry["output_index"], "item": item},
            )
            items.append(item)
        return items

    def _finish_message(self) -> list[dict]:
        if not self.message_started:
            return []
        self._emit(
            "response.output_text.done",
            {
                "item_id": self.message_item_id,
                "output_index": self.message_output_index,
                "content_index": 0,
                "text": self.message_text,
            },
        )
        self._emit(
            "response.content_part.done",
            {
                "item_id": self.message_item_id,
                "output_index": self.message_output_index,
                "content_index": 0,
                "part": {
                    "type": "output_text",
                    "text": self.message_text,
                    "annotations": [],
                },
            },
        )
        item = _message_item(self.message_text, self.message_item_id)
        self._emit(
            "response.output_item.done",
            {"output_index": self.message_output_index, "item": item},
        )
        return [item]

    def finish(self) -> None:
        if self.completed:
            return
        self.start()
        output = self._finish_message() + self._finish_tools()
        status, incomplete = _finish_state(self.finish_reason)
        payload = {
            "id": self.response_id,
            "object": "response",
            "created": self.created,
            "model": self.model,
            "status": status,
            "output": output,
            "output_text": _output_text(output),
            "usage": _usage_to_responses(self.usage),
        }
        if incomplete is not None:
            payload["incomplete_details"] = incomplete
        self._emit("response.completed", {"response": payload})
        self.completed = True

    def fail(self, message: str) -> None:
        """Report a broken upstream stream as ``response.failed``.

        Emitting ``response.completed`` here would look like a successful turn
        with truncated output, so the failure gets its own terminal event.
        """
        if self.completed:
            return
        payload = self._response_stub("failed")
        payload["output"] = self._finish_message() + self._finish_tools()
        payload["error"] = {"code": "server_error", "message": message}
        self._emit("response.failed", {"response": payload})
        self.completed = True

    # -------------------------------------------------------------- feeding

    def feed(self, chunk: bytes) -> list[bytes]:
        text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)
        self.events = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(":"):
                continue
            if not stripped.startswith("data:"):
                continue
            payload = stripped[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            self._consume(obj)
        return self.events

    def _consume(self, obj: dict) -> None:
        if obj.get("id") and not self.upstream_id:
            self.upstream_id = str(obj["id"])
            if not self.upstream_id.startswith("resp_"):
                self.response_id = f"resp_{self.upstream_id}"
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
        elif isinstance(content, list):
            text = _text_from_parts(content)
            if text:
                self.add_text(text)
        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            index = int(call.get("index") or 0)
            self.add_tool_delta(
                index,
                str(call.get("id") or ""),
                str(function.get("name") or ""),
                str(function.get("arguments") or ""),
            )
        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])


def _argument_chunks(arguments: str, size: int = 256) -> list[str]:
    if not arguments:
        return []
    return [arguments[index : index + size] for index in range(0, len(arguments), size)]


def _wrap_stream(response: StreamingResponse, requested_model: str) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        translator = _StreamTranslator(requested_model)
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

    wrapped = StreamingResponse(body(), status_code=response.status_code, media_type=RESPONSES_SSE)
    wrapped.raw_headers = _clean_headers(list(response.raw_headers))
    return wrapped


class ResponsesTransform:
    """Adapter passed to the shared chat routing path."""

    def __init__(self, requested_model: str):
        self.requested_model = requested_model

    async def __call__(self, response: Response) -> Response:
        content_type = response.headers.get("content-type", "")
        if content_type.startswith(RESPONSES_SSE):
            return _wrap_stream(response, self.requested_model)
        if response.status_code >= 400:
            return response
        try:
            payload = json.loads(response.body.decode("utf-8", "replace"))
        except (ValueError, AttributeError):
            return response
        if not isinstance(payload, dict) or "choices" not in payload:
            return response
        return JSONResponse(
            chat_to_responses(payload, self.requested_model),
            status_code=response.status_code,
        )


# ---------------------------------------------------------------------------
# Endpoint handler


async def handle(request: Request) -> Response:
    """``POST /v1/responses`` - Responses API for Codex and similar clients."""
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
    # The stream is parsed here, so the upstream must not compress it.
    headers = [(n, v) for n, v in headers if n.lower() != b"accept-encoding"]
    headers.append((b"accept-encoding", b"identity"))

    chat_body = responses_to_chat(body)
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
        transform=ResponsesTransform(str(body.get("model") or "")),
        request_kind="responses",
    )
