# Hermes Smart Router v0.5.1

Smart Router is an OpenAI-compatible `/v1` proxy for Hermes Linux Stack. Version 0.5.1 keeps the v0.2 deterministic safety path and adds an optional offline-trained, CPU-only three-tier learned proposal (`fast`, `standard`, `strong`). The learned classifier never bypasses tools, vision, context, sticky-session, or output-budget policy.

## Safe default

```env
SMART_ROUTER_MODE=observe
SMART_ROUTER_POLICY=heuristic
```

The learned policy is workload-specific. Train it offline, evaluate it, run it in observe/shadow mode, then explicitly opt into route mode.

## Learned policy

```env
SMART_ROUTER_POLICY=learned
SMART_ROUTER_LEARNED_MODEL_FILE=/policy/learned-v4.joblib
SMART_ROUTER_LEARNED_METADATA_FILE=/policy/learned-v4.json
SMART_ROUTER_LEARNED_MIN_CONFIDENCE=0.70
SMART_ROUTER_LEARNED_FALLBACK=standard
SMART_ROUTER_LEARNED_ERROR_FALLBACK=heuristic
```

Inference is local and network-free. The model is loaded once when the application starts. If loading or inference fails, routing falls back to the configured deterministic policy. The feature schema is versioned (`1`) and contains only structured request-shape/capability features; observations never need raw prompt text or raw tool arguments.

> Security: `joblib` uses pickle-compatible serialization and can execute code while loading. Only load model artifacts produced by a trusted Smart Router training process. The metadata can include an SHA-256 digest to detect accidental or unauthorized artifact replacement.

## Train

Training input is JSONL with `schema_version`, a complete privacy-safe `features` object, and either `label` or tier outcome scores.

```bash
smart-router-train examples/learned-routing-sample.jsonl \
  --output policy/learned-v4.joblib \
  --metadata policy/learned-v4.json \
  --random-seed 42
```

The default classifier is `HistGradientBoostingClassifier`; `--model logistic-regression` is also supported. Training produces metadata with schema, classes, validation metrics, confidence fallback, seed, and artifact digest.

## Evaluate

```bash
smart-router-report examples/learned-routing-sample.jsonl \
  --learned-model policy/learned-v4.joblib \
  --metadata policy/learned-v4.json
```

The report includes fixed fast/standard/strong baselines and learned accuracy, per-tier precision/recall, confusion matrix, false-fast rate, strong-overroute rate, tier distribution, and low-confidence fallback rate. When evaluation rows include a safe `request` object, capability violations and upgrades are measured; otherwise those capability metrics are reported as unavailable rather than fabricated. Capability gates remain runtime invariants; a learned proposal cannot force an incompatible tier.

## Client API key

Smart Router can be exposed to OpenAI-compatible clients without sharing the downstream 9router/OmniRoute/provider credential:

```env
SMART_ROUTER_CLIENT_API_KEY=<random-client-secret>
SMART_ROUTER_UPSTREAM_API_KEY=<optional-downstream-gateway-secret>
```

When `SMART_ROUTER_CLIENT_API_KEY` is set, clients must send `Authorization: Bearer <secret>` or `x-api-key: <secret>`. Client credentials are consumed at Smart Router and are not forwarded. If `SMART_ROUTER_UPSTREAM_API_KEY` is configured, Smart Router injects it upstream. Use HTTPS or a private network; do not publish port 8080 directly to the Internet.

Clients should select `model=auto`. `auto-fast`, `auto-standard`, and `auto-strong` are available as explicit tier aliases. Forced fast/standard aliases can still be upgraded by hard capability gates.

## Branch configuration

`main` (9router):

```env
SMART_ROUTER_UPSTREAM_BASE_URL=http://nine-router:20128/v1
SMART_ROUTER_UPSTREAM_HEALTH_URL=http://nine-router:20128/api/health
SMART_ROUTER_FAST_MODEL=combo-fast
SMART_ROUTER_STANDARD_MODEL=combo-standard
SMART_ROUTER_STRONG_MODEL=combo-strong
```

`hermes-omniroute-linux-stack` (OmniRoute):

```env
SMART_ROUTER_UPSTREAM_BASE_URL=http://omniroute:20129/v1
SMART_ROUTER_UPSTREAM_HEALTH_URL=http://omniroute:20128/api/monitoring/health
SMART_ROUTER_FAST_MODEL=auto
SMART_ROUTER_STANDARD_MODEL=auto
SMART_ROUTER_STRONG_MODEL=auto
```

Do not invent OmniRoute route IDs. Replace the three `auto` targets only after the actual OmniRoute deployment has validated distinct tier route IDs.

## Endpoints

- `GET /health`
- `GET /ready`
- `GET /metrics`
- `GET /v1/models`
- `POST /v1/chat/completions`

Explicit non-Smart-Router model IDs pass through unchanged. Streaming response bytes are passed through without decoding/re-encoding the SSE body.


## v0.4.0 hardening

- The upstream API URL is required; the shared image no longer defaults to a 9router address.
- `SMART_ROUTER_UPSTREAM_HEALTH_URL` supports backend-specific readiness probes.
- Tier tool/vision capability flags and context windows must be monotonic from fast to standard to strong.
- Sticky routing is rechecked against capability/context requirements before the final model is selected.
- Logistic-regression training is compatible with current scikit-learn.
- Small stratified datasets reserve enough validation rows for all three classes.
- Learned inference latency and fail-open events are exported through Prometheus metrics.
- Use `SMART_ROUTER_*_MAX_CONTEXT`; the legacy `*_CONTEXT_LIMIT` names are not used by v0.3.x.


<!-- v0.4.0-benchmarking -->
## v0.4.0 cost/quality benchmark

Install the optional benchmark tooling with `python -m pip install -e './smart-router[dev,bench]'`, then run `smart-router-benchmark`. It generates `quality_vs_cost.png`, `tier_distribution.png`, `confusion_matrix.png`, `confidence_risk.png`, `frontier.csv`, `summary.json`, and `report.md`.

The bundled `examples/benchmark-synthetic-v0.4.0.jsonl` is synthetic test data only. Its plots are watermarked and must not be presented as Hermes performance. For public results, supply per-tier `quality_by_tier` scores from a representative held-out workload and a documented cost model. See `../docs/SMART-ROUTER-v0.4.0-BENCHMARKING.md`.

### v0.4.0 safety controls

- `SMART_ROUTER_CONTEXT_TOKEN_SAFETY_FACTOR=1.15` applies a conservative margin to approximate prompt-token counts before context capability gates.
- `SMART_ROUTER_ALLOW_TIER_OVERRIDES=false` prevents ordinary clients from forcing `auto-fast`, `auto-standard`, `auto-strong`, or `X-Router-Tier`. Trusted deployments may opt in.
- The unused `SMART_ROUTER_FAIL_OPEN_MODEL` setting was removed. Learned load/inference failures continue through `SMART_ROUTER_LEARNED_ERROR_FALLBACK`.


<!-- v0.5.0-dashboard-and-evidence -->
## v0.5.0 measured-cost dashboard

Open `http://<smart-router-host>:8080/dashboard`. When `SMART_ROUTER_CLIENT_API_KEY` is configured, the dashboard and its JSON API use the same client authentication as the OpenAI-compatible endpoints.

```env
SMART_ROUTER_COST_LEDGER_ENABLED=true
SMART_ROUTER_COST_DATABASE_PATH=/data/cost-ledger.sqlite3
SMART_ROUTER_PRICING_FILE=/policy/pricing-v0.5.json
SMART_ROUTER_DASHBOARD_ENABLED=true
```

The dashboard reports **real upstream usage only**. USD values are calculated only when usage and tier pricing are both available. The strong-only baseline prices the same measured token counts at the strong-tier rates; it is not a claim that strong would generate identical tokens.

### Provider profile CLI

```bash
smart-router-provider-profile examples/provider-profile-9router-v0.5.json
smart-router-provider-profile examples/provider-profile-omniroute-v0.5.json
```

### Preference-derived labels

```bash
smart-router-preference-build outcomes.jsonl \
  --output preference-training.jsonl \
  --retention 0.95
```

Input rows must contain the **existing complete privacy-safe feature schema** plus measured `quality_by_tier`. The builder emits the existing `schema_version` + `features` + `label` training shape. The format example in `examples/preference-outcomes-format-v0.5.example.jsonl` is illustrative; replace its placeholder features with real Smart Router feature objects before training.

Use the current Operations Center guide and release notes for supported behavior and operational validation.


---

## v0.5.1 control plane

v0.5.1 adds dynamic `fast`/`standard`/`strong`/`coding`/`vision` profiles, the `/control/` operations panel, RBAC, virtual keys and quotas, scoped budgets, policies, knowledge/RAG, persistent memory, agents and teams, plugin registry, audit telemetry, PostgreSQL-ready shared state and a v0.5.1 benchmark reporter. See `../docs/HERMES-OPERATIONS-CENTER-USER-GUIDE-v0.5.9.md` for the current control-plane/operator guide.
