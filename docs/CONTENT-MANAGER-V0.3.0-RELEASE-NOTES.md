# Content Manager v0.3.0

Third release of the Content Manager fork on the Hermes Linux Stack v0.5.9
platform. It adds the operator console, the shared tool registry and scheduled
routines, Instagram publishing with automatic long-lived token refresh, the
LocalLab brand mark on media, shared object storage with an optional bundled
RustFS, and stack backups that can be taken in full or per section.

## Highlights

- **Operator console** - `panel/` (Compose profile `panel`,
  `./manage.sh panel-enable`) is a loopback-bound web console for stack status,
  the live draft queue with the same decisions as the Telegram keyboard,
  validated editors for `editorial-policy.yaml`, `sources.yaml`,
  `categories.yaml`, and `tools.json`, `.env` editing with masked secrets,
  per-service logs, and a fixed action whitelist. The token lives outside the
  repository, sessions use an HMAC-derived HttpOnly cookie, and every
  state-changing request needs the `X-Panel-Csrf` header. Screens are in the
  README and `docs/PANEL.md`.
- **Object storage in the console** - the panel's **Storage** view reads the
  same shared `S3_*` block every service uses, shows the per-service storage
  matrix and the bundled RustFS state, and runs `s3-status` / `s3-verify`
  (the check uses the in-network endpoint, not the host loopback value).
- **Backups in the console** - the **Backups** view lists archives with the
  creation time, size, stack version, and section list from their `.meta.json`
  sidecar; **Create backup** runs a live full backup and **Partial backup**
  takes `backup --only SECTION[,...]` for a chosen set of sections. Because the
  panel runs as the operator uid, both actions run the real `manage.sh backup`
  in a throwaway container from the panel image and hand the finished
  `hermes-stack-*` files back to the backup directory owner.
- **Section backups and portable restores** - `./manage.sh backup --only
  SECTION[,...]` archives only the named part of the stack (`env`, `secrets`,
  `hermes`, `router`, `content`, `media`, `panel`, `n8n`, `openwebui`, `s3`,
  `caddy`, `execution`, `state`, `compose`). Restores rewrite absolute
  `*_HOST_PATH`/`*_STACK_PATH` values when the archive came from another
  checkout, merge partial archives instead of replacing the stack, keep a
  pre-restore safety backup, and roll back when validation or readiness fails.
- **Shared object storage** - one `S3_STORAGE_BACKEND=rustfs|external|off`
  block with endpoint, bucket, region, and credential variables; the optional
  `rustfs` profile runs RustFS in-stack with the S3 API on 9000 and its console
  on 9001, and any other S3 provider can be pointed at the same block. Open
  WebUI consumes it natively; the Content Bot, Media Studio, n8n free tier, and
  Hermes Agent keep local storage, and `docs/S3-STORAGE.md` documents the
  matrix. Day-two commands: `s3-status`, `s3-enable`, `s3-disable`,
  `s3-verify [--create-bucket]`, `s3-keys`, `s3-guide`, `logs rustfs`.
- **Tool registry and routines** - `content/config/tools.json` registers the
  MCP/OpenAPI tools the bot, router, and n8n share, and the editorial policy
  gained scheduled per-platform routines (research, draft, media, queue) with a
  per-platform cadence.
- **Instagram** - the Graph publisher supports photos, carousels, and video
  behind the same approve buttons, the long-lived token refreshes itself, the
  optional `ig-media` profile serves `data/content-bot/media` through nginx on
  loopback (with a quick or named Cloudflare tunnel, or a Caddy domain), and
  the bot resolves the public media host from a pinned file, `.env`, or the
  live tunnel log. `docs/INSTAGRAM-SETUP.md` covers the app, token, and routes.
- **LocalLab brand chip** - `MEDIA_STUDIO_BRAND_TEXT` stamps the configurable
  corner brand on AI-generated and operator-uploaded images.
- **Operator video** - a clip the operator sends is asked about: edit it or
  publish it as-is.
- **Helm chart** - an optional in-cluster RustFS component, Content Bot, panel,
  Media Studio, and media file server components, a generic ingress with
  `ingress.tls: false` for an external reverse proxy, and the publishing guide
  in `docs/HELM.md`. `content-manager-v*` tags publish the packaged chart.

## Artifacts

- Content Bot image: `afsharidevops/content-bot:0.3.0` (plus `:latest`),
  published by `.github/workflows/publish-content-bot.yml`.
- Media Studio image: `afsharidevops/media-studio:0.3.0` (plus `:latest`),
  published by `.github/workflows/publish-media-studio.yml`.
- Operator panel image: `afsharidevops/content-panel:0.3.0` (plus `:latest`),
  published by `.github/workflows/publish-panel.yml`.
- Helm chart: published by `.github/workflows/publish-helm-chart.yml` on the
  release tag.
- Platform: Hermes Linux Stack v0.5.9 (inherited unchanged upstream).

## Upgrade notes

- The panel is a new optional profile: `./manage.sh panel-enable` creates
  `data/panel/` and the backup directory next to the checkout and starts the
  console on `127.0.0.1:8899`. `PANEL_IMAGE_TAG` defaults to `0.3.0`.
- `PANEL_STACK_PATH` is refreshed by `panel-enable`; moving the checkout only
  requires re-running it.
- Backups are unchanged for host runs. The panel mounts
  `<checkout>-backups`; set `CONTENT_MANAGER_BACKUP_DIR` in `.env` to move it.
- Object storage stays off until `./manage.sh s3-enable` (or the installer
  asks). `OPENWEBUI_STORAGE_PROVIDER=s3` without that step now raises a warning
  in the console and in `s3-status` instead of failing silently at upload time.
- Restores from an archive created on another host rewrite the absolute paths
  recorded in `.env`; pass `--no-relocate` to keep the recorded values.

## Documentation

- `docs/PANEL.md` - console install, views, actions, security model, remote
  access through a reverse proxy.
- `docs/S3-STORAGE.md` - the shared S3 block, per-service consumers, the
  ArvanCloud-to-Caddy route, and troubleshooting.
- `docs/BACKUP-RESTORE.md` - sections, full versus partial restores, and the
  cross-server procedure.
- `docs/INSTAGRAM-SETUP.md` - Meta app, token, media host, and publishing.
- `docs/HELM.md` - chart values, secrets, ingress, and chart publishing.
- `docs/CONTENT-PRODUCTION-GUIDE.md`, `docs/MEDIA-STUDIO.md`,
  `docs/FLOW-UNLOCK-STANDALONE.md`, `docs/TOOL-REGISTRY.md` - the pipeline,
  media worker, laptop Flow unlock, and the shared tool registry.

## Validation

- GitHub Actions runs `validate` on every `main` push: shell and script syntax,
  stack integration tests, the Helm chart lint/render, the panel suite, the
  content layer and Content Bot suites, the Smart Router suite, image builds,
  Compose execution-boundary checks, SSH-profile broker tests, and the
  interactive installer smoke test.
- Panel suite: 68 tests. Object storage: 17 shell tests. Stack operations
  (backups, restores, relocation): 15 shell tests. Helm chart: 19 tests.
- Both router-backend Compose profile combinations render cleanly
  (`docker compose config --quiet`).
- Live checks on the reference deployment: a full panel backup, a section
  backup, and `s3-verify` against the bundled RustFS were all exercised through
  the console.
- Waiver: the repository-wide `sha256sum -c MANIFEST.sha256` recorded at the
  upstream import is regenerated with this release so it covers the fork's
  files; the check is not enforced by CI.
