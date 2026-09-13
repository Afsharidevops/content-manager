# Multi-agent orchestration

The Operations Center **Orchestrator** page turns one operator task into a
machine-readable plan and runs it across the registered agents. It is the
supervisor layer on top of the existing Agent registry: the Planner decomposes
the work, the Executor path runs each step through an agent, sensitive steps
wait for a human decision, and the Reviewer validates the outcome.

Nothing in this flow executes shell commands inside the router. Tool names in a
plan are declarative metadata: the run record and the audit log show the intent,
and the execution itself belongs to the Execution Broker trust boundary.

## Concepts

| Part | Where it lives | What it does |
| --- | --- | --- |
| Planner | `smart-router/src/smart_router/orchestrator_v60.py` | Builds a JSON plan and assigns every step to an active agent |
| Run / steps | tables `v60_agent_runs`, `v60_agent_run_steps` | Persist goal, plan, per-step status, attempts, output and error |
| Executor | `_run_agent` per step | Runs the step, carries earlier results forward, retries once by default |
| Reviewer | reviewer pass after the last step | Stores `success`/`failure`/`uncertain`, findings and a rollback suggestion |
| Human approval | `awaiting_approval` pause | Stops the run before a sensitive step until an operator approves or rejects |
| History memory | recent completed runs | The planner reuses earlier goals and reviewer summaries |

A step requires approval when the planner flagged it, when the step text matches
a dangerous operation pattern (`kubectl delete`, `kubectl drain`,
`terraform destroy|apply`, `iptables`/`nftables`, `DROP TABLE|DATABASE`,
`TRUNCATE TABLE`, `docker rm|rmi|volume rm|system prune`, `systemctl stop|disable|mask`,
`mkfs`, `dd if=`, `chmod 777`, `fdisk`/`parted`, `userdel`, `kill -9`,
`git push --force`, `DELETE FROM`, `shutdown|reboot|halt|poweroff`), or when a
declared tool is registered as high risk or is not in the plugin registry.

## Run a task

```bash
KEY="$(grep '^SMART_ROUTER_ADMIN_API_KEY=' .env | cut -d= -f2-)"
BASE=http://127.0.0.1:8787

# 1. Plan preview (nothing is stored)
curl -sS "$BASE/api/orchestrations/plan" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"task":"The Kubernetes cluster is unhealthy, find and fix the cause"}' | jq

# 2. Plan and execute in one call
curl -sS "$BASE/api/orchestrations" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{
        "task":"Rotate the expired TLS certificates on the gateway",
        "agent_ids":[1,2],
        "planner_agent_id":null,
        "max_steps":8,
        "approval_mode":"auto",
        "auto_execute":true
      }' | jq

# 3. Inspect, then decide on a paused step
curl -sS "$BASE/api/orchestrations/12" -H "Authorization: Bearer $KEY" | jq '.steps[] | {index,title,status,approval_reason}'
curl -sS -X POST "$BASE/api/orchestrations/12/approve" -H "Authorization: Bearer $KEY" | jq '.status'
curl -sS -X POST "$BASE/api/orchestrations/12/reject"  -H "Authorization: Bearer $KEY" | jq '.status'
```

Requests accept `task`, an optional `agent_ids` pool (empty = every active
agent), `planner_agent_id` (empty = the built-in planner prompt), `max_steps`
(default 8, cap 20), `approval_mode` (`auto`, `always`, `never`),
`planner_profile` (`fast`/`standard`/`strong`/`coding`/`vision`),
`include_history` (default `true`) and `auto_execute` (default `true`).

## Run states

- `planned` - the plan is stored and the run has not started or was paused before step one.
- `running` - a step is executing.
- `awaiting_approval` - the run stopped on a sensitive step; the step waits in `awaiting_approval`.
- `completed` - every step finished and the reviewer did not report `failure`.
- `failed` - a step exhausted its attempts, or the reviewer reported `failure`.
- `rejected` - an operator rejected the pending step; remaining steps are `skipped`.

Step states are `pending`, `running`, `awaiting_approval`, `approved`/`done`,
`failed`, `rejected` and `skipped`. Each step keeps `attempts`, `max_attempts`,
its tool list, the produced output and the last error.

## API surface

| Method and path | Permission | Purpose |
| --- | --- | --- |
| `GET /api/orchestrations` | `panel.read` | List recent runs with progress and review status |
| `POST /api/orchestrations` | `agents.run` | Plan a task (and execute it by default) |
| `POST /api/orchestrations/plan` | `agents.run` | Plan without storing a run |
| `GET /api/orchestrations/{id}` | `panel.read` | Full run, steps, plan and review |
| `DELETE /api/orchestrations/{id}` | `agents.manage` | Remove a finished run and its steps |
| `POST /api/orchestrations/{id}/execute` | `agents.run` | Continue a `planned` run |
| `POST /api/orchestrations/{id}/approve` | `agents.run` | Approve the waiting step and continue |
| `POST /api/orchestrations/{id}/reject` | `agents.run` | Reject the waiting step and stop the run |

Every transition writes an audit event (`orchestration.create`,
`orchestration.plan`, `orchestration.execute`, `orchestration.step.approve`,
`orchestration.step.reject`, `orchestration.delete`) and the Prometheus metrics
`smart_router_orchestration_runs_total` and
`smart_router_orchestration_steps_total`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SMART_ROUTER_ORCHESTRATOR_APPROVAL_MODE` | `auto` | `auto` gates flagged/dangerous steps, `always` gates every step, `never` disables gating |
| `SMART_ROUTER_ORCHESTRATOR_PLANNER_TIER` | `standard` | Capability pool for the built-in planner pass |
| `SMART_ROUTER_ORCHESTRATOR_REVIEWER_TIER` | `strong` | Capability pool for the reviewer pass |

Model selection stays inside the Smart Router: each agent keeps its own
`tier`/`profile`, and the planner and reviewer passes only choose a capability
pool, so cost and health routing still apply.

## Operational notes

- Runs are stored in the same Operations database as the rest of the control
  plane (`SMART_ROUTER_CONTROL_DATABASE_URL`), so the usual backup covers them.
- A run holds an in-process lock while it advances; a single router process is
  assumed, as with the rest of the control plane.
- Planner failures fail the request with `planner_failed`; a planner answer that
  is not valid JSON falls back to a single-step plan and records a note on the
  run so the operator can see why.
- The reviewer never blocks the outcome silently: `uncertain` keeps the run
  `completed` with the verdict attached, while `failure` marks it `failed`.
- `approval_mode: never` removes the gate entirely. Use it only on a trusted
  lab deployment, and remember that the step text still appears in the audit
  trail.
