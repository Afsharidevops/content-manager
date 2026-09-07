from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings
from .routing import Decision


@dataclass(frozen=True)
class BudgetResult:
    client_limit: int | None
    proposed_limit: int
    effective_limit: int | None
    enforced: bool
    fields: tuple[str, ...]


def propose_budget(
    body: dict[str, Any], decision: Decision, settings: Settings
) -> BudgetResult:
    fields, client_limit = _client_limits(body)
    tier_limit = settings.tier(decision.proposed_tier).max_output
    proposed = tier_limit
    text = decision.facts.text
    if any(term in text for term in ("reply briefly", "short answer", "be concise")):
        proposed = min(proposed, 700)
    if any(term in text for term in ("translate", "rewrite", "grammar")):
        proposed = min(proposed, 1200)
    if decision.facts.structured_output:
        proposed = min(proposed, 2500)
    if decision.facts.has_tools:
        proposed = min(tier_limit, max(proposed, 2048))
    if client_limit is not None:
        proposed = min(proposed, client_limit)
    return BudgetResult(client_limit, proposed, client_limit, False, fields)


def enforce_budget(
    body: dict[str, Any], proposal: BudgetResult, settings: Settings
) -> BudgetResult:
    fields = proposal.fields or (settings.preferred_token_field,)
    enforced = False
    effective_values: list[int] = []
    for field in fields:
        if field in body:
            current = int(body[field])
            effective = min(current, proposal.proposed_limit)
            body[field] = effective
            enforced = enforced or effective < current
        else:
            effective = proposal.proposed_limit
            body[field] = effective
            enforced = True
        effective_values.append(effective)
    return BudgetResult(
        proposal.client_limit,
        proposal.proposed_limit,
        min(effective_values),
        enforced,
        fields,
    )


def _client_limits(body: dict[str, Any]) -> tuple[tuple[str, ...], int | None]:
    fields = tuple(
        field
        for field in ("max_completion_tokens", "max_tokens")
        if body.get(field) is not None
    )
    if not fields:
        return (), None
    values = []
    for field in fields:
        value = int(body[field])
        if value < 0:
            raise ValueError(f"{field} must be non-negative")
        values.append(value)
    return fields, min(values)
