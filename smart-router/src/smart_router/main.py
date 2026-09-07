from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager

import httpx
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from . import __version__
from .budget import BudgetResult, enforce_budget, propose_budget
from .config import Settings
from .control_plane import ControlPlane
from .costs import CostLedger
from .dashboard import dashboard_enabled, dashboard_response
from .database import create_route_store
from .metrics import (
    ACTIVE_STREAMS,
    BUDGET_ENFORCEMENTS,
    DURATION,
    EFFECTIVE_OUTPUT,
    FAIL_OPEN,
    LEARNED_FALLBACKS,
    PROPOSED_OUTPUT,
    PROPOSED_TIERS,
    READINESS,
    REQUESTS,
    STICKY,
    TOKEN_ESTIMATES,
    UPSTREAM_CACHED_INPUT_TOKENS,
    UPSTREAM_ERRORS,
    UPSTREAM_INPUT_TOKENS,
    UPSTREAM_OUTPUT_TOKENS,
    USAGE_MISSING,
)
from .observations import ObservationWriter
from .privacy import session_identity
from .proxy import forward_headers, proxy_buffered, proxy_streaming, response_header_pairs
from .routing import AUTO_ALIASES, Decision, build_policy_runtime, decide, tier_satisfies_capabilities

logger = logging.getLogger("smart-router")
logging.basicConfig(level=logging.INFO, format="%(message)s")



def _resolve_requested_tier(
    requested_model: str, header_tier: str | None, allow_tier_overrides: bool
) -> str | None:
    requested_tier = AUTO_ALIASES[requested_model]

    if requested_model != "auto" and not allow_tier_overrides:
        raise PermissionError("client tier overrides are disabled")

    if header_tier is not None:
        header_tier = header_tier.strip().lower()

        if header_tier not in {"fast", "standard", "strong"}:
            raise ValueError(
                "X-Router-Tier must be fast, standard, or strong"
            )

        if not allow_tier_overrides:
            raise PermissionError("client tier overrides are disabled")

        requested_tier = header_tier

    return requested_tier


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    settings = settings or Settings.from_env()
    store = create_route_store(settings)
    policy_runtime = build_policy_runtime(settings)
    observations = ObservationWriter(settings.observation_file)
    cost_ledger = CostLedger.from_env(default_database_path=settings.database_path)
    control_plane = ControlPlane(settings)  # Hermes Smart Router v0.5.9 Operations Center hook
    timeout = httpx.Timeout(
        connect=settings.connect_timeout_seconds,
        read=settings.read_timeout_seconds,
        write=settings.read_timeout_seconds,
        pool=settings.connect_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        app.state.client = httpx.AsyncClient(timeout=timeout, transport=transport)
        try:
            yield
        finally:
            await app.state.client.aclose()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "mode": settings.mode,
                "policy": settings.policy,
                "learned_model_loaded": policy_runtime.learned is not None,
            }
        )

    async def ready(request: Request) -> JSONResponse:
        database_ok = store.ready()
        upstream_ok = False
        try:
            response = await request.app.state.client.get(
                settings.upstream_health_url,
                headers=_upstream_auth_headers(settings),
            )
            upstream_ok = response.is_success
        except httpx.HTTPError:
            pass
        control_db_ok = control_plane.db.ping()
        redis_ok = control_plane.redis.ping()
        READINESS.labels("database").set(int(database_ok))
        READINESS.labels("control_database").set(int(control_db_ok))
        READINESS.labels("redis").set(int(redis_ok))
        READINESS.labels("upstream").set(int(upstream_ok))
        ready_ok = database_ok and control_db_ok and upstream_ok and redis_ok
        return JSONResponse(
            {
                "status": "ready" if ready_ok else "not-ready",
                "components": {"database": database_ok, "control_database": control_db_ok, "redis": redis_ok, "upstream": upstream_ok},
            },
            status_code=200 if ready_ok else 503,
        )

    async def metrics(_: Request) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    async def dashboard(request: Request) -> Response:
        # Static shell contains no telemetry; the JSON API remains authenticated.
        if not dashboard_enabled():
            return Response(status_code=404)
        return dashboard_response(version=__version__)

    async def dashboard_summary(request: Request) -> Response:
        auth_error = _client_auth_error(request, settings)
        if auth_error:
            return auth_error
        if not dashboard_enabled():
            return Response(status_code=404)
        try:
            hours = float(request.query_params.get("hours", "24"))
        except ValueError:
            return _openai_error("hours must be numeric", "invalid_dashboard_window", 400)
        payload = cost_ledger.summary(hours=hours)
        payload["version"] = __version__
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def dashboard_traces(request: Request) -> Response:
        auth_error = _client_auth_error(request, settings)
        if auth_error:
            return auth_error
        if not dashboard_enabled():
            return Response(status_code=404)
        try:
            limit = max(1, min(250, int(request.query_params.get("limit", "80"))))
        except ValueError:
            return _openai_error("limit must be an integer", "invalid_trace_limit", 400)
        return JSONResponse(control_plane.recent_traces(limit), headers={"Cache-Control": "no-store"})

    async def dashboard_trace(request: Request) -> Response:
        auth_error = _client_auth_error(request, settings)
        if auth_error:
            return auth_error
        if not dashboard_enabled():
            return Response(status_code=404)
        return JSONResponse(control_plane.trace_detail(str(request.path_params["request_id"])[:80]), headers={"Cache-Control": "no-store"})


    async def models(request: Request) -> Response:
        auth_error = _client_auth_error(request, settings)
        if auth_error:
            return auth_error
        started = time.monotonic()
        try:
            headers = _forward_headers(request, settings)
        except ValueError as error:
            return _openai_error(str(error), "duplicate_credential_header", 400)
        url = settings.upstream_base_url + "/models"
        if request.scope["query_string"]:
            url += "?" + request.scope["query_string"].decode("ascii")
        try:
            upstream = await request.app.state.client.get(url, headers=headers)
            upstream.read()
        except httpx.HTTPError:
            return _openai_error("upstream unavailable", "upstream_unavailable", 503)
        response: Response
        if upstream.is_success:
            try:
                payload = upstream.json()
                existing = {item.get("id") for item in payload.get("data", [])}
                advertised_aliases = AUTO_ALIASES if settings.allow_tier_overrides else {"auto": None}
                for alias in advertised_aliases:
                    if alias not in existing:
                        payload.setdefault("data", []).append(
                            {"id": alias, "object": "model", "owned_by": "smart-router"}
                        )
                response = JSONResponse(payload, status_code=upstream.status_code)
                response.raw_headers = [
                    (name, value)
                    for name, value in response_header_pairs(upstream.headers)
                    if name.lower() not in {b"content-encoding", b"content-length"}
                ] + [(b"content-length", str(len(response.body)).encode())]
            except (ValueError, TypeError):
                response = Response(upstream.content, status_code=upstream.status_code)
                response.raw_headers = response_header_pairs(upstream.headers)
        else:
            response = Response(upstream.content, status_code=upstream.status_code)
            response.raw_headers = response_header_pairs(upstream.headers)
        _record_request("models", settings.mode, "explicit", False, response.status_code, started)
        return response

    async def completions(request: Request) -> Response:
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
        v51_error = control_plane.begin_request(request, body)
        if v51_error:
            return v51_error
        requested_model = body["model"]
        stream = body.get("stream") is True
        try:
            headers = _forward_headers(request, settings)
        except ValueError as error:
            return _openai_error(str(error), "duplicate_credential_header", 400)
        url = settings.upstream_base_url + "/chat/completions"
        if request.scope["query_string"]:
            url += "?" + request.scope["query_string"].decode("ascii")

        # Explicit model requests remain byte-transparent and bypass Smart Router policy.
        if requested_model not in AUTO_ALIASES:
            control_plane.trace(request, "selected_route", "explicit", {"model": requested_model, "automatic_routing": False})
            response = await _dispatch(request, raw, headers, url, stream, settings.mode, "explicit", started)
            control_plane.trace(request, "result", "ok" if response.status_code < 400 else "error", {"status_code": response.status_code, "explicit_model": requested_model})
            return response

        header_tier = request.headers.get("x-router-tier")
        try:
            requested_tier = _resolve_requested_tier(
                requested_model,
                header_tier,
                settings.allow_tier_overrides,
            )
        except PermissionError as error:
            return _openai_error(
                str(error), "tier_override_forbidden", 403
            )
        except ValueError as error:
            return _openai_error(
                str(error), "invalid_tier_override", 400
            )
        try:
            decision = decide(body, settings, requested_tier, policy_runtime)
            proposal = propose_budget(body, decision, settings)
            control_plane.trace_routing_decision(request, decision, proposal)
        except (TypeError, ValueError, OverflowError) as error:
            return _openai_error(str(error), "invalid_routing_request", 422)

        if decision.policy_fallback:
            LEARNED_FALLBACKS.labels(f"error_{decision.policy_fallback}").inc()
        elif "learned_low_confidence_fallback" in decision.reasons:
            LEARNED_FALLBACKS.labels("low_confidence").inc()
        _safe_record_proposal(decision, proposal, settings.mode)
        session_hash, session_source = session_identity(
            request.headers, body, settings.hmac_secret
        )
        selected_tier = decision.proposed_tier
        sticky_action = "stateless"
        if settings.mode == "route" and session_hash and not control_plane.ha_mode:
            try:
                if request.headers.get("x-router-reset", "").lower() == "true":
                    await asyncio.to_thread(store.reset, session_hash, requested_model)
                sticky = await asyncio.to_thread(
                    store.resolve,
                    session_hash,
                    requested_model,
                    decision.proposed_tier,
                    decision.reasons[-1],
                )
                selected_tier = sticky.tier
                sticky_action = sticky.action
                STICKY.labels(sticky.action, session_source, sticky.tier).inc()
            except Exception as error:
                sticky_action = "store-error-stateless"
                logger.error(json.dumps({"event": "sticky_store_error", "reason": type(error).__name__}))

            # Sticky state is never allowed to bypass capability/context requirements.
            # If a manually-constructed/non-monotonic configuration makes the sticky
            # tier incompatible, prefer the already capability-gated proposal.
            if not tier_satisfies_capabilities(selected_tier, decision.facts, settings):
                selected_tier = decision.proposed_tier
                sticky_action = f"{sticky_action}-capability-override"

        if settings.mode == "observe":
            effective_model = settings.observe_model
            budget = BudgetResult(
                proposal.client_limit,
                proposal.proposed_limit,
                proposal.client_limit,
                False,
                proposal.fields,
            )
        else:
            effective_model = settings.tier(selected_tier).model
            if selected_tier != decision.proposed_tier:
                selected_decision = Decision(
                    proposed_tier=selected_tier,
                    proposed_model=effective_model,
                    score=decision.score,
                    reasons=decision.reasons,
                    facts=decision.facts,
                    policy=decision.policy,
                    confidence=decision.confidence,
                    probabilities=decision.probabilities,
                    learned_raw_tier=decision.learned_raw_tier,
                    policy_fallback=decision.policy_fallback,
                    capability_upgrade=decision.capability_upgrade,
                    feature_schema_version=decision.feature_schema_version,
                    safe_features=decision.safe_features,
                )
                proposal = propose_budget(body, selected_decision, settings)
            budget = enforce_budget(body, proposal, settings)
            if budget.enforced:
                BUDGET_ENFORCEMENTS.labels(
                    selected_tier, "+".join(budget.fields) or "none"
                ).inc()

        v51_route = control_plane.finalize_routing(request, body, selected_tier, effective_model, budget)
        if v51_route.error:
            return v51_route.error
        selected_tier = v51_route.tier
        effective_model = v51_route.model
        body["model"] = effective_model
        outbound = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        _safe_log_decision(
            settings.mode,
            requested_model,
            effective_model,
            decision,
            selected_tier,
            budget,
            session_source,
            sticky_action,
        )
        _safe_observe(
            observations,
            decision,
            selected_tier,
            effective_model,
            budget,
            session_hash,
            sticky_action,
            settings.mode,
        )
        response = await _dispatch(
            request, outbound, headers, url, stream, settings.mode, "auto", started
        )
        try:
            cost_ledger.record_response(
                response,
                tier=selected_tier,
                model=effective_model,
                client_output_limit=budget.client_limit,
                effective_output_limit=budget.effective_limit,
                streaming=stream,
            )
        except Exception as error:
            logger.error(json.dumps({"event": "cost_ledger_error", "reason": type(error).__name__}))
        try:
            control_plane.record_route(request, response, decision.policy, decision.reasons)
        except Exception as error:
            logger.error(json.dumps({"event": "v51_control_telemetry_error", "reason": type(error).__name__}))
        return response

    async def router_info(_: Request) -> Response:
        return JSONResponse({
            "version": __version__,
            "mode": settings.mode,
            "policy": settings.policy,
            "control_plane": control_plane.enabled,
            "ha_mode": control_plane.ha_mode,
            "redis_enabled": control_plane.redis.enabled,
            "sticky_backend": type(store).__name__,
        }, headers={"Cache-Control": "no-store"})

    routes = [
        Route("/health", health),
        Route("/ready", ready),
        Route("/metrics", metrics),
        Route("/router/info", router_info, methods=["GET"]),
        Route("/router/policy", router_info, methods=["GET"]),  # v0.5.1 manage.sh compatibility
        Route("/dashboard", dashboard, methods=["GET"]),
        Route("/dashboard/api/summary", dashboard_summary, methods=["GET"]),
        Route("/dashboard/api/traces", dashboard_traces, methods=["GET"]),
        Route("/dashboard/api/traces/{request_id:str}", dashboard_trace, methods=["GET"]),
        Mount("/control", app=control_plane.app),
        Route("/v1/models", models, methods=["GET"]),
        Route("/v1/chat/completions", completions, methods=["POST"]),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.policy_runtime = policy_runtime
    app.state.cost_ledger = cost_ledger
    app.state.control_plane = control_plane
    return app


async def _dispatch(
    request: Request,
    content: bytes,
    headers: list[tuple[bytes, bytes]],
    url: str,
    stream: bool,
    mode: str,
    request_kind: str,
    started: float,
) -> Response:
    try:
        if stream:
            ACTIVE_STREAMS.inc()
            response_holder: dict[str, Response] = {}

            def stream_complete(completed: bool) -> None:
                ACTIVE_STREAMS.dec()
                USAGE_MISSING.labels("true").inc()
                response = response_holder.get("response")
                _record_request(
                    "chat", mode, request_kind, True,
                    response.status_code if completed and response else 502,
                    started,
                )

            response = await proxy_streaming(
                request.app.state.client,
                "POST",
                url,
                headers,
                content,
                on_complete=stream_complete,
            )
            response_holder["response"] = response
        else:
            retry_count = max(0, min(5, int(getattr(request.state, "v56_retry_count", 0) or 0)))
            fallback_models = list(getattr(request.state, "v56_fallback_models", []) or [])
            attempt = 0
            current_content = content
            response = await proxy_buffered(request.app.state.client, "POST", url, headers, current_content)
            while response.status_code in {408, 425, 429, 500, 502, 503, 504} and attempt < retry_count:
                attempt += 1
                control = getattr(request.app.state, "control_plane", None)
                if control is not None:
                    control.trace(request, "retry", "attempt", {"attempt": attempt, "status_code": response.status_code})
                if fallback_models and attempt - 1 < len(fallback_models):
                    try:
                        retry_body = json.loads(content)
                        retry_body["model"] = fallback_models[attempt - 1]
                        current_content = json.dumps(retry_body, ensure_ascii=False, separators=(",", ":")).encode()
                        if control is not None:
                            control.trace(request, "fallback", "retry_model", {"attempt": attempt, "model": fallback_models[attempt - 1]})
                    except Exception:
                        current_content = content
                await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
                response = await proxy_buffered(request.app.state.client, "POST", url, headers, current_content)
            _record_actual_usage(response)
    except httpx.HTTPError as error:
        logger.error(json.dumps({"event": "upstream_error", "reason": type(error).__name__}))
        _record_request("chat", mode, request_kind, stream, 503, started)
        return _openai_error("upstream unavailable", "upstream_unavailable", 503)
    if response.status_code >= 400:
        UPSTREAM_ERRORS.labels(_status_class(response.status_code)).inc()
    if not stream:
        _record_request("chat", mode, request_kind, False, response.status_code, started)
    return response


def _client_auth_error(request: Request, settings: Settings) -> JSONResponse | None:
    control = getattr(request.app.state, "control_plane", None)
    if control is not None:
        if control.authenticate_api_request(request) is not None:
            return None
        if control.require_auth:
            return _openai_error("authentication required", "auth_required", 401)
    if not settings.client_api_key:
        return None
    provided = request.headers.get("authorization", "")
    token = provided[7:] if provided.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
    if not token or not hmac.compare_digest(token, settings.client_api_key):
        return _openai_error("invalid API key", "invalid_api_key", 401)
    return None


def _forward_headers(request: Request, settings: Settings) -> list[tuple[bytes, bytes]]:
    return forward_headers(
        request.scope["headers"],
        upstream_api_key=settings.upstream_api_key,
        consume_client_credentials=bool(settings.client_api_key) or bool(getattr(request.state, "v51_identity", None)),
    )


def _upstream_auth_headers(settings: Settings) -> dict[str, str] | None:
    if not settings.upstream_api_key:
        return None
    return {"Authorization": f"Bearer {settings.upstream_api_key}"}


def _safe_observe(
    writer: ObservationWriter,
    decision: Decision,
    selected_tier: str,
    effective_model: str,
    budget: BudgetResult,
    session_hash: str | None,
    sticky_action: str,
    mode: str,
) -> None:
    try:
        writer.write(
            {
                "event": "route_decision",
                "mode": mode,
                "policy": decision.policy,
                "feature_schema_version": decision.feature_schema_version,
                "features": decision.safe_features.as_dict() if decision.safe_features else {},
                "proposed_tier": decision.proposed_tier,
                "final_tier": selected_tier,
                "effective_model": effective_model,
                "confidence": decision.confidence,
                "probabilities": decision.probabilities,
                "learned_raw_tier": decision.learned_raw_tier,
                "policy_fallback": decision.policy_fallback,
                "capability_upgrade": decision.capability_upgrade,
                "sticky_action": sticky_action,
                "session_hash": session_hash,
                "proposed_output_limit": budget.proposed_limit,
                "effective_output_limit": budget.effective_limit,
            }
        )
    except Exception as error:
        logger.error(json.dumps({"event": "observation_error", "reason": type(error).__name__}))


def _safe_record_proposal(decision: Decision, budget: BudgetResult, mode: str) -> None:
    try:
        _record_proposal(decision, budget, mode)
    except Exception as error:
        logger.error(json.dumps({"event": "metrics_error", "reason": type(error).__name__}))


def _safe_log_decision(*args: object) -> None:
    try:
        _log_decision(*args)  # type: ignore[arg-type]
    except Exception as error:
        logger.error(json.dumps({"event": "log_error", "reason": type(error).__name__}))


def _record_proposal(decision: Decision, budget: BudgetResult, mode: str) -> None:
    PROPOSED_TIERS.labels(mode, decision.proposed_tier, decision.reasons[0]).inc()
    PROPOSED_OUTPUT.observe(budget.proposed_limit)
    facts = decision.facts
    TOKEN_ESTIMATES.labels("messages").inc(facts.estimated_message_tokens)
    TOKEN_ESTIMATES.labels("tool_schema").inc(facts.estimated_tool_schema_tokens)
    TOKEN_ESTIMATES.labels("tool_results").inc(facts.estimated_tool_result_tokens)
    TOKEN_ESTIMATES.labels("total").inc(facts.estimated_total_tokens)


def _record_actual_usage(response: Response) -> None:
    found = False
    try:
        payload = json.loads(response.body)
        usage = payload.get("usage") or {}
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens")
        completion = usage.get("completion_tokens") or usage.get("output_tokens")
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        cached = details.get("cached_tokens")
        if isinstance(prompt, (int, float)):
            UPSTREAM_INPUT_TOKENS.inc(prompt); found = True
        if isinstance(cached, (int, float)):
            UPSTREAM_CACHED_INPUT_TOKENS.inc(cached); found = True
        if isinstance(completion, (int, float)):
            UPSTREAM_OUTPUT_TOKENS.inc(completion); found = True
    except (ValueError, TypeError, AttributeError):
        pass
    if not found:
        USAGE_MISSING.labels("false").inc()


def _log_decision(
    mode: str,
    requested_model: str,
    effective_model: str,
    decision: Decision,
    selected_tier: str,
    budget: BudgetResult,
    session_source: str,
    sticky_action: str,
) -> None:
    logger.info(
        json.dumps(
            {
                "event": "route_decision",
                "mode": mode,
                "requested_model": requested_model,
                "effective_model": effective_model,
                "policy": decision.policy,
                "proposed_tier": decision.proposed_tier,
                "selected_tier": selected_tier,
                "score": decision.score,
                "reasons": decision.reasons,
                "confidence": decision.confidence,
                "probabilities": decision.probabilities,
                "capability_upgrade": decision.capability_upgrade,
                "policy_fallback": decision.policy_fallback,
                "session_source": session_source,
                "sticky_action": sticky_action,
                "client_output_limit": budget.client_limit,
                "effective_output_limit": budget.effective_limit,
                "proposed_output_limit": budget.proposed_limit,
                "budget_enforced": budget.enforced,
            },
            separators=(",", ":"),
        )
    )
    if budget.effective_limit is not None:
        EFFECTIVE_OUTPUT.observe(budget.effective_limit)


def _record_request(
    endpoint: str, mode: str, request_kind: str, stream: bool,
    status: int, started: float,
) -> None:
    REQUESTS.labels(endpoint, mode, request_kind, str(stream).lower(), _status_class(status)).inc()
    DURATION.labels(mode, request_kind, str(stream).lower()).observe(time.monotonic() - started)


def _status_class(status: int) -> str:
    return f"{status // 100}xx"


async def _bounded_body(request: Request, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ValueError("request too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _openai_error(message: str, code: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "smart_router_error", "code": code}},
        status_code=status,
    )


def run() -> None:
    import uvicorn
    uvicorn.run("smart_router.asgi:app", host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    run()
