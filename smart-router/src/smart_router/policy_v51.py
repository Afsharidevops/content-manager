from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from .control_db import ControlDB, Policy
from .security_v51 import Identity

TIER_ORDER = {"fast": 0, "standard": 1, "strong": 2}


@dataclass
class PolicyResult:
    allowed: bool = True
    deny_reason: str = ""
    force_min_tier: str | None = None
    max_output_tokens: int | None = None
    matched: list[str] | None = None


class PolicyEngine:
    def __init__(self, db: ControlDB):
        self.db = db

    def evaluate(self, body: dict[str, Any], identity: Identity, proposed_tier: str, profile: str) -> PolicyResult:
        result = PolicyResult(matched=[])
        prompt = _prompt_text(body)
        with self.db.session() as session:
            policies = list(session.scalars(select(Policy).where(Policy.enabled.is_(True)).order_by(Policy.priority.asc())))
        for policy in policies:
            try:
                rule = json.loads(policy.rule_json or "{}")
                action = json.loads(policy.action_json or "{}")
            except json.JSONDecodeError:
                continue
            if not _matches(rule, identity, proposed_tier, profile, prompt):
                continue
            result.matched.append(policy.name)
            if action.get("deny") is True:
                result.allowed = False
                result.deny_reason = str(action.get("reason") or f"denied by policy {policy.name}")
                return result
            min_tier = action.get("force_min_tier")
            if min_tier in TIER_ORDER:
                if result.force_min_tier is None or TIER_ORDER[min_tier] > TIER_ORDER.get(result.force_min_tier, -1):
                    result.force_min_tier = min_tier
            if action.get("max_output_tokens") is not None:
                value = max(1, int(action["max_output_tokens"]))
                result.max_output_tokens = value if result.max_output_tokens is None else min(value, result.max_output_tokens)
        return result


def _matches(rule: dict[str, Any], identity: Identity, tier: str, profile: str, prompt: str) -> bool:
    roles = rule.get("roles")
    if roles and identity.role not in roles:
        return False
    teams = rule.get("teams")
    if teams and identity.team not in teams:
        return False
    tiers = rule.get("tiers")
    if tiers and tier not in tiers:
        return False
    profiles = rule.get("profiles")
    if profiles and profile not in profiles:
        return False
    contains = rule.get("prompt_contains")
    if contains and not any(str(term).lower() in prompt.lower() for term in contains):
        return False
    regex = rule.get("prompt_regex")
    if regex:
        try:
            if not re.search(str(regex), prompt, re.IGNORECASE):
                return False
        except re.error:
            return False
    return True


def _prompt_text(body: dict[str, Any]) -> str:
    pieces: list[str] = []
    for msg in body.get("messages") or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            pieces.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    pieces.append(str(item.get("text", "")))
    return "\n".join(pieces)[-20000:]
