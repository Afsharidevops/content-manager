# OpenAI-compatible client API — Smart Router v0.3.1

Smart Router is the API entry point for external OpenAI-compatible applications. Clients should use the Smart Router, not 9router directly.

Typical client settings:

```text
API type: OpenAI-compatible
Base URL: https://api.example.com/v1
API key: <SMART_ROUTER_CLIENT_API_KEY>
Model: auto
```

Aliases: `auto`, `auto-fast`, `auto-standard`, `auto-strong`. Prefer `auto` for normal use. Hard tool, vision, context, sticky-session and output-budget rules remain authoritative.

## Credentials

Generate a persistent router HMAC secret once:

```bash
openssl rand -hex 32
```

Set it privately as `SMART_ROUTER_HMAC_SECRET`. Do not reuse it as a client credential. For public client access, optionally set a separate `SMART_ROUTER_CLIENT_API_KEY`. If the downstream gateway requires a bearer key, set `SMART_ROUTER_UPSTREAM_API_KEY`; client credentials terminate at Smart Router and are not forwarded.

## Backend wiring for this branch

```env
SMART_ROUTER_UPSTREAM_BASE_URL=http://nine-router:20128/v1
SMART_ROUTER_UPSTREAM_HEALTH_URL=http://nine-router:20128/api/health
SMART_ROUTER_FAST_MODEL=combo-fast
SMART_ROUTER_STANDARD_MODEL=combo-standard
SMART_ROUTER_STRONG_MODEL=combo-strong
```

## Test

```bash
curl https://api.example.com/health
curl https://api.example.com/v1/models -H 'Authorization: Bearer YOUR_CLIENT_KEY'
```

For multiple independent customers, put an API gateway/identity-aware proxy in front for per-client keys, quotas, revocation and audit identity.
