from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .features import FEATURE_SCHEMA_VERSION, SafeFeatures, extract_safe_features
from .learned.model import LearnedPolicy, load_learned_policy
from .metrics import FAIL_OPEN, LEARNED_INFERENCE_SECONDS

AUTO_ALIASES = {
    "auto": None,
    "auto-fast": "fast",
    "auto-standard": "standard",
    "auto-strong": "strong",
}
TIER_RANK = {"fast": 0, "standard": 1, "strong": 2}
COMPLEX_TERMS = (
    "architecture",
    "security review",
    "root cause",
    "production incident",
    "multi-file",
    "migration",
    "refactor",
    "race condition",
    "performance investigation",
    "threat model",
)
SIMPLE_TERMS = (
    "translate",
    "grammar",
    "rewrite",
    "reformat",
    "extract",
    "reply briefly",
    "summarize briefly",
)
FAILURE_TERMS = ("failed", "failure", "didn't work", "did not work", "try again", "error")

DEFAULT_CALIBRATION = {
    "weights": {
        "context_gt_8000": 2,
        "context_gt_25000": 2,
        "context_gt_50000": 3,
        "tools_present": 1,
        "large_tool_schema": 1,
        "large_tool_results": 1,
        "multiple_code_blocks": 1,
        "multiple_files": 2,
        "structured_output": 1,
        "complex_intent": 2,
        "simple_intent": -2,
        "failure_language": 2,
    },
    "fast_max_score": 0,
    "standard_max_score": 5,
}


@dataclass(frozen=True)
class RequestFacts:
    estimated_message_tokens: int
    estimated_tool_schema_tokens: int
    estimated_tool_result_tokens: int
    estimated_total_tokens: int
    has_tools: bool
    has_vision: bool
    structured_output: bool
    code_blocks: int
    referenced_files: int
    requested_output_tokens: int
    text: str


@dataclass(frozen=True)
class Decision:
    proposed_tier: str
    proposed_model: str
    score: int
    reasons: tuple[str, ...]
    facts: RequestFacts
    policy: str = "heuristic"
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    learned_raw_tier: str | None = None
    policy_fallback: str | None = None
    capability_upgrade: str | None = None
    feature_schema_version: int = FEATURE_SCHEMA_VERSION
    safe_features: SafeFeatures | None = None


@dataclass(frozen=True)
class PolicyRuntime:
    calibration: dict[str, Any]
    learned: LearnedPolicy | None
    learned_load_error: str | None = None


def build_policy_runtime(settings: Settings) -> PolicyRuntime:
    calibration = _load_calibration(settings.calibration_file)
    learned: LearnedPolicy | None = None
    learned_error: str | None = None
    # v0.5.6 can switch policy from the Operations Center without a process
    # restart. Pre-load an available learned artifact even when the initial
    # environment policy is heuristic/calibrated, so a later UI switch is
    # immediately effective. Missing artifacts remain harmless unless learned
    # mode is actually selected.
    learned_files_present = (
        Path(settings.learned_model_file).is_file()
        and Path(settings.learned_metadata_file).is_file()
    )
    if settings.policy == "learned" or learned_files_present:
        try:
            learned = load_learned_policy(
                settings.learned_model_file,
                settings.learned_metadata_file,
                min_confidence=settings.learned_min_confidence,
                fallback_tier=settings.learned_fallback,
            )
        except Exception as exc:  # fail-open is deliberate at startup
            learned_error = type(exc).__name__
            if settings.policy == "learned":
                FAIL_OPEN.labels(f"learned_load_{learned_error}").inc()
    return PolicyRuntime(calibration, learned, learned_error)


def analyze_request(body: dict[str, Any]) -> RequestFacts:
    messages = body.get("messages") or []
    text_parts: list[str] = []
    non_tool_parts: list[str] = []
    message_chars = 0
    tool_result_chars = 0
    has_vision = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content", "")
        serialized = _stable_text(content)
        if message.get("role") == "tool":
            tool_result_chars += len(serialized)
        else:
            message_chars += len(serialized)
            non_tool_parts.append(serialized)
        text_parts.append(serialized)
        has_vision = has_vision or _contains_image(content)
    tools = body.get("tools") or []
    tool_chars = len(json.dumps(tools, sort_keys=True, separators=(",", ":")))
    text = "\n".join(text_parts).lower()
    non_tool_text = "\n".join(non_tool_parts).lower()
    code_chars = sum(len(block) for block in re.findall(r"```.*?```", non_tool_text, re.S))
    message_tokens = max(1, (message_chars - code_chars) // 4 + code_chars // 3)
    tool_schema_tokens = tool_chars * 2 // 7
    tool_result_tokens = tool_result_chars // 3
    total = message_tokens + tool_schema_tokens + tool_result_tokens
    referenced_files = len(
        set(re.findall(r"(?:^|\s)(?:[\w.-]+/)+[\w.-]+", text, re.M))
    )
    response_format = body.get("response_format")
    structured = bool(
        response_format
        and isinstance(response_format, dict)
        and response_format.get("type") in {"json_object", "json_schema"}
    )
    requested_outputs = [
        int(body[field])
        for field in ("max_completion_tokens", "max_tokens")
        if isinstance(body.get(field), int) and int(body[field]) >= 0
    ]
    return RequestFacts(
        estimated_message_tokens=message_tokens,
        estimated_tool_schema_tokens=tool_schema_tokens,
        estimated_tool_result_tokens=tool_result_tokens,
        estimated_total_tokens=total,
        has_tools=bool(tools),
        has_vision=has_vision,
        structured_output=structured,
        code_blocks=text.count("```") // 2,
        referenced_files=referenced_files,
        requested_output_tokens=min(requested_outputs) if requested_outputs else 0,
        text=text,
    )


def decide(
    body: dict[str, Any],
    settings: Settings,
    requested_tier: str | None = None,
    runtime: PolicyRuntime | None = None,
) -> Decision:
    facts = analyze_request(body)
    safe_features = extract_safe_features(
        body, strongest_context=settings.strong.max_context
    )
    reasons: list[str] = []
    score = 0
    policy = settings.policy
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    learned_raw_tier: str | None = None
    policy_fallback: str | None = None

    if requested_tier is not None:
        tier = requested_tier
        policy = "forced-alias"
        reasons.append(f"forced_{requested_tier}")
    else:
        runtime = runtime or build_policy_runtime(settings)
        if settings.policy == "learned":
            if runtime.learned is None:
                policy_fallback = settings.learned_error_fallback
                reasons.append(
                    f"learned_unavailable_{runtime.learned_load_error or 'unknown'}"
                )
                tier, score, fallback_reasons = _fallback_policy(
                    facts, runtime, settings.learned_error_fallback
                )
                reasons.extend(fallback_reasons)
            else:
                inference_started = time.perf_counter()
                try:
                    prediction = runtime.learned.predict(safe_features)
                    tier = prediction.tier
                    confidence = prediction.confidence
                    probabilities = prediction.probabilities
                    learned_raw_tier = prediction.raw_tier
                    if prediction.low_confidence_fallback:
                        reasons.append("learned_low_confidence_fallback")
                    else:
                        reasons.append("learned_argmax")
                except Exception as exc:
                    policy_fallback = settings.learned_error_fallback
                    error_name = type(exc).__name__
                    FAIL_OPEN.labels(f"learned_inference_{error_name}").inc()
                    reasons.append(f"learned_inference_error_{error_name}")
                    tier, score, fallback_reasons = _fallback_policy(
                        facts, runtime, settings.learned_error_fallback
                    )
                    reasons.extend(fallback_reasons)
                finally:
                    LEARNED_INFERENCE_SECONDS.observe(time.perf_counter() - inference_started)
        elif settings.policy == "calibrated":
            tier, score, reasons = _calibrated_decision(
                facts, runtime.calibration
            )
        else:
            tier, score, reasons = _heuristic_decision(facts)

    gated_tier, capability_upgrade = apply_capability_gates(
        tier, facts, settings, reasons
    )
    return Decision(
        proposed_tier=gated_tier,
        proposed_model=settings.tier(gated_tier).model,
        score=score,
        reasons=tuple(reasons or [f"default_{gated_tier}"]),
        facts=facts,
        policy=policy,
        confidence=confidence,
        probabilities=probabilities,
        learned_raw_tier=learned_raw_tier,
        policy_fallback=policy_fallback,
        capability_upgrade=capability_upgrade,
        safe_features=safe_features,
    )


def _required_context_tokens(facts: RequestFacts, candidate: Any, settings: Settings) -> int:
    # Character-based token estimation is intentionally cheap but approximate.
    # Apply a conservative prompt-only margin before a tier is declared context-safe.
    prompt_tokens = math.ceil(
        facts.estimated_total_tokens * settings.context_token_safety_factor
    )
    return prompt_tokens + (facts.requested_output_tokens or candidate.max_output)


def tier_satisfies_capabilities(
    tier: str, facts: RequestFacts, settings: Settings
) -> bool:
    candidate = settings.tier(tier)
    if facts.has_tools and not candidate.supports_tools:
        return False
    if facts.has_vision and not candidate.supports_vision:
        return False
    required_context = _required_context_tokens(facts, candidate, settings)
    return required_context <= candidate.max_context


def apply_capability_gates(
    tier: str, facts: RequestFacts, settings: Settings, reasons: list[str]
) -> tuple[str, str | None]:
    if facts.has_vision:
        reasons.append("vision_required")
    requested_rank = TIER_RANK[tier]
    for rank in range(requested_rank, TIER_RANK["strong"] + 1):
        candidate_name = _tier_name(rank)
        if not tier_satisfies_capabilities(candidate_name, facts, settings):
            continue
        if candidate_name != tier:
            reason = _capability_reason(tier, candidate_name, facts, settings)
            reasons.append(f"capability_upgrade_{tier}_to_{candidate_name}")
            return candidate_name, reason
        return candidate_name, None
    raise ValueError("no configured tier can satisfy request capabilities and context")


def _capability_reason(
    original: str, selected: str, facts: RequestFacts, settings: Settings
) -> str:
    if facts.has_vision and not settings.tier(original).supports_vision:
        return "vision"
    if facts.has_tools and not settings.tier(original).supports_tools:
        return "tools"
    required = _required_context_tokens(
        facts, settings.tier(original), settings
    )
    if required > settings.tier(original).max_context:
        return "context"
    return f"upgrade_to_{selected}"


def _heuristic_decision(facts: RequestFacts) -> tuple[str, int, list[str]]:
    score, reasons = _score_facts(facts, DEFAULT_CALIBRATION["weights"])
    tier = "fast" if score <= 0 else "standard" if score <= 5 else "strong"
    return tier, score, reasons


def _calibrated_decision(
    facts: RequestFacts, calibration: dict[str, Any]
) -> tuple[str, int, list[str]]:
    weights = calibration.get("weights", DEFAULT_CALIBRATION["weights"])
    score, reasons = _score_facts(facts, weights)
    fast_max = int(calibration.get("fast_max_score", 0))
    standard_max = int(calibration.get("standard_max_score", 5))
    tier = "fast" if score <= fast_max else "standard" if score <= standard_max else "strong"
    return tier, score, reasons


def _fallback_policy(
    facts: RequestFacts, runtime: PolicyRuntime, fallback: str
) -> tuple[str, int, list[str]]:
    if fallback == "calibrated":
        tier, score, reasons = _calibrated_decision(facts, runtime.calibration)
        return tier, score, ["fallback_calibrated", *reasons]
    tier, score, reasons = _heuristic_decision(facts)
    return tier, score, ["fallback_heuristic", *reasons]


def _score_facts(
    facts: RequestFacts, weights: dict[str, Any]
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    def add(condition: bool, name: str) -> None:
        nonlocal score
        if condition:
            score += int(weights.get(name, 0))
            reasons.append(name)

    add(facts.estimated_total_tokens > 8_000, "context_gt_8000")
    add(facts.estimated_total_tokens > 25_000, "context_gt_25000")
    add(facts.estimated_total_tokens > 50_000, "context_gt_50000")
    add(facts.has_tools, "tools_present")
    add(facts.estimated_tool_schema_tokens > 2_000, "large_tool_schema")
    add(facts.estimated_tool_result_tokens > 2_000, "large_tool_results")
    add(facts.code_blocks > 2, "multiple_code_blocks")
    add(facts.referenced_files > 3, "multiple_files")
    add(facts.structured_output, "structured_output")
    add(any(term in facts.text for term in COMPLEX_TERMS), "complex_intent")
    add(any(term in facts.text for term in SIMPLE_TERMS), "simple_intent")
    add(any(term in facts.text for term in FAILURE_TERMS), "failure_language")
    return score, reasons


def _load_calibration(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("calibration must be a JSON object")
        return payload
    except (OSError, ValueError, json.JSONDecodeError):
        return DEFAULT_CALIBRATION.copy()


def _tier_name(rank: int) -> str:
    return ("fast", "standard", "strong")[rank]


def _contains_image(value: Any) -> bool:
    if isinstance(value, list):
        return any(_contains_image(item) for item in value)
    if isinstance(value, dict):
        kind = str(value.get("type", "")).lower()
        mime = str(value.get("mime_type", value.get("media_type", ""))).lower()
        return kind in {"image", "image_url", "input_image"} or mime.startswith("image/") or any(
            _contains_image(child) for child in value.values()
        )
    return False


def _stable_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
