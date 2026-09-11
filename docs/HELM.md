# Helm chart: Content Manager on Kubernetes

`deploy/helm/hermes-linux-stack` packages the stack for Kubernetes. The chart
keeps the Smart Router HA core enabled by default and treats every Content
Manager component as an opt-in module, so the same chart serves both a
router-only cluster and the full content pipeline.

Two versions move together:

| Field | Meaning | Where it changes |
| --- | --- | --- |
| `Chart.yaml: version` | Chart packaging version (semver) | Bump on every chart change |
| `Chart.yaml: appVersion` | Platform version this chart was validated against | Tracks the repository `VERSION` file |

`tests/test-helm-chart.sh` verifies that `appVersion` matches `VERSION` and
that the chart lints and renders, including the full-stack overlay.

## What the chart deploys

| Component | Default | Resources |
| --- | --- | --- |
| Smart Router HA core | enabled | Deployment (2 replicas), Service, PDB, NetworkPolicy, optional HPA |
| PostgreSQL | enabled | StatefulSet + Service + PVC |
| Redis | enabled | StatefulSet + Service + PVC |
| 9router or OmniRoute | disabled | Deployment + Service + PVC |
| Content Bot | disabled | Deployment + PVC (+ optional policy ConfigMap) |
| Operator panel | disabled | Deployment + Service + PVC |
| Media Studio | disabled | Deployment + Service + PVC |
| Media file server | disabled | nginx Deployment + Service over the Content Bot volume |
| Ingress | disabled | One Ingress for the hosts you list |

## Requirements

- Kubernetes 1.27 or newer with a default StorageClass (ReadWriteOnce).
- Helm 3.14+ or Helm 4.
- An ingress controller (for example ingress-nginx or Traefik) if you enable
  `ingress`.
- Images from Docker Hub: `afsharidevops/hermes-smart-router`,
  `afsharidevops/content-bot`, `afsharidevops/content-panel`,
  `afsharidevops/media-studio`, plus `decolua/9router`,
  `diegosouzapw/omniroute`, `postgres`, `redis`, and `nginx` when those
  components are enabled. Use `imagePullSecrets` when the registry needs
  authentication.

## Installing

```bash
kubectl create namespace content-manager

# Router secrets: key names are fixed by the templates.
kubectl -n content-manager create secret generic hermes-smart-router-secrets \
  --from-literal=hmac-secret="$(openssl rand -hex 32)" \
  --from-literal=admin-api-key="$(openssl rand -hex 32)" \
  --from-literal=client-api-key="$(openssl rand -hex 32)" \
  --from-literal=bootstrap-admin-password="$(openssl rand -base64 18)" \
  --from-literal=postgres-password="$(openssl rand -hex 16)" \
  --from-literal=redis-password="$(openssl rand -hex 16)"

helm install content-manager ./deploy/helm/hermes-linux-stack \
  --namespace content-manager
```

Upgrades and rollbacks are ordinary Helm operations:

```bash
helm upgrade content-manager ./deploy/helm/hermes-linux-stack -n content-manager
helm rollback content-manager 1 -n content-manager
helm uninstall content-manager -n content-manager
```

`helm uninstall` keeps PVCs; delete them explicitly when the data is no longer
needed. Kubernetes data and the Compose data directory are different stores:
moving an existing installation into the cluster means copying the directories
described in `docs/BACKUP-RESTORE.md` into the matching PVCs.

### TLS and external reverse proxies

`ingress.tls` accepts three shapes:

| Value | Result |
| --- | --- |
| `tls: []` (default) or `tls: false` | No TLS block at the ingress; the hostname is served over plain HTTP |
| `tls: [{hosts: [panel.example.com]}]` | The ingress controller's default certificate |
| `tls: [{hosts: [panel.example.com], secretName: panel-tls}]` | Your certificate |

Setting `tls: false` is a supported value, not an error. Use it when another
layer terminates HTTPS — a router proxy (MikroTik/Caddy), ArvanCloud, or any
load balancer in front of the cluster — and the ingress only forwards HTTP.
Tell the controller not to redirect to HTTPS, otherwise it sends clients to a
port that the external proxy may not serve:

```yaml
# ingress-nginx
ingress:
  enabled: true
  className: nginx
  tls: false
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
    nginx.ingress.kubernetes.io/force-ssl-redirect: "false"
```

```yaml
# Traefik
ingress:
  enabled: true
  className: traefik
  tls: false
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
```

The applications still see HTTPS because the browser reaches the external
proxy, so keep `PANEL_COOKIE_SECURE=true` for the panel and use an `https://`
value for the Instagram media base URL. `tests/test-helm-chart.sh` renders the
chart with `tls: false` and asserts that no TLS block is emitted.

## Enabling the Content Manager components

`examples/helm/content-stack-values.yaml` is a complete overlay. Create the
secrets and ConfigMap it references first:

| Secret | Keys (exact environment variable names) |
| --- | --- |
| `content-bot-secrets` | `CONTENT_BOT_TOKEN`, `CONTENT_TELEGRAM_CHANNEL`, `CONTENT_TELEGRAM_USERS`, `CONTENT_WRITER_API_KEY`, `CONTENT_MEDIA_STUDIO_TOKEN`, `INSTAGRAM_BUSINESS_ID`, `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, optionally `CONTENT_MEDIA_STUDIO_URL`, `CONTENT_VIDEO_CHARACTER` |
| `media-studio-secrets` | `MEDIA_STUDIO_API_TOKEN`, `MEDIA_STUDIO_WRITER_API_KEY`, optionally `MEDIA_STUDIO_WRITER_BASE_URL` |
| `content-panel-secrets` | Optional; the panel token lives on its PVC (`data/panel/token` equivalent) |
| `nine-router-secrets` | `JWT_SECRET`, `INITIAL_PASSWORD`, `API_KEY_SECRET`, `MACHINE_ID_SALT`, optionally `REQUIRE_API_KEY`, `AUTH_COOKIE_SECURE` |
| `omniroute-secrets` | `JWT_SECRET`, `INITIAL_PASSWORD`, `API_KEY_SECRET`, `MANAGEMENT_API_KEY`, `OMNIROUTE_API_KEY`, `STORAGE_ENCRYPTION_KEY`, `MACHINE_ID_SALT`, `OMNIROUTE_WS_BRIDGE_SECRET` |
| `rustfs-secrets` | `RUSTFS_ACCESS_KEY`, `RUSTFS_SECRET_KEY`, optionally other `RUSTFS_*` settings |

```bash
kubectl -n content-manager create configmap content-policy \
  --from-file=editorial-policy.yaml=content/config/editorial-policy.yaml \
  --from-file=tools.json=content/config/tools.json \
  --from-file=sources.yaml=content/config/sources.yaml

helm install content-manager ./deploy/helm/hermes-linux-stack \
  --namespace content-manager -f examples/helm/content-stack-values.yaml
```

Notes for the Kubernetes deployment:

- The operator panel cannot run `docker compose` actions inside a cluster.
  `components.panel.dockerSocket.enabled=true` mounts the host Docker socket on
  a single-node cluster if you really need that behaviour; otherwise the panel
  serves the read-only and configuration views.
- Instagram requires a public HTTPS URL for every media file. Publish
  `components.mediaFiles` through your ingress and set the bot's public base URL
  to that hostname (the equivalent of `data/content-bot/media-base-url.txt` is
  the `INSTAGRAM_MEDIA_PUBLIC_BASE_URL` secret key here).
- Google Flow video jobs need a signed-in browser session and a CDP endpoint
  reachable from Media Studio. That is easiest on a single host; keep the
  Compose deployment for those drivers if the cluster cannot provide one.
- `components.rustfs` runs the same S3-compatible server as the Compose profile.
  In-cluster clients use `http://<release>-rustfs:9000` with path-style
  addressing (`STORAGE_PROVIDER=s3` for Open WebUI, and the `S3_*` variables
  documented in `docs/S3-STORAGE.md`). The console listens on port 9001 under
  `/rustfs/console`; publish it through the ingress only on a trusted network.

## Values reference

| Value | Default | Purpose |
| --- | --- | --- |
| `replicaCount` | `2` | Smart Router replicas |
| `image`, `imagePullSecrets` | `afsharidevops/hermes-smart-router:0.5.9` | Router image |
| `upstream.baseUrl`, `upstream.healthUrl` | empty | Explicit upstream; empty derives from `upstreamServer` |
| `upstreamServer.enabled`, `upstreamServer.backend` | `false`, `9router` | Deploy an in-cluster gateway |
| `components.contentBot.*` | disabled | Bot image, PVC size, `existingSecret`, `policyConfigMap`, `env` |
| `components.panel.*` | disabled | Panel image, service, PVC, `dockerSocket` |
| `components.mediaStudio.*` | disabled | Media Studio image, service, PVC |
| `components.mediaFiles.*` | disabled | nginx file server; `existingClaim` defaults to the Content Bot PVC |
| `components.rustfs.*` | disabled | RustFS S3 server: image, `existingSecret`, `region`, PVC size, service ports, `env` |
| `ingress.*` | disabled | Class, annotations, TLS, hosts; paths reference component names |
| `secrets.existingSecret` | `hermes-smart-router-secrets` | Router/PostgreSQL/Redis secret |
| `postgres.*`, `redis.*` | enabled | In-cluster HA state stores |
| `podDisruptionBudget`, `networkPolicy`, `hpa` | PDB/NetworkPolicy on, HPA off | Availability and scaling controls |

## Publishing the chart

### Package and verify locally

```bash
helm lint deploy/helm/hermes-linux-stack -f examples/helm/content-stack-values.yaml
helm template t deploy/helm/hermes-linux-stack -f examples/helm/content-stack-values.yaml >/dev/null
helm package deploy/helm/hermes-linux-stack --destination dist
tests/test-helm-chart.sh
```

`scripts/helm-publish.sh` wraps lint, render, package, and push:

```bash
# OCI registry (GitHub Container Registry, free)
scripts/helm-publish.sh --registry oci://ghcr.io/afsharidevops/charts

# ChartMuseum or any repository that accepts a POST for each file
scripts/helm-publish.sh --chartmuseum https://charts.example.com

# Show what would run without contacting a registry
scripts/helm-publish.sh --registry oci://ghcr.io/afsharidevops/charts --dry-run
```

Log in before pushing (the script never stores credentials):

```bash
echo "$GHCR_TOKEN" | helm registry login ghcr.io --username YOUR_USER --password-stdin
```

### Option A: OCI registry on GHCR (free, recommended)

```bash
helm package deploy/helm/hermes-linux-stack --destination dist
helm push dist/hermes-linux-stack-0.6.0.tgz oci://ghcr.io/afsharidevops/charts
```

Consumers install straight from the registry:

```bash
helm install content-manager oci://ghcr.io/afsharidevops/charts/hermes-linux-stack \
  --version 0.6.0 --namespace content-manager
```

`.github/workflows/publish-helm-chart.yml` does this automatically for
`content-manager-v*` and `hermes-linux-stack-v*` tags using `GITHUB_TOKEN`, and
also uploads the packaged chart to the GitHub release.

OCI references are lowercase and a GitHub owner name may not be, so the
workflow and `scripts/helm-publish.sh` both lowercase the owner before they
build the destination: `oci://ghcr.io/Afsharidevops/charts` is rejected by the
registry with `invalid repository`.

### Option B: classic Helm repository on GitHub Pages (free)

1. Create a `gh-pages` branch (or a `charts` directory on `main`).
2. Use `helm/chart-releaser-action` with `charts_dir: deploy/helm` in a
   workflow triggered by tags; it packages the chart, creates a release, and
   updates `index.yaml` on `gh-pages`.
3. Consumers add it like any other repository:

```bash
helm repo add afsharidevops https://afsharidevops.github.io/content-manager
helm repo update
helm install content-manager afsharidevops/hermes-linux-stack
```

A minimal workflow for this option:

```yaml
name: Release chart to GitHub Pages
on:
  push:
    tags: ['content-manager-v*']
permissions:
  contents: write
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with: {fetch-depth: 0}
      - uses: azure/setup-helm@v4
      - uses: helm/chart-releaser-action@v1.7.0
        with:
          charts_dir: deploy/helm
```

### Option C: ChartMuseum (self-hosted, free)

```bash
docker run -d --name chartmuseum -p 8080:8080 \
  -v "$PWD/chartmuseum:/charts" chartmuseum/chartmuseum:latest
scripts/helm-publish.sh --chartmuseum http://127.0.0.1:8080
helm repo add local-museum http://127.0.0.1:8080
```

ChartMuseum also supports OCI, S3, and GCS storage backends when it grows past
a single host.

### Option D: other OCI registries

Any registry that implements the OCI distribution spec works with `helm push`:
Docker Hub (`oci://registry-1.docker.io/<user>`), Quay.io, GitLab Container
Registry, Harbor, AWS ECR, and Azure ACR. Only the login command differs.

### Listing on Artifact Hub (free)

Artifact Hub indexes public Helm repositories and OCI registries:

- Repository-based: register the `index.yaml` URL.
- OCI-based: push an `artifacthub-repo.yml` metadata file to the same OCI
  namespace, then add the registry as an OCI repository.

The `annotations:` block in `Chart.yaml` (category, changes) is what Artifact
Hub displays on the package page.

### Free registry comparison

| Option | Cost | Best for | Notes |
| --- | --- | --- | --- |
| GHCR OCI | Free for public packages | Default choice | Same credentials as GitHub Actions |
| GitHub Pages (`index.yaml`) | Free | Classic `helm repo add` consumers | Needs a branch and a release workflow |
| ChartMuseum | Free (self-hosted) | Air-gapped or internal clusters | You run and back it up |
| Docker Hub OCI | Free for public repositories | Docker-centric teams | Rate limits apply to pulls |
| Quay.io | Free for public repositories | Red Hat ecosystems | — |
| GitLab Package Registry | Free tier | GitLab-based projects | Supports OCI charts |
| Artifact Hub | Free | Discovery of public charts | Index only; it does not host |

## Keeping the chart in sync

The chart is part of the repository, so it moves with the project:

1. Change templates or values and run `tests/test-helm-chart.sh` plus
   `helm lint` with the overlay.
2. Bump `Chart.yaml: version` (semver) for every chart change and keep
   `appVersion` aligned with the repository `VERSION` file.
3. When a Compose service changes an image tag, an environment variable, or a
   port, update the matching component in `values.yaml` and the tables in this
   document.
4. Tag the release; the publish workflow pushes the packaged chart to GHCR and
   attaches it to the GitHub release.

## Troubleshooting

- `helm template` fails with `unknown component` — an ingress path references a
  name outside `router`, `upstream`, `content-bot`, `panel`, `media-studio`,
  `media-files`.
- Pods stay `Pending` — the StorageClass is missing or the PVC size is larger
  than the provisioner supports.
- `media-files` shows an empty listing — the Content Bot PVC must contain a
  `media` directory; the file server only serves that subPath.
- The panel answers but actions are disabled — expected in-cluster; see the
  `components.panel.dockerSocket` note above.
