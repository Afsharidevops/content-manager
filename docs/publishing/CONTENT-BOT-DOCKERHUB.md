# Content Bot — Docker Hub

## Images

```text
afsharidevops/content-bot:0.1.0
afsharidevops/content-bot:latest
```

Supported runtime platforms:

```text
linux/amd64
linux/arm64
```

Do not publish a redundant `v0.1.0` Docker tag.

## How publishing works

Pushing `content-bot/`, `content/content_pipeline/`, or this workflow to the
`main` branch of this repository triggers
`.github/workflows/publish-content-bot.yml`, which:

1. Runs the content-layer and Content Bot test suites.
2. Builds the image for `linux/amd64` and `linux/arm64` with build metadata.
3. Pushes `afsharidevops/content-bot:0.1.0` and `:latest` to Docker Hub.
4. Verifies the published multi-platform manifest.

The workflow can also be started manually from the GitHub Actions tab
(`workflow_dispatch`); manual runs publish the same `0.1.0` tag from `main`.

## Repository settings

The workflow publishes through the `dockerhub-release` environment and needs:

- Repository variable `DOCKERHUB_USERNAME` (for example `afsharidevops`).
- Secret `DOCKERHUB_TOKEN` (a Docker Hub access token with write access).

The same settings already power the Smart Router publish workflow. The
`content-bot` repository appears on Docker Hub automatically on the first
successful push.

## Version bumps

A version bump changes the image tag that installs pull by default:

1. Bump `IMAGE_VERSION` in `.github/workflows/publish-content-bot.yml`.
2. Bump `CONTENT_BOT_IMAGE_TAG` in `docker-compose.yml` and `.env.example`.
3. Bump the version label in `content-bot/Dockerfile`.
4. Update this page and `docs/CONTENT-PRODUCTION-GUIDE.md`.

Push the change to `main`; the workflow publishes the new tag plus `:latest`.

## Updating a server

Servers pull the published image; they never build it:

```bash
./manage.sh configure   # or edit .env directly
# Set CONTENT_BOT_IMAGE_TAG to the tag you want, then:
./manage.sh start
./manage.sh logs content
```
