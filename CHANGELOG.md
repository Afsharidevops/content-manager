# Changelog

This file is the canonical Hermes Linux Stack release history from v0.5.2 onward.
Older component-specific history remains in the component release-note files and Git history.

The current runtime release is **v0.5.9**.

## Hermes Linux Stack — v0.5.9 Changelog — 2026-08-13

### v0.5.9 release focus

Visual Flow Connections across Workflow, Agent, Router Pipeline, and Knowledge Pipeline studios, while preserving Hermes execution trust boundaries.

### v0.5.9 release acceptance

Automated candidate, API/persistence, regression, Compose, manifest, and full Smart Router smoke validation passed during release preparation. The release owner explicitly requested early finalization and waived the remaining manual browser light/dark rendering and mouse/trackpad interaction gate. The waived checks are not represented as passed.


- Fixed direct Users, Policies, and Plugins create APIs returning HTTP 500 after commit because audit logging dereferenced expired SQLAlchemy rows outside their sessions.

- Added the v0.5.9 visual-flow implementation work without changing the runtime release version.
- Updated Hermes stack plugins for the current plugin registration API.
- Persist Telegram enablement for `stack-execution-policy` and `stack-package-policy` during installation.
- Updated `manage.sh` runtime diagnostics to verify the current stack plugin toolset names.
- Added regression coverage for Telegram plugin-toolset persistence and runtime registration.
- Implemented v0.5.9 Phase 1 shared graph contract for Workflow and Knowledge studios: port-aware edges, legacy graph normalization, typed compatibility, named outputs, drag-to-connect preview/target validation, selectable/deletable edges, dirty state, and server-side duplicate/self/cycle checks.
- Completed the v0.5.9 visual-flow development candidate across all four studios: Agent and Router shared-canvas integration, persisted agent composition graphs, legacy graph synthesis, named Router branch outputs (classifier/condition/capability/health/approval), branch-aware runtime traversal, edge reconnection, drop-on-empty quick add, undo/redo, pan/zoom/fit controls, keyboard/pointer accessibility, explicit save states, and backward-compatible port-aware persistence.
- Preserved the execution trust boundary: visual orchestration and Telegram UX do not grant Docker, SSH, signing-key, or Execution Admin authority.

---

## Hermes Linux Stack — v0.5.8 Changelog

### Release focus

v0.5.8 is the visual-building and operator-UX release for the 9router branch. It keeps the v0.5.7 trust-separated execution design while making browser connectivity failures diagnosable and adding visual studios for workflows, agents, router pipelines, and knowledge pipelines.

### Execution & Approvals reliability

- Execution Broker target advances to `0.1.3`.
- The Operations Center now probes the Execution Admin `/health` endpoint before attempting key authentication. Network/bind/CORS failures are reported separately from key failures.
- Added `./manage.sh configure-execution-admin-browser ORIGIN [PRIVATE_IPV4]` to configure a private bind plus exact browser origin without printing the Execution Admin key.
- Execution Admin CORS preflight supports the Private Network Access response header for explicitly allowed origins.
- The key remains browser-memory-only and is still sent directly from the operator browser to Execution Admin; Smart Router does not receive it.
- Wildcard/public bind shortcuts are intentionally refused by the helper.

### Operations Center redesign

- Reworked dark and light palettes around semantic surface/text/border tokens.
- Reorganized navigation into Observe, Build, Tools, Routing, Access, and System.
- Added Dify-inspired visual interaction patterns without copying Dify branding or assets.
- Added Workflow Studio with draggable nodes, visible edges, node inspector, references, and save/edit lifecycle.
- Added Agent Studio with visible Input → Knowledge → Agent → Tools → Answer path and configuration inspector.
- Added Router Pipeline Studio for conditions, capability/health filters, scoring, load balance, route, retry, fallback, and approval stages.
- Added Knowledge Pipeline Studio and persistent `v58_knowledge_pipelines` registry.
- Added Publish & Monitor workspace that links API, Flight Deck, agent testing, and runtime status.
- Improved Flight Deck light-mode surfaces, sidebar, controls, and status pills.

### Data and API

- Smart Router runtime/control schema marker advances in place to `0.5.8`.
- Compatibility database filename remains `control-v0.5.2.sqlite3`; persistent Operations Center state is upgraded in place.
- New Operations API routes:
  - `GET/POST /control/api/knowledge-pipelines`
  - `PUT/DELETE /control/api/knowledge-pipelines/{id}`
- Knowledge pipeline graph validation accepts only managed ingestion/indexing node types and validates node IDs, edge references, and graph limits.

### Image targets

- Smart Router: `afsharidevops/hermes-smart-router:0.5.8`
- Execution Broker: `afsharidevops/hermes-execution-broker:0.1.3`
- Intended platforms: `linux/amd64`, `linux/arm64`

### Validation performed in packaging environment

- Smart Router pytest suite: 96 passed per branch.
- Execution Admin focused unittest suite: 5 passed per branch.
- `manage.sh` UX tests: passed per branch.
- `bash -n` for `manage.sh` and `install.sh`: passed.
- Python compilation for modified Smart Router UI/control files: passed.
- Operations Center JavaScript syntax (`node --check`): passed.

The broader root Python test discovery also contains environment-dependent tests requiring `ssh-keygen` and a fully usable npm prefix. Those checks could not complete in this packaging environment and should be run in the normal CI/release host before production publication.

### Security invariant

UI convenience must not collapse execution authority into Smart Router. Execution Admin continues to exclude the Ed25519 approval-signing private key, Docker socket, and SSH private credentials, while Smart Router continues to exclude the Execution Admin key and execution approval secrets.

### Post-release private-ingress hotfix

- Execution Admin now joins a dedicated `execution-admin-ingress-net` in addition to the internal `execution-control-net`.
- The control network remains `internal: true`; only Execution Admin receives the host-ingress bridge.
- This allows `${EXECUTION_ADMIN_BIND_IP}:${EXECUTION_ADMIN_PORT}:8752` to be actually published on Docker while preserving broker isolation.
- Smart Router and Execution Broker application images are unchanged by this Compose-only networking fix.
- Smart Router publishing documentation/workflow now uses only the plain version tag and `latest`; no `v<version>` or SHA alias is emitted.
- `MANIFEST.sha256` is regenerated from repository-tracked release files, removing stale ignored cache entries that are not present in a fresh clone.

---

## Hermes Linux Stack — v0.5.7 Changelog

### Focus

v0.5.7 integrates secure execution administration with the v0.5.6 Operations Center without collapsing the execution-broker trust boundaries.

### Execution & Approvals

- Added **System → Execution & Approvals** to Hermes Operations Center.
- Added `execution-admin` mode to Hermes Execution Broker `0.1.2`.
- Operations Center talks directly from the operator browser to the separate Execution Admin endpoint with a separate admin key.
- Smart Router backend does not receive the Execution Admin key or dedicated Telegram approval-bot token.
- Execution Admin does not mount the Ed25519 approval signing key, Docker socket, or SSH private credentials.
- Added live redacted health for approver, Docker broker and SSH broker.
- Added live enable/disable policy for already-deployed `local`, `docker`, and `ssh` execution capabilities.
- Added Telegram execution approver management, constrained to IDs already present in `TELEGRAM_ALLOWED_USERS`.
- Added write-only dedicated approval-bot token replacement. The token is never returned by the API.
- Added protection preventing the execution approval bot from reusing the Hermes Telegram bot token when the Hermes token hash is synchronized.
- Added broker control-secret rotation from the separate admin boundary.
- Added redacted SSH profile listing without exposing private keys/passwords.
- Added execution-admin audit events.
- Every execution policy/admin mutation increments the policy generation, invalidating older pending capabilities/approvals.

### Dynamic execution policy

- Added `EXECUTION_FEATURES_FILE` support to the Hermes execution policy plugin and execution brokers.
- Added `EXECUTION_POLICY_GENERATION_FILE` support to Docker, SSH and approver modes.
- Policy files are bind-mounted and rewritten in place to preserve host ownership and permissions.
- Existing environment variables remain compatibility fallbacks.
- First-time broker deployment remains a host `manage.sh` operation; the UI does not need Docker-socket authority.

### Management commands

Added:

```text
./manage.sh enable-execution-admin
./manage.sh disable-execution-admin
./manage.sh execution-admin-status
./manage.sh show-execution-admin-key
./manage.sh rotate-execution-admin-key
```

The existing execution commands remain supported.

### Security defaults

- Execution Admin binds to `127.0.0.1:8752` by default.
- Browser CORS uses an exact allowlist (`EXECUTION_ADMIN_ALLOWED_ORIGINS`); wildcard origins are not used.
- The admin credential is separate from Smart Router authentication.
- Operations Center keeps the Execution Admin key only in page memory; it is not written to localStorage.
- Bot token readback is not implemented.
- Signing-key rotation and SSH credential creation/removal remain local operator/CLI operations.

### v0.5.6 platform foundations retained

v0.5.7 retains the v0.5.6 light/dark UI, lifecycle fixes, hybrid vector RAG/pgvector support, Flight Deck traces, guardrails, router pipelines, workflows, prompt versioning, datasets/evaluations, model catalog, marketplace/onboarding and HA foundations.

### Compatibility

- Smart Router runtime/schema marker: `0.5.7`.
- Existing `control-v0.5.2.sqlite3` compatibility filename is preserved and upgraded in place.
- Smart Router image target: `afsharidevops/hermes-smart-router:0.5.7`.
- Execution Broker image target: `afsharidevops/hermes-execution-broker:0.1.2`.
- Branches: `main` (9router) and `hermes-omniroute-linux-stack` (OmniRoute).

---

## Hermes Linux Stack v0.5.6 — Platform Foundations

v0.5.6 advances Hermes from an operator-focused Smart Router into a broader self-hosted AI infrastructure platform while preserving the existing `control-v0.5.2.sqlite3` compatibility filename and explicit-model pass-through behavior.

### Major additions

- Light/dark themes in Hermes Operations Center and Flight Deck, persisted in the browser.
- Consistent lifecycle UX: Agents, Teams, and Groups use reversible Enable/Disable controls; permanent deletion is a separate destructive action. Group purge protects ACL references unless explicit cascade deletion is requested. Plugins and Skills have explicit enable/disable and permanent removal controls.
- Hybrid Knowledge/RAG with lexical + vector retrieval, embedding indexing, score fusion/reranking, PostgreSQL `pgvector` acceleration where available, and a deterministic portable vector fallback for offline/development use.
- Full request trace records for request/auth/authorization/guardrails/RAG/classification/routing/retry/fallback/result stages, visible in Flight Deck and Operations Center.
- Guardrail engine foundations for prompt-injection indicators, PII indicators, content deny rules, tool allow-lists, and high-risk tool confirmation policy. Modes: `off`, `audit`, `enforce`.
- Advanced router pipeline definitions with conditions, route stages, load balancing, retries, and fallback model chains.
- Workflow graph registry and visual workflow preview for Agent/Team orchestration.
- Prompt registry with immutable versions, activation/rollback, notes, and history.
- Evaluation datasets, dataset items, and A/B evaluation-run definitions.
- Model catalog synchronized from the upstream `/models` endpoint with capability/context/pricing/health/latency metadata where available.
- Plugin/Skill marketplace view built on the safe catalog/registry model.
- First-run Operations Center onboarding flow.
- PostgreSQL + pgvector + Redis + two-router HA Compose example and smoke/load-test tooling.
- Enterprise identity readiness page. OIDC remains the completed interactive login path; LDAP/SAML/SCIM are connector/provisioning foundations and require deployment-specific integration before production use.
- Static public-docs starter with release screenshots and demo deployment example.

### Compatibility and non-regression rules

- `model=auto` enters Smart Router automatic selection.
- Explicit upstream model IDs pass through unchanged.
- Existing SQLite data is upgraded in place; the compatibility DB filename is not renamed simply because the software version changes.
- Catalog plugin installation does not download/execute arbitrary untrusted code. Lifecycle state, permissions metadata, endpoint configuration, and safe registration remain controlled by the operator.

### Production notes

For real semantic vector RAG, configure an embeddings endpoint and use PostgreSQL with the `vector` extension. The built-in deterministic embedding fallback is useful for tests/offline operation but is not a substitute for a production embedding model. Run the HA smoke and load-test scripts against your own infrastructure before calling a deployment production-HA certified.

---

## Hermes Linux Stack v0.5.5

### Operations Center runtime controls, agent lifecycle, skills, groups, and built-in docs

v0.5.5 is an operator-control and usability release built on v0.5.4. It preserves the existing Smart Router data directory and the default `control-v0.5.2.sqlite3` compatibility filename while upgrading the Operations Center schema in place to `0.5.5`.

#### Smart Router / Operations Center

- Live UI editing for `router_mode`: `observe` or `route`.
- Live UI editing for `router_policy`: `heuristic`, `calibrated`, or `learned`.
- UI control for HA mode with a guard that requires Redis before HA can be enabled.
- Runtime UI settings persist in the Operations database and override startup environment values until **Reset to environment** is used.
- System page now reports the schema version and explains why the default DB filename can remain `control-v0.5.2.sqlite3`.
- OIDC login button corrected to the actual `/api/auth/oidc/start` route.
- ACL UI corrected to use backend subject type `virtual_key`; `group` subjects are now exposed.

#### Agents

- Create Agent errors are shown instead of failing silently.
- Agent names and referenced Knowledge/Plugin/Skill IDs are validated.
- Agents can be edited in the UI.
- Agents can be disabled reversibly or permanently deleted.
- Agent forms use existing Knowledge, Skill, and Plugin records instead of requiring blind CSV IDs.

#### Skills and plugins

- New reusable Skill registry with curated suggestions for Linux, Docker, networking, MikroTik, automation safety, and incident response.
- Skills can be installed from the built-in catalog or registered manually, including commercial/license metadata without storing license secrets.
- Skills assigned to an agent are injected into that agent's system context during runs.
- Suggested Plugin catalog can install safe registry templates for GitHub MCP, read-only PostgreSQL, Kubernetes observation, and MikroTik observation.
- Plugin catalog installation does not download or execute arbitrary code and installs suggested entries disabled by default.

#### Access groups

- New Access → Groups menu.
- Groups contain Operations Center usernames.
- ACL rules with `subject_type=group` now resolve real group membership.

#### Help and documentation

- New built-in **Docs** menu covers routing, system controls, users/keys/groups/ACLs, Knowledge, Memory, Agents, Skills, Plugins, Teams, upgrades, backups, and troubleshooting examples.
- Memory page now explains scope behavior and warns against using memory as a secret store.

#### Database compatibility

The default filename remains:

```text
sqlite:////data/control-v0.5.2.sqlite3
```

This is intentional. v0.5.5 creates its new tables in the same database and updates the `schema_versions` marker to `0.5.5`, preserving v0.5.1/v0.5.2-era users, routes, keys, policies, budgets, knowledge, memory, agents, teams, plugins, ACLs, audit, and outcome records.

New v0.5.5 tables include:

```text
v55_runtime_settings
v55_access_groups
v55_skills
v55_agent_skills
```

Back up the persistent Smart Router database before any production upgrade.

---

## v0.5.4 change summary — 9router

- Fixed the hidden 200,000 TPM ceiling on `SMART_ROUTER_CLIENT_API_KEY`; trusted stack-client RPM/TPM/daily limits are explicit (2,000,000 TPM default).
- Local quota 429 responses now identify Smart Router as the source, include current/limit/estimated values, and return `Retry-After`.
- Redis/HA quota checks are atomic: denied requests no longer consume additional quota.
- Virtual API-key RPM/TPM/daily limits can be edited in-place in `/control/` without rotating the key.
- Visible **Control Plane** naming is replaced by **Hermes Operations Center**; `/control/` and legacy `SMART_ROUTER_CONTROL_*` variables stay compatible.
- RAG knowledge tables can share the Operations DB or use a separate SQLite/PostgreSQL database through `SMART_ROUTER_KNOWLEDGE_DATABASE_URL`.
- The Operations Center shows RAG storage health/backend and labels the built-in retriever accurately as lexical.
- The persistent default filename remains `control-v0.5.2.sqlite3` so upgrades preserve state.
- Runtime/package/image/chart defaults are `0.5.4`.

---

## v0.5.3 change summary — 9router

### User experience

- `./manage.sh` now opens a grouped interactive manager by default.
- Added dedicated Services, Smart Router, Hermes, n8n, Execution, Maintenance, and Security menu groups.
- Smart Router mode/policy/time-window/feature choices are shown as numbered choices instead of requiring memorized values.
- Direct v0.5.2 commands remain backward compatible for scripts and automation.

### Hermes Smart Router Flight Deck

- Redesigned `/dashboard` as the Hermes Flight Deck with access state, time-window and auto-refresh selectors, routing-flow explanation, telemetry quality, route mix, and explicit zero/pricing states.
- Redesigned `/control/` navigation into Observe, Routing, Access, Intelligence, and System groups.
- Added dropdown/select controls for common finite-value choices such as roles, budget scopes, ACL effects, agent profiles, team strategy, plugin kind/risk, and API-key tiers.
- Dashboard and Control Plane link to each other.

### Compatibility

- Runtime/package version is 0.5.3.
- Fresh installs pin `afsharidevops/hermes-smart-router:0.5.3`.
- Existing v0.5.2 Control Plane SQLite/schema naming is intentionally preserved for in-place data compatibility.
- Corrected the installer status message so it reports the selected Smart Router mode instead of always saying observation mode.

---

## v0.5.2 change summary — main

- fixed router-info endpoint and retained compatibility alias
- hardened installer secret rotation and Smart Router client-key synchronization
- removed fixed Docker GID assumption
- enabled control/client auth by default and disabled Open WebUI signup by default
- made application image tags `.env`-overrideable while retaining `latest`/`main` defaults
- added provider health/circuit breakers, shared Redis state/stickiness, OIDC/ACL/secrets foundations, outcome capture and provider quality metadata
- added doctor/backup/restore/rollback/image-lock operations
- added HA Compose and Helm foundations plus security CI
- reconciled package version/documentation to Smart Router v0.5.2

See `docs/HERMES-SMART-ROUTER-v0.5.2-IMPLEMENTATION-STATUS.md` for items still open and not claimed complete.

### Post-review cleanup

- Replaced the Docker execution GID 0 fallback with fail-closed sentinel GID 65534; `install.sh` still detects the actual Docker socket GID when available.
- Removed a dead duplicate `/router/info` handler definition.
- Deduplicated Redis/Authlib dependency constraints to their prior effective versions.
- Removed duplicate long-range planning documentation from the active release tree.
- Updated smoke validation for configurable Smart Router image tags (`latest` by default, pinnable via `.env`).
