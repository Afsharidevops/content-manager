# Client examples for Smart Router

Ready-to-copy configurations for agent clients that talk to Smart Router.

| Client | File | Copy to |
| --- | --- | --- |
| Codex CLI | `codex-config.toml` | `~/.codex/config.toml` |
| Claude Code | `claude-code.env.example` | source it, or paste the names into the `env` block of `~/.claude/settings.json` |

Replace `https://api.example.com` with the Smart Router origin and
`<SMART_ROUTER_CLIENT_API_KEY>` with the client key from `.env`. Smart Router
answers `/v1/chat/completions`, `/v1/responses`, and `/v1/messages`; the
protocol is chosen by the client, not by the URL.

## Verify before starting the client

```bash
export SR=https://api.example.com
export KEY=<client key>

curl -sS "$SR/health"
curl -sS "$SR/v1/models" -H "Authorization: Bearer $KEY"
curl -sS "$SR/v1/responses" -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"model":"auto","input":"say hi","stream":true}'
curl -sS "$SR/v1/messages" -H "x-api-key: $KEY" \
  -H 'anthropic-version: 2023-06-01' -H 'content-type: application/json' \
  -d '{"model":"auto","max_tokens":64,"messages":[{"role":"user","content":"say hi"}]}'
```

## Model mapping

`auto` routes by policy and is the recommended value everywhere. `auto-fast`,
`auto-standard`, and `auto-strong` force one tier. Any other model name bypasses
Smart Router policy and is forwarded upstream verbatim, so point every model
name a client can request - including background and small/fast models - at a
router alias.

## Requirements

- The display name of the Smart Router service in `docker-compose.yml` is
  `hermes-smart-router`; publish it through the stack reverse proxy and use that
  host name in the client configuration.
- Keep streaming uncompressed: both translated endpoints request
  `accept-encoding: identity` themselves, and a buffering proxy in front of
  Smart Router will stall streaming responses.
- HTTPS or a private network only; do not expose the router port directly.
