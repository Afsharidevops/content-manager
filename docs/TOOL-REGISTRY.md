# Shared tool registry

`content/config/tools.json` is the single place that lists the remote tools
the content stack may call: MCP servers, OpenAPI services, and plain HTTP
endpoints. The Content Bot, the Smart Router, and the n8n bootstrap read the
same file, so a new tool is added once.

The repository copy is the default. Each deployment edits its own working
copy at `data/content-manager/config/tools.json`; the interactive installer
seeds it on first run and never overwrites operator edits.

## File shape

```json
{
  "schema_version": 1,
  "tools": [
    {
      "id": "media-studio",
      "title": "Media Studio",
      "kind": "openapi",
      "base_url": "http://media-studio:8850",
      "spec_url": "http://media-studio:8850/openapi.json",
      "transport": "http",
      "auth": { "type": "bearer", "env": "MEDIA_STUDIO_API_TOKEN" },
      "capabilities": ["media.image", "media.video"],
      "consumers": ["bot", "router", "n8n"],
      "notes": "Job queue for images, videos, and uploaded-clip edits."
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `schema_version` | Must be `1`. |
| `id` | Unique key used by logs, chat replies, and consumer filters. |
| `kind` | `mcp`, `openapi`, or `http`. |
| `url` | Address of an MCP server (required for `kind: "mcp"`). |
| `base_url` | Service root (required for `kind: "http"`, and for `openapi` when no spec is published). |
| `spec_url` | OpenAPI document (the Media Studio entry points at `/openapi.json`). |
| `transport` | `stdio`, `sse`, `streamable-http`, or `http`. |
| `auth.type` | `none`, `bearer`, `header`, or `query`. |
| `auth.env` | Name of the environment variable that holds the credential. Never put the secret in this file. |
| `capabilities` | Free-form labels such as `media.image`, `llm.chat`, `search.web`. |
| `consumers` | Services allowed to call the tool: `bot`, `router`, `n8n`, `media-studio`. |
| `notes` | Human context shown by the bot and the bootstrap report. |

`base_url`, `spec_url`, and `url` may contain `${ENV_NAME}` placeholders; each
runtime substitutes them from its own environment, so one registry works for
a split deployment (a laptop pointing at a remote Media Studio keeps the same
`id` and capabilities).

## Who reads it, and how

- **Content Bot** loads `tools.json` from its policy directory
  (`CONTENT_POLICY_DIR`, default `/policy`). `/tools` lists the entries whose
  `consumers` include `bot`, with the credential variable and whether it is
  set; `/status` shows a one-line summary. A missing or invalid file never
  stops the bot: it logs a warning and reports "not configured".
- **Smart Router** reads `SMART_ROUTER_TOOLS_REGISTRY` (default
  `/policy-content/tools.json`, mounted from the Content Manager config
  directory) and serves the entries marked for `router` on `GET /v1/tools`
  using the same client authentication as `GET /v1/models`.
- **n8n** bootstrap (`./manage.sh bootstrap-n8n`, `scripts/bootstrap-n8n.mjs`)
  reads `N8N_TOOLS_REGISTRY` (manage.sh mounts the config directory at
  `/tools` and sets `/tools/tools.json`) and reports the entries marked for
  `n8n` in its JSON summary, including any missing credential variable. The
  registry never changes the managed workflows by itself; turning entries into
  workflow tool nodes is the Phase 3 connector.

Adding a tool touches one file; every consumer sees the new entry the next
time it starts (or, for the router, on the next request).
