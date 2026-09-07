# Hermes Operations Center User Guide — v0.5.9

## Navigation

v0.5.9 groups the Operations Center around operator intent:

- **Observe** — overview, traces, provider health, audit.
- **Build** — workflows, agents, knowledge pipelines, knowledge, memory, teams, prompts, evaluations, publish/monitor.
- **Tools** — skills, plugins, marketplace.
- **Routing** — routes, router pipelines, providers, model catalog, policies, guardrails, budgets.
- **Access** — users/keys, groups, ACLs, identity.
- **System** — execution/approvals, onboarding, docs, system state.

## Visual studios

**Workflow Studio** lays out input, Agent/Team, Knowledge, Skill/Plugin, approval, branch, parallel, and output nodes on a visible canvas. Nodes are draggable; the inspector edits labels, references, config JSON, and edges. Saving still uses the validated workflow registry.

**Agent Studio** makes the common path visible as Input → Knowledge → Agent → Tools → Answer. The right-side inspector configures tier/profile, Knowledge, Skills, Plugins, system instruction, and lifecycle state.

**Router Pipeline Studio** presents validated routing stages as a visible lane. Stage JSON is still available in the inspector for precise configuration.

**Knowledge Pipeline Studio** stores reusable ingestion/indexing graph definitions including data source, extract, transform, chunk, embedding, index, Knowledge base, Q&A, and output nodes. It does not silently ingest external content or execute untrusted code merely by browsing/editing the graph.

## Execution & Approvals connection

The Execution Admin service remains separate from Smart Router. For a browser on another machine, loopback binding is not reachable. v0.5.9 first checks `/health`, then checks the key, so a network/CORS/bind error is no longer presented as if the key were wrong.

On the server, configure the exact browser origin and private bind:

```bash
./manage.sh configure-execution-admin-browser http://YOUR_PRIVATE_SERVER_IP:8787 YOUR_PRIVATE_SERVER_IP
./manage.sh execution-admin-status
./manage.sh show-execution-admin-key
```

The helper refuses wildcard/public binds and never prints the key. `show-execution-admin-key` remains interactive-only. The key stays in page JavaScript memory and is sent directly to Execution Admin.

## Theme

Light mode now uses dedicated semantic surfaces, text, borders, inputs, tables, cards, canvas, and sidebar variables instead of reusing dark-theme panel backgrounds. Flight Deck uses the same principle for its main surfaces and status controls.

## Upgrade note

The control schema advances in place to `0.5.9`; the compatibility SQLite filename may remain `control-v0.5.2.sqlite3`. Back up and preserve `data/smart-router/` and `data/stack-secrets/` during normal upgrades.

## Execution Admin network layout

For remote private-browser administration, Execution Admin uses two Docker networks:

```text
execution-control-net          internal=true; broker/admin control traffic
execution-admin-ingress-net   normal bridge; Execution Admin only
```

Do not attach the Docker broker, SSH broker, approver, or Smart Router to `execution-admin-ingress-net`, and do not disable `internal: true` on the control network. After configuration, `docker port hermes-execution-admin` should show the exact private bind, for example `192.168.85.243:8752`.


## v0.5.9 Visual Flow Connections

Workflow Studio, Agent Studio, Router Pipeline Studio, and Knowledge Pipeline Studio share the v0.5.9 port-aware graph engine.

The editor supports output/input handles, live connection previews, typed target validation, named branch outputs, edge selection/deletion/reconnection, drop-on-empty quick add, undo/redo, pan/zoom/fit, and port-aware save/reload.

Visual graph connections define orchestration only. They do not grant Docker, SSH, package-install, signing-key, or Execution Admin authority. Approval and privileged execution remain enforced by the separate Hermes approval/signing/broker trust boundaries.
