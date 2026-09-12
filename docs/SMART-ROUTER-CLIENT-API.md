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

## Wire protocols

One router serves three client protocols. Routing, budget, sticky-session and
capability policy run in the same path for all of them.

| Endpoint | Protocol | Used by |
| --- | --- | --- |
| `POST /v1/chat/completions` | OpenAI Chat Completions | most OpenAI-compatible clients |
| `POST /v1/responses` | OpenAI Responses | Codex (`wire_api = "responses"`) |
| `POST /v1/messages` | Anthropic Messages | Claude Code and Anthropic SDK clients |
| `POST /v1/messages/count_tokens` | Anthropic token count | Claude Code context accounting |
| `GET /v1/models` | OpenAI model list | answers in the Anthropic shape when the client asks for it |

`/v1/responses` and `/v1/messages` translate both directions, including
streaming, tool calls, images, and usage reporting, so tool-using agents keep
working instead of degrading to plain text.

### Codex

Ready-to-copy file: `examples/clients/codex-config.toml`.

```toml
model = "auto"
model_provider = "smart-router"
model_reasoning_effort = "high"

[model_providers.smart-router]
name = "Smart Router"
base_url = "https://api.example.com/v1"
wire_api = "responses"
env_key = "SMART_ROUTER_CLIENT_API_KEY"
```

`export SMART_ROUTER_CLIENT_API_KEY=<client key>` before starting Codex, or
replace `env_key` with `experimental_bearer_token = "<client key>"` to keep the
credential in the config file. `wire_api = "chat"` also works and uses
`/v1/chat/completions`. `model = "auto"` routes by policy; `auto-fast`,
`auto-standard`, and `auto-strong` force a tier.

### Claude Code

Ready-to-copy file: `examples/clients/claude-code.env.example`.

```bash
export ANTHROPIC_BASE_URL=https://api.example.com
export ANTHROPIC_AUTH_TOKEN="<client key>"
export ANTHROPIC_MODEL=auto
export ANTHROPIC_SMALL_FAST_MODEL=auto
export ANTHROPIC_DEFAULT_HAIKU_MODEL=auto
export ANTHROPIC_DEFAULT_SONNET_MODEL=auto
export ANTHROPIC_DEFAULT_OPUS_MODEL=auto
```

`ANTHROPIC_BASE_URL` is the origin only; the client appends `/v1/messages`
itself. Use `ANTHROPIC_API_KEY` instead of `ANTHROPIC_AUTH_TOKEN` if a client
can only send `x-api-key`; Smart Router accepts either header. Map every model
alias Claude Code can request (`ANTHROPIC_MODEL`, the small/fast model, and the
per-tier defaults) onto a router alias, otherwise a `claude-*` model name is
forwarded upstream verbatim as an explicit model.

Claude Code warns that `auto` is not a model it recognizes and assumes a 200k
context window. Set `CLAUDE_CODE_MAX_CONTEXT_TOKENS` to the real window of the
tier behind the alias, or use the client's `modelOverrides` setting, if
auto-compaction should use the true limit.

## Client examples

`examples/clients/` holds both files plus a short setup and verification checklist.

## Test

```bash
SR=https://api.example.com
KEY="$SMART_ROUTER_CLIENT_API_KEY"

curl -sS "$SR/health"
curl -sS "$SR/v1/models" -H "Authorization: Bearer $KEY"
curl -sS "$SR/v1/responses" -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"model":"auto","input":"say hi","stream":true}'
curl -sS "$SR/v1/messages" -H "x-api-key: $KEY" \
  -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
  -d '{"model":"auto","max_tokens":64,"messages":[{"role":"user","content":"say hi"}]}'
```

The same commands are kept next to the client files in
`examples/clients/README.md`; both use shell variables on purpose, so no header
in the documentation ever looks like a committed credential.

For multiple independent customers, put an API gateway/identity-aware proxy in front for per-client keys, quotas, revocation and audit identity.
