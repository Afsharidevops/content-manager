# Hermes Linux Stack release process

This document is the current generic release checklist. Historical version-specific command transcripts are archived under `docs/archive/release-commands/`.

## 1. Branch policy

- `main` is the only maintained branch and supports both router backends.
- The router backend is a Compose profile choice (`9router` or `omniroute`);
  the installer selects one and writes the matching Smart Router upstream
  URLs, route-profile aliases, and client keys into `.env`.
- Backend-specific installer, Compose, and management logic lives behind the
  profiles in `main`; there is no separate OmniRoute branch.
- Shared Smart Router, Execution Broker, stack plugin, and shared-test
  changes stay on `main`.

## 2. Before a release

1. Ensure the working tree is clean.
2. Run `bash -n install.sh manage.sh tests/smoke.sh`.
3. Install `smart-router/requirements-dev.txt` into an isolated Python environment and run `./tests/smoke.sh`.
4. Run focused execution/plugin regression tests when those areas changed.
5. Run `sha256sum -c MANIFEST.sha256`.
6. Validate both router-backend profile combinations (`9router` and
   `omniroute`) with `docker compose config --quiet` before publishing shared
   images.
7. Back up a real test deployment and perform an in-place upgrade test.

## 3. Versioning

Do not bump `VERSION`, Smart Router package/image versions, or Execution Broker versions merely because development work has started.

Current release state:

- stack/runtime release: `v0.5.9`
- Smart Router image: `afsharidevops/hermes-smart-router:0.5.9`
- Smart Router mutable current tag: `afsharidevops/hermes-smart-router:latest`
- Execution Broker image: `afsharidevops/hermes-execution-broker:0.1.3`

Fork release state (Content Manager):

- fork/content release: `content-manager-v0.3.0`
- Content Bot image: `afsharidevops/content-bot:0.3.0`
- Content Bot mutable current tag: `afsharidevops/content-bot:latest`
- Media Studio image: `afsharidevops/media-studio:0.3.0`
- Media Studio mutable current tag: `afsharidevops/media-studio:latest`
- Operator panel image: `afsharidevops/content-panel:0.3.0`
- Operator panel mutable current tag: `afsharidevops/content-panel:latest`

The Content Bot, Media Studio, and operator panel images are rebuilt and
published by `.github/workflows/publish-content-bot.yml`,
`.github/workflows/publish-media-studio.yml`, and
`.github/workflows/publish-panel.yml` on `main` pushes that touch their build
contexts; no image rebuild is needed for a plain fork release. A
`content-manager-v*` tag additionally publishes the packaged Helm chart through
`.github/workflows/publish-helm-chart.yml`. Fork
releases reuse the upstream versioning section only for inherited platform
artifacts; the fork itself is versioned by the `content-manager-v*` release
tags.

For v0.5.9, automated acceptance passed and the release owner explicitly waived the remaining manual browser light/dark and mouse/trackpad interaction gate in order to finalize early. Record such waivers explicitly; never mark an unperformed check as passed.

## 4. Docker publishing policy

Only rebuild an image when files in that image's build context or required build inputs changed.

For Smart Router releases, publish the plain version tag and `latest` after multi-architecture validation. Do not publish a redundant `v<version>` alias.

For Execution Broker, choose a new broker version only when broker source/runtime behavior changes; do not infer a broker version from the stack version.

Never place registry credentials in repository files or command transcripts.

## 5. Git release policy

Do not create a Git release tag as part of routine branch preparation. Create release tags only as an explicit release action after validation.

## 6. Security release gate

A release must preserve the execution trust boundary:

- Smart Router does not receive the Execution Admin key, approval signing private key, Docker socket, or SSH credentials.
- Execution Admin does not receive the approval signing private key, Docker socket, or SSH credentials.
- The approval service receives only the dedicated approval-bot token, signing material, and configured execution-user policy needed for approval.
- Visual workflow edges define orchestration; they do not grant execution authority.
