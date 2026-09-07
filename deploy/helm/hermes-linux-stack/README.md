# Hermes Linux Stack Helm — v0.5.9 foundation

This chart deploys the **Smart Router HA core** with PostgreSQL and Redis, two router replicas, probes, a PDB, security contexts, topology spread, and a baseline NetworkPolicy. It deliberately does **not** claim the full v0.5.9 Helm release gate yet: Hermes, 9router/OmniRoute, Open WebUI, n8n, execution brokers, ingress/TLS and production External Secrets still require full-stack templates and cluster validation.

Create a Kubernetes Secret named `hermes-smart-router-secrets` with keys `hmac-secret`, `admin-api-key`, `client-api-key`, `bootstrap-admin-password`, `postgres-password`, and `redis-password`, then install:

```bash
helm install hermes ./deploy/helm/hermes-linux-stack --namespace hermes --create-namespace
```

For the OmniRoute branch, `values.yaml` already points at the OmniRoute service. For external gateways, override `upstream.baseUrl` and `upstream.healthUrl`.
