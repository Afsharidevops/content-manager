# Media Studio — Docker Hub publishing

## Image

`afsharidevops/media-studio` — tags `0.1.0` and `latest` (latest is pushed only
for stable releases).

## Workflow

`.github/workflows/publish-media-studio.yml` builds and pushes the image when
changes land on `main` inside `media-studio/**`, `extensions/**`, or the
workflow itself. Manual runs are available through
`Actions -> Publish Media Studio -> Run workflow`.

The image includes a Playwright Chromium and bundles the `locallab-flow-unlock`
extension files under `/app/extensions/locallab-flow-unlock`.

## Prerequisites

Same repository configuration as the other stack images:

- Environment `dockerhub-release` with `DOCKERHUB_USERNAME` variable and
  `DOCKERHUB_TOKEN` secret (Actions settings).
- The local Compose file pins `MEDIA_STUDIO_IMAGE_REPOSITORY` and
  `MEDIA_STUDIO_IMAGE_TAG` in `.env`; the server pulls the published image.

## Version bumps

1. Edit `media-studio/Dockerfile` LABEL version and
   `.github/workflows/publish-media-studio.yml` `IMAGE_VERSION`.
2. Update `MEDIA_STUDIO_IMAGE_TAG` in `.env.example`.
3. Commit; the workflow publishes the new tag on `main`.
