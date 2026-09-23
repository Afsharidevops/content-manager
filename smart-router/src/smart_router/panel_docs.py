"""Built-in Operations Center documentation rendered by the Docs page.

The page contains two references: an operator manual for every navigation
area and a complete API guide with ready-to-copy client configurations for
OpenAI-compatible and Anthropic-compatible tools. The HTML is injected into
the panel as a JSON string, so it can contain quotes and backslashes freely.
"""

PANEL_DOCS_HTML = r'''
<div class="split section">
<div class="card">
<h2>1. Sign in, roles and audit</h2>
<p class="muted">Open <span class="badge">/control/</span> and authenticate with the bootstrap admin key (<b>SMART_ROUTER_ADMIN_API_KEY</b>) or an Operations Center user. The browser keeps only the session token; every mutation is written to Observe -&gt; Audit with actor, role, action and target.</p>
<div class="code">Roles: super_admin | admin | operator | analyst | user | read_only
Permissions: panel.read, keys.manage, users.manage, budgets.manage, agents.run, ...
Auth endpoints: POST /control/api/login   GET /control/api/me
OIDC: GET /control/api/auth/oidc/start (configure the provider first)</div>
</div>
<div class="card">
<h2>2. First-run checklist</h2>
<p class="muted">A safe order for a fresh deployment:</p>
<div class="code">1. System        -> confirm mode (observe | route), policy, HA state
2. Providers     -> Discover upstream models
3. Routing       -> map fast / standard / strong profiles
4. Users &amp; Keys -> create one virtual key per client (copy srk_ once)
5. Budgets       -> set cost envelopes and hard stops
6. Agent + Team  -> build, Test run, then wire into workflows
7. Onboarding    -> work through the checklist</div>
</div>
</div>

<div class="split section">
<div class="card">
<h2>3. Observe</h2>
<p class="muted"><b>Overview</b> shows request volume, measured cost, latency, error rate and tier distribution for the selected window. <b>Traces</b> replays one request step by step: authentication, guardrails, routing decision, knowledge retrieval, fallback and the final result. <b>Provider Health</b> tracks health score, success rate, latency EMA and circuit-breaker state per model/route. <b>Audit</b> is the append-only record of administrative and security events (actor, role, action, target).</p>
</div>
<div class="card">
<h2>4. Build</h2>
<p class="muted"><b>Workflows</b> compose agents, teams, tools and approvals on a visual canvas. <b>Agents</b> define system prompt, tier/profile, knowledge, skills, plugins and permissions; use Test run before wiring an agent into anything else. <b>Knowledge Pipelines</b> describe repeatable ingestion graphs. <b>Knowledge</b> is hybrid lexical/vector RAG. <b>Memory</b> stores durable scoped facts, never secrets. <b>Teams</b> run several agents sequentially or in parallel. <b>Orchestrator</b> adds a planner, supervisor, approvals and a reviewer verdict. <b>Prompts</b> keeps versioned prompts, <b>Evaluations</b> datasets and A/B runs, <b>Publish & Monitor</b> links to the runtime surfaces.</p>
</div>
</div>

<div class="split section">
<div class="card">
<h2>5. Tools</h2>
<p class="muted"><b>Skills</b> are reusable instruction/capability packs; suggested skills install with one click, manual or commercial skills carry a license note. <b>Plugins</b> is a permission-reviewed registry of MCP/HTTP/webhook integrations: installing a catalog entry only adds a template, the endpoint and credentials stay explicit. <b>Marketplace</b> lists curated catalog entries. Skill and plugin records never execute arbitrary software by themselves.</p>
</div>
<div class="card">
<h2>6. Routing</h2>
<p class="muted"><b>Routing</b> maps capability profiles to upstream models: <b>model=auto</b> enters automatic selection, an explicit model stays explicit. <b>Router Pipelines</b> builds routing DAGs (classifier, condition, capability/health filter, cost-latency score, load balance, route, retry, fallback, approval). <b>Providers</b> probes the upstream gateway, <b>Model Catalog</b> keeps capabilities and pricing, <b>Policies</b> and <b>Guardrails</b> define allow/deny behaviour, <b>Budgets</b> sets cost envelopes and hard stops.</p>
</div>
</div>

<div class="split section">
<div class="card">
<h2>7. Access</h2>
<p class="muted"><b>Users &amp; Keys</b> manages panel users and virtual API keys. <b>Revoke</b> is reversible and stops authentication immediately; <b>Delete</b> removes the key permanently and is refused while ACL rules or budgets still reference it unless the cascade option is confirmed. <b>Groups</b> collect usernames for ACL subjects. <b>ACLs</b> are fine-grained allow/deny rules; deny always wins. <b>Identity</b> covers OIDC and enterprise directory readiness.</p>
<div class="code">Users &amp; Keys columns: Name | Prefix | Role | RPM | TPM | Daily | Active | actions
Edit limits  -> change RPM/TPM/daily/monthly budget without rotating the key
Revoke       -> DELETE /control/api/keys/{id}
Delete       -> DELETE /control/api/keys/{id}?purge=true[&amp;cascade=true]</div>
</div>
<div class="card">
<h2>8. System</h2>
<p class="muted"><b>Execution &amp; Approvals</b> holds the separate Execution Admin connection, approval policy, Telegram approvers, broker health and audit. <b>Onboarding</b> is the first-run checklist. <b>System</b> changes mode, policy and HA live; UI overrides persist in the Operations DB and <b>Reset to environment</b> removes them. HA requires <b>SMART_ROUTER_REDIS_URL</b>.</p>
<div class="code">mode  : observe (evaluate and log) | route (apply the decision)
policy: heuristic | calibrated | learned
DB    : sqlite:////data/control-v0.5.2.sqlite3 or PostgreSQL
HA    : Redis-backed rate limits, budgets and coordinator</div>
</div>
</div>

<div class="split section">
<div class="card">
<h2>9. Upgrade and backup</h2>
<p class="muted">Keep the data directory (or database) persistent and back it up before a release change. Images are published by CI; the server only pulls.</p>
<div class="code">cp -a data/smart-router/control-v0.5.2.sqlite3 data/smart-router/control-v0.5.2.sqlite3.bak-$(date +%F-%H%M%S)
docker compose --env-file .env pull smart-router
docker compose --env-file .env up -d --no-deps --force-recreate smart-router</div>
</div>
<div class="card">
<h2>10. Troubleshooting the UI</h2>
<div class="code">docker logs --tail 100 hermes-smart-router
./manage.sh router-status
./manage.sh router-system

409 duplicate_*      -> the name already exists
409 group_in_use     -> ACL rules still reference the group (retry with cascade)
409 key_in_use       -> ACL rules or budgets still reference the key
422 invalid_*        -> referenced IDs or field values are invalid
422 HA enable        -> configure Redis first
unexpected override  -> System -> Reset to environment</div>
</div>
</div>

<div class="card section">
<h2>11. API guide: base URLs and authentication</h2>
<p class="muted">Everything the UI does is available over HTTP. Replace <b>HOST</b>/<b>PORT</b> with the published bind address of the router (this deployment publishes <b>192.168.4.222:8787</b>; inside the Docker network the service answers on <b>http://smart-router:8080</b>).</p>
<div class="code">Operations Center : http://HOST:PORT/control/
Flight Deck       : http://HOST:PORT/dashboard
OpenAI clients    : http://HOST:PORT/v1            (chat completions, responses, models, tools)
Anthropic clients : http://HOST:PORT               (clients append /v1/messages)
Content pipeline  : http://HOST:PORT/v1/content/*  (storyboard, scene, timeline, plans, recovery)</div>
<p class="muted">Keys are accepted as <b>Authorization: Bearer &lt;key&gt;</b> or <b>x-api-key: &lt;key&gt;</b> (matching Anthropic clients):</p>
<div class="code">SMART_ROUTER_ADMIN_API_KEY   bootstrap admin key for the Operations Center API
SMART_ROUTER_CLIENT_API_KEY  stack-wide client key created by install.sh
srk_...                      virtual key created in Access -> Users &amp; Keys (shown once)</div>
</div>

<div class="split section">
<div class="card">
<h2>12. OpenAI-compatible API</h2>
<div class="code">GET  /v1/models             upstream models and profiles
GET  /v1/tools              tool registry exposed to agents
POST /v1/chat/completions   drop-in replacement for OpenAI chat
POST /v1/responses          OpenAI Responses API surface</div>
<div class="code">curl -s http://HOST:PORT/v1/chat/completions \
  -H "Authorization: Bearer $HERMES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Summarize this deployment."}]}'

# Force a capability profile for one request (admin/operator, or
# SMART_ROUTER_ALLOW_TIER_OVERRIDES=true):
#   -H "x-router-profile: strong"

# Inject knowledge bases and memory scopes:
#   "metadata":{"hermes":{"knowledge_bases":[1],"rag_limit":4,"project":"production-k8s"}}</div>
</div>
<div class="card">
<h2>13. Anthropic-compatible API</h2>
<div class="code">POST /v1/messages                Anthropic Messages API
POST /v1/messages/count_tokens   token counting</div>
<div class="code">curl -s http://HOST:PORT/v1/messages \
  -H "x-api-key: $HERMES_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","max_tokens":1024,
       "messages":[{"role":"user","content":"Hello from Claude."}]}'</div>
<p class="muted">Streaming, tools and count_tokens follow the same contract as the public Anthropic API; routing, rate limits, budgets and guardrails are identical to the OpenAI surface.</p>
</div>
</div>

<div class="card section">
<h2>14. Content production API</h2>
<p class="muted">Used by the content-manager stack.</p>
<div class="code">GET  /v1/content/agents      seeded Storyboard, Video Director, Media Planner and Recovery agents with their agent_id
POST /v1/content/storyboard  {topic, script, platform, duration, language, style}          -> {storyboard}
POST /v1/content/scene       {topic, storyboard, index, instruction, language}             -> {scene}
POST /v1/content/timeline    {storyboard, aspect_ratio, language}                          -> {timeline}
POST /v1/content/video-plan  storyboard + timeline in one call                             -> {storyboard, timeline}
POST /v1/content/media-plan  {scenes, budget, providers}                                   -> {media_plan}
POST /v1/content/recover     {step, attempt, error, url, page_state}                       -> {decision}</div>
<div class="code">curl -s http://HOST:PORT/v1/content/storyboard \
  -H "Authorization: Bearer $HERMES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"topic":"Container networking","duration":60,"language":"en","platform":"youtube"}'</div>
<p class="muted">Every valid key may call the content endpoints; the operating agent profiles are selected server-side.</p>
</div>

<div class="card section">
<h2>15. Video Studio production jobs</h2>
<p class="muted">The Operations Center stores production jobs under <b>/control/api/production</b>, runs the storyboard and timeline steps through the content agents, and submits render-ready timelines to Media Studio.</p>
<div class="code">GET  /control/api/production/jobs?limit=100&amp;status=timeline_ready      -> [{job}]
POST /control/api/production/jobs  {topic, script, platform, aspect_ratio, language, style} -> {job}
GET  /control/api/production/jobs/{id}                         -> {job with storyboard, timeline, media_plan}
DELETE /control/api/production/jobs/{id}                       -> {ok:true}
POST /control/api/production/jobs/{id}/storyboard              -> {job status=storyboard_created}
POST /control/api/production/jobs/{id}/timeline                -> {job status=timeline_ready}
POST /control/api/production/jobs/{id}/render                  -> {job status=rendering, render:{...}}
GET  /control/api/production/jobs/{id}/render                  -> {job status=rendering|done|failed, render:{...}}
GET  /control/api/production/jobs/{id}/artifact/{name}         -> downloaded artifact bytes</div>
<div class="code">curl -s -X POST http://HOST:PORT/control/api/production/jobs \
  -H "Authorization: Bearer $SMART_ROUTER_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"topic":"Container networking","platform":"youtube","aspect_ratio":"9:16","language":"en"}'

curl -s -X POST http://HOST:PORT/control/api/production/jobs/1/storyboard \
  -H "Authorization: Bearer $SMART_ROUTER_ADMIN_API_KEY"

curl -s -X POST http://HOST:PORT/control/api/production/jobs/1/timeline \
  -H "Authorization: Bearer $SMART_ROUTER_ADMIN_API_KEY"

curl -s -X POST http://HOST:PORT/control/api/production/jobs/1/render \
  -H "Authorization: Bearer $SMART_ROUTER_ADMIN_API_KEY"</div>
<p class="muted">Supported aspect ratios are <b>9:16</b> and <b>16:9</b>. Rendering requires <b>SMART_ROUTER_MEDIA_STUDIO_URL</b>; add <b>SMART_ROUTER_MEDIA_STUDIO_TOKEN</b> when Media Studio enforces bearer-token authentication.</p>
</div>

<div class="split section">
<div class="card">
<h2>16. Operations Center API</h2>
<p class="muted">Base path <b>/control/api</b>. Reads need <b>panel.read</b> or the matching <b>*.read</b> permission; writes need the <b>*.manage</b>/<b>*.run</b> permission for the resource. Listings accept GET, mutations POST/PUT/DELETE.</p>
<div class="code">GET|POST        /control/api/users            PUT|DELETE /control/api/users/{id}
GET|POST        /control/api/groups           PUT|DELETE /control/api/groups/{id}
GET|POST        /control/api/keys             PUT|DELETE /control/api/keys/{id}
GET|POST        /control/api/acls             DELETE    /control/api/acls/{id}
GET|POST        /control/api/budgets          DELETE    /control/api/budgets/{id}
GET|POST        /control/api/policies         PUT|DELETE /control/api/policies/{id}
GET|POST        /control/api/agents           POST /control/api/agents/{id}/run
GET|POST        /control/api/teams            POST /control/api/teams/{id}/run
GET|POST        /control/api/orchestrations   POST /control/api/orchestrations/{id}/execute
GET|POST        /control/api/knowledge        POST /control/api/knowledge/{id}/documents
GET|POST        /control/api/memory           DELETE    /control/api/memory/{id}
GET|POST        /control/api/skills|plugins   POST /control/api/{skills,plugins}/install
GET|POST        /control/api/workflows|router-pipelines|knowledge-pipelines|prompts|datasets|evaluations
GET                 /control/api/traces|audit|summary|provider-health|model-catalog|rate-limits
GET|PUT|DELETE      /control/api/system</div>
<div class="code">curl -s http://HOST:PORT/control/api/keys \
  -H "Authorization: Bearer $SMART_ROUTER_ADMIN_API_KEY"

curl -s -X POST http://HOST:PORT/control/api/keys \
  -H "Authorization: Bearer $SMART_ROUTER_ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"codex-laptop","role":"user","rpm":120,"tpm":2000000}'</div>
</div>
<div class="card">
<h2>17. Virtual key lifecycle</h2>
<div class="code">Create   Access -> Users &amp; Keys -> Create API key
         POST /control/api/keys        (the srk_ value is returned once)
Limits   Access -> Users &amp; Keys -> Edit limits
         PUT  /control/api/keys/{id}   (no rotation required)
Revoke   Access -> Users &amp; Keys -> Revoke
         DELETE /control/api/keys/{id} (soft: authentication stops, row stays)
Delete   Access -> Users &amp; Keys -> Delete
         DELETE /control/api/keys/{id}?purge=true
         DELETE /control/api/keys/{id}?purge=true&amp;cascade=true
         (cascade also removes referencing ACL rules and budgets)</div>
<p class="muted">A key carries role, team, RPM/TPM/daily quotas, a monthly USD envelope and the allowed tiers (<b>fast</b>, <b>standard</b>, <b>strong</b>). Rate limits are enforced by Redis when HA is on, otherwise by the operations database.</p>
</div>
</div>

<div class="split section">
<div class="card">
<h2>17. Codex client configuration</h2>
<p class="muted">Point Codex CLI at the router and give it a virtual key. Create the key first in Access -&gt; Users &amp; Keys.</p>
<div class="code"># ~/.codex/config.toml
model = "auto"
model_provider = "hermes"

[model_providers.hermes]
name = "Hermes Smart Router"
base_url = "https://sr.stack.locallab.ir/v1"
env_key = "HERMES_API_KEY"
experimental_bearer_token = "<SMART_ROUTER_CLIENT_API_KEY>"
wire_api = "responses"     # Codex uses the Responses API at /v1/responses
request_max_retries = 3
stream_max_retries = 2</div>
<div class="code">export HERMES_API_KEY="srk_..."   # add to ~/.bashrc or ~/.zshrc
codex</div>
<p class="muted">With <b>model = "auto"</b> every request is routed automatically; set an explicit upstream model (or a profile override) when a task needs a specific tier.</p>
</div>
<div class="card">
<h2>18. Claude client configuration</h2>
<p class="muted">Claude Code and any Anthropic SDK client can use the router as their endpoint because <b>/v1/messages</b> is implemented natively.</p>
<div class="code"># VS Code — settings.json
{
  "claude": {
    "serverUrl": "https://sr.stack.locallab.ir",
    "serverType": "custom",
    "authToken": "srk_...",
    "model": "auto"
  }
}</div>
<div class="code"># Environment variables (CLI)
export ANTHROPIC_BASE_URL="https://sr.stack.locallab.ir"
export ANTHROPIC_AUTH_TOKEN="srk_..."   # sent as Authorization: Bearer
# or: export ANTHROPIC_API_KEY="srk_..."  # sent as x-api-key
export ANTHROPIC_MODEL="auto"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="auto"
claude</div>
<div class="code"># Python SDK
from anthropic import Anthropic
client = Anthropic(base_url="https://sr.stack.locallab.ir", api_key="srk_...")
client.messages.create(model="auto", max_tokens=512,
                       messages=[{"role": "user", "content": "Hello"}])</div>
<p class="muted">Session persistence, guardrails, budgets and trace recording behave exactly like the OpenAI surface, so a single key can serve both tools. Set <b>ANTHROPIC_DEFAULT_HAIKU_MODEL=auto</b> when Claude Code defaults to Haiku; the router handles model selection.</p>
</div>
</div>

<div class="card section">
<h2>19. Errors and troubleshooting</h2>
<div class="code">401 auth_required      missing or unknown key
403 permission_denied  the key's role cannot use the permission
409 duplicate_*        unique name already exists
409 key_in_use         ACL rules or budgets reference the key (use cascade=true)
422 invalid_*          payload failed validation (see error.details)
429 quota exceeded     rate/budget limit with retry_after_seconds
503 unavailable        control database or Redis is not reachable

Health:  GET /health   GET /ready   GET /router/info   GET /metrics
Traces:  Operations Center -> Observe -> Traces (request id is in the log line)</div>
</div>
'''
