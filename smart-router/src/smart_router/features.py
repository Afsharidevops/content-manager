from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

FEATURE_SCHEMA_VERSION = 1

FEATURE_NAMES = (
    "input_tokens",
    "character_count",
    "message_count",
    "user_message_count",
    "assistant_message_count",
    "system_message_count",
    "conversation_depth",
    "max_message_length",
    "system_prompt_chars",
    "has_tools",
    "tool_count",
    "has_tool_choice",
    "has_vision",
    "structured_output",
    "requested_output_tokens",
    "context_utilization",
    "has_code",
    "code_block_count",
    "has_stack_trace",
    "has_json_like",
    "has_table_like",
    "question_count",
    "url_count",
    "file_count",
    "long_form_cue",
    "reasoning_cue",
    "summarization_cue",
    "translation_cue",
    "extraction_cue",
)


@dataclass(frozen=True)
class SafeFeatures:
    input_tokens: int
    character_count: int
    message_count: int
    user_message_count: int
    assistant_message_count: int
    system_message_count: int
    conversation_depth: int
    max_message_length: int
    system_prompt_chars: int
    has_tools: int
    tool_count: int
    has_tool_choice: int
    has_vision: int
    structured_output: int
    requested_output_tokens: int
    context_utilization: float
    has_code: int
    code_block_count: int
    has_stack_trace: int
    has_json_like: int
    has_table_like: int
    question_count: int
    url_count: int
    file_count: int
    long_form_cue: int
    reasoning_cue: int
    summarization_cue: int
    translation_cue: int
    extraction_cue: int

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)

    def as_row(self) -> list[float]:
        values = self.as_dict()
        return [float(values[name]) for name in FEATURE_NAMES]


LONG_FORM_CUES = (
    "write a report",
    "write an essay",
    "comprehensive",
    "detailed analysis",
    "long form",
)
REASONING_CUES = (
    "reason",
    "analyze",
    "architecture",
    "root cause",
    "debug",
    "prove",
    "calculate",
    "migration",
    "refactor",
)
SUMMARY_CUES = ("summarize", "summary", "tl;dr")
TRANSLATION_CUES = ("translate", "translation")
EXTRACTION_CUES = ("extract", "classify", "categorize", "parse into")
STACK_TRACE_PATTERNS = ("traceback (most recent call last)", "exception:", " at ")


def extract_safe_features(
    body: dict[str, Any], *, strongest_context: int = 200_000
) -> SafeFeatures:
    messages = body.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    texts: list[str] = []
    chars = 0
    system_chars = 0
    max_message_length = 0
    user_count = assistant_count = system_count = 0
    has_vision = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", ""))
        if role == "user":
            user_count += 1
        elif role == "assistant":
            assistant_count += 1
        elif role == "system":
            system_count += 1
        content = message.get("content", "")
        serialized = _stable_text(content)
        texts.append(serialized)
        n = len(serialized)
        chars += n
        max_message_length = max(max_message_length, n)
        if role == "system":
            system_chars += n
        has_vision = has_vision or _contains_image(content)

    tools = body.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    tool_text = json.dumps(tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    full_text = "\n".join(texts)
    lower = full_text.lower()
    code_blocks = len(re.findall(r"```.*?```", full_text, re.S))
    code_chars = sum(len(block) for block in re.findall(r"```.*?```", full_text, re.S))
    input_tokens = max(1, (chars - code_chars) // 4 + code_chars // 3 + len(tool_text) * 2 // 7)
    response_format = body.get("response_format")
    structured = bool(
        isinstance(response_format, dict)
        and response_format.get("type") in {"json_object", "json_schema"}
    )
    requested_output = _requested_output_tokens(body)
    file_count = len(set(re.findall(r"(?:^|\s)(?:[\w.-]+/)+[\w.-]+", lower, re.M)))
    return SafeFeatures(
        input_tokens=input_tokens,
        character_count=chars,
        message_count=len(messages),
        user_message_count=user_count,
        assistant_message_count=assistant_count,
        system_message_count=system_count,
        conversation_depth=max(user_count, assistant_count),
        max_message_length=max_message_length,
        system_prompt_chars=system_chars,
        has_tools=int(bool(tools)),
        tool_count=len(tools),
        has_tool_choice=int(body.get("tool_choice") not in (None, "auto")),
        has_vision=int(has_vision),
        structured_output=int(structured),
        requested_output_tokens=requested_output,
        context_utilization=min(2.0, (input_tokens + requested_output) / max(1, strongest_context)),
        has_code=int(code_blocks > 0),
        code_block_count=code_blocks,
        has_stack_trace=int(any(term in lower for term in STACK_TRACE_PATTERNS)),
        has_json_like=int(bool(re.search(r"\{\s*[\"'][^\n]+?:", full_text))),
        has_table_like=int(bool(re.search(r"(?m)^\s*\|.+\|\s*$", full_text))),
        question_count=full_text.count("?"),
        url_count=len(re.findall(r"https?://[^\s)\]>]+", full_text, re.I)),
        file_count=file_count,
        long_form_cue=int(any(term in lower for term in LONG_FORM_CUES)),
        reasoning_cue=int(any(term in lower for term in REASONING_CUES)),
        summarization_cue=int(any(term in lower for term in SUMMARY_CUES)),
        translation_cue=int(any(term in lower for term in TRANSLATION_CUES)),
        extraction_cue=int(any(term in lower for term in EXTRACTION_CUES)),
    )


def validate_feature_mapping(
    mapping: dict[str, Any], *, allow_extra: bool = False
) -> list[float]:
    if not isinstance(mapping, dict):
        raise ValueError("features must be an object")
    missing = [name for name in FEATURE_NAMES if name not in mapping]
    if missing:
        raise ValueError(f"missing feature keys: {', '.join(missing)}")
    if not allow_extra:
        extra = sorted(set(mapping) - set(FEATURE_NAMES))
        if extra:
            raise ValueError(f"unknown feature keys: {', '.join(extra)}")
    row: list[float] = []
    for name in FEATURE_NAMES:
        value = mapping[name]
        if not isinstance(value, (int, float, bool)):
            raise ValueError(f"feature {name} must be numeric")
        row.append(float(value))
    return row


def _requested_output_tokens(body: dict[str, Any]) -> int:
    values = []
    for field in ("max_completion_tokens", "max_tokens"):
        value = body.get(field)
        if isinstance(value, int) and value >= 0:
            values.append(value)
    return min(values) if values else 0


def _contains_image(value: Any) -> bool:
    if isinstance(value, list):
        return any(_contains_image(item) for item in value)
    if isinstance(value, dict):
        kind = str(value.get("type", "")).lower()
        mime = str(value.get("mime_type", value.get("media_type", ""))).lower()
        return (
            kind in {"image", "image_url", "input_image"}
            or mime.startswith("image/")
            or any(_contains_image(child) for child in value.values())
        )
    return False


def _stable_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
