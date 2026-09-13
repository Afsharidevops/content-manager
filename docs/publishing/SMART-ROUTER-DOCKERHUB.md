# Hermes Smart Router v0.6.0 — Docker Hub

## Images

```text
afsharidevops/hermes-smart-router:0.6.0
afsharidevops/hermes-smart-router:latest
```

Supported runtime platforms:

```text
linux/amd64
linux/arm64
```

Do not publish a redundant `v0.6.0` Docker tag.

## How the image gets published

Pushing to `main` with a change under `smart-router/**` (or to this workflow
file) runs `publish-smart-router.yml`: it tests the package and the benchmark
pipeline, builds `linux/amd64` and `linux/arm64`, pushes the version tag plus
`latest`, and verifies both platforms in the published manifest.

The version comes from `smart-router/pyproject.toml`. Bump that version in the
same change that alters the runtime, otherwise the new code is published under
the previous tag and deployments that pin a version will not see it. A manual
`workflow_dispatch` run may pass an explicit version, which must match the
package version.

## Release focus

v0.6.0 adds the multi-agent Orchestrator to the Operations Center: a planner
builds a validated machine-readable plan, the supervisor runs the steps through
the registered agents, sensitive steps wait for an approve/reject decision, and
a reviewer records a verdict with an optional rollback suggestion. Runs and
steps persist in the control-plane database (`v60_agent_runs`,
`v60_agent_run_steps`) and the planner reuses recent runs as history. The
console itself moves to the Content Console design language: the shared
gradient palette, glass surfaces, and the LocalLab brand mark. See
`../../docs/ORCHESTRATION.md` for the API and the approval policy.

v0.5.9 is the Visual Flow Connections release. Workflow Studio, Agent Studio, Router Pipeline Studio, and Knowledge Pipeline Studio share a port-aware graph engine with drag-to-connect, named outputs, edge validation/editing, quick add, undo/redo, pan/zoom/fit, and backward-compatible graph persistence.

The release also fixes direct Users, Policies, and Plugins create APIs that could return HTTP 500 when audit logging dereferenced expired SQLAlchemy rows after their sessions closed.

## Execution boundary

The Smart Router image does not receive the Execution Admin key, approval-signing private key, Docker socket, or SSH credentials. Visual Approval nodes and `approved` edges describe orchestration only and do not constitute execution authorization.

Execution Broker remains separately versioned at:

```text
afsharidevops/hermes-execution-broker:0.1.3
```
