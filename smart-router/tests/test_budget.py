from smart_router.budget import enforce_budget, propose_budget
from smart_router.routing import decide


def test_tier_budget_clamps_client_limit(settings):
    body = {"model": "auto-fast", "messages": [{"role": "user", "content": "translate this"}], "max_tokens": 9000}
    decision = decide(body, settings, requested_tier="fast")
    proposal = propose_budget(body, decision, settings)
    result = enforce_budget(body, proposal, settings)
    assert body["max_tokens"] <= settings.fast.max_output
    assert result.enforced


def test_explicit_budget_not_touched_by_router_helper_contract(settings):
    body = {"model": "gpt-explicit", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 9000}
    original = body.copy()
    assert body == original


def test_standard_budget_is_applied(settings):
    body = {"model": "auto-standard", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 99999}
    decision = decide(body, settings, requested_tier="standard")
    result = enforce_budget(body, propose_budget(body, decision, settings), settings)
    assert body["max_tokens"] == settings.standard.max_output
    assert result.enforced


def test_strong_budget_is_applied(settings):
    body = {"model": "auto-strong", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 99999}
    decision = decide(body, settings, requested_tier="strong")
    result = enforce_budget(body, propose_budget(body, decision, settings), settings)
    assert body["max_tokens"] == settings.strong.max_output
    assert result.enforced


def test_max_completion_tokens_budget_is_applied(settings):
    body = {"model": "auto-fast", "messages": [{"role": "user", "content": "translate"}], "max_completion_tokens": 2000}
    decision = decide(body, settings, requested_tier="fast")
    result = enforce_budget(body, propose_budget(body, decision, settings), settings)
    assert body["max_completion_tokens"] == settings.fast.max_output
    assert result.enforced
