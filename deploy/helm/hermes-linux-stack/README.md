# Content Manager Helm chart

Deploys the Content Manager stack on the Hermes Linux Stack v0.5.9 platform:

- **Smart Router HA core** — `replicaCount` router replicas with probes, a
  PodDisruptionBudget, a NetworkPolicy, an optional HPA, and in-cluster
  PostgreSQL and Redis for sticky routing.
- **Optional in-cluster upstream** — 9router or OmniRoute, with a PVC for its
  accounts and configuration.
- **Optional Content Manager components** — Content Bot, operator panel,
  Media Studio, and an nginx file server for the Instagram media directory.
- **Optional ingress** for the panel and the public media hostname.

Components are disabled by default, so a plain `helm install` behaves like the
router-only foundation chart. `examples/helm/content-stack-values.yaml` enables
the whole stack as an overlay.

## Quick start

```bash
# 1. Router secrets (keys are fixed)
kubectl create namespace content-manager
kubectl -n content-manager create secret generic hermes-smart-router-secrets \
  --from-literal=hmac-secret="$(openssl rand -hex 32)" \
  --from-literal=admin-api-key="$(openssl rand -hex 32)" \
  --from-literal=client-api-key="$(openssl rand -hex 32)" \
  --from-literal=bootstrap-admin-password="$(openssl rand -base64 18)" \
  --from-literal=postgres-password="$(openssl rand -hex 16)" \
  --from-literal=redis-password="$(openssl rand -hex 16)"

# 2. Install the router core
helm install content-manager ./deploy/helm/hermes-linux-stack \
  --namespace content-manager

# 3. Or enable the whole Content Manager stack
helm install content-manager ./deploy/helm/hermes-linux-stack \
  --namespace content-manager -f examples/helm/content-stack-values.yaml
```

`docs/HELM.md` documents every value, the component secrets, publishing the
chart to an OCI registry or a Helm repository, and the free registry options.

## Validate locally

```bash
helm lint deploy/helm/hermes-linux-stack -f examples/helm/content-stack-values.yaml
helm template t deploy/helm/hermes-linux-stack -f examples/helm/content-stack-values.yaml >/dev/null
tests/test-helm-chart.sh
```
