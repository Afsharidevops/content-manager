from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from .control_db import GuardrailRule

_INJECTION = [
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|system|developer)\s+instructions?\b", re.I),
    re.compile(r"\breveal\s+(the\s+)?(system|developer)\s+(prompt|message)\b", re.I),
    re.compile(r"\b(jailbreak|prompt\s*injection)\b", re.I),
]
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_HIGH_RISK_TOOLS = re.compile(r"(delete|destroy|format|wipe|shutdown|reboot|exec|shell|ssh|firewall|iptables|terraform.*apply)", re.I)


def _messages_text(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _tool_names(body: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for item in body.get("tools") or []:
        if not isinstance(item, dict):
            continue
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name = fn.get("name") if isinstance(fn, dict) else None
        if name:
            result.append(str(name))
    return result


@dataclass
class GuardrailResult:
    allowed: bool = True
    findings: list[dict[str, Any]] = field(default_factory=list)
    action: str = "allow"


class GuardrailEngine:
    def __init__(self, db: Any):
        self.db = db
        self.mode = os.getenv("SMART_ROUTER_GUARDRAILS_MODE", "audit").strip().lower()
        if self.mode not in {"off", "audit", "enforce"}:
            self.mode = "audit"
        self.allowed_tools = {x.strip() for x in os.getenv("SMART_ROUTER_ALLOWED_TOOLS", "").split(",") if x.strip()}
        self.deny_patterns = [x.strip() for x in os.getenv("SMART_ROUTER_GUARDRAIL_DENY_PATTERNS", "").split(",") if x.strip()]

    def status(self) -> dict[str, Any]:
        with self.db.session() as session:
            custom = list(session.scalars(select(GuardrailRule).where(GuardrailRule.enabled == True)))  # noqa: E712
        return {
            "mode": self.mode,
            "prompt_injection": True,
            "pii_detection": True,
            "tool_policy": True,
            "custom_rules": len(custom),
            "allowed_tools_configured": bool(self.allowed_tools),
        }

    def evaluate(self, body: dict[str, Any]) -> GuardrailResult:
        if self.mode == "off":
            return GuardrailResult()
        text_value = _messages_text(body)
        findings: list[dict[str, Any]] = []
        if any(pattern.search(text_value) for pattern in _INJECTION):
            findings.append({"category": "prompt_injection", "severity": "high", "message": "prompt-injection pattern detected"})
        pii: list[str] = []
        if _EMAIL.search(text_value): pii.append("email")
        if _SSN.search(text_value): pii.append("ssn_like")
        if _CARD.search(text_value): pii.append("long_number_or_card_like")
        if pii:
            findings.append({"category": "pii", "severity": "medium", "types": pii, "message": "possible PII detected"})
        for pattern in self.deny_patterns:
            if pattern.lower() in text_value.lower():
                findings.append({"category": "content_policy", "severity": "high", "message": f"configured deny pattern matched: {pattern[:80]}"})
        tools = _tool_names(body)
        if self.allowed_tools:
            denied = [tool for tool in tools if tool not in self.allowed_tools]
            if denied:
                findings.append({"category": "tool_policy", "severity": "high", "message": "tool is not allow-listed", "tools": denied})
        high_risk = [tool for tool in tools if _HIGH_RISK_TOOLS.search(tool)]
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        hermes = metadata.get("hermes") if isinstance(metadata.get("hermes"), dict) else {}
        if high_risk and not bool(hermes.get("high_risk_confirmed")):
            findings.append({"category": "tool_confirmation", "severity": "high", "message": "high-risk tool requires explicit confirmation metadata", "tools": high_risk})
        with self.db.session() as session:
            rules = list(session.scalars(select(GuardrailRule).where(GuardrailRule.enabled == True)))  # noqa: E712
        for rule in rules:
            try:
                matched = re.search(rule.pattern, text_value, re.I) is not None
            except re.error:
                matched = rule.pattern.lower() in text_value.lower()
            if matched:
                findings.append({"category": rule.category, "severity": "high" if rule.action == "block" else "medium", "message": f"custom rule matched: {rule.name}", "rule_id": rule.id, "rule_action": rule.action})
        block = self.mode == "enforce" and any(
            finding.get("category") in {"prompt_injection", "content_policy", "tool_policy", "tool_confirmation"}
            or finding.get("rule_action") == "block"
            for finding in findings
        )
        return GuardrailResult(allowed=not block, findings=findings, action="block" if block else ("audit" if findings else "allow"))
