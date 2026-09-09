# Content Manager v0.2.0

Second release of the Content Manager fork on the Hermes Linux Stack v0.5.9
platform. It adds the Media Studio media worker, topic-driven drafts with
media attachment, unified media publishing, a configurable corner brand chip,
and the selectable 9router/OmniRoute backend on `main`.

## Highlights

- **Media Studio** - a single-worker job service (`media-studio/`, Compose
  profile `media`) with a JSON API on `127.0.0.1:8850`. The `api-image` driver
  generates images through the selected writer gateway, and the Google
  `flow-video`/`gemini-image` drivers drive a signed-in Chrome over CDP on the
  operator desktop or in a persistent container profile.
- **Locallab Flow Unlock** - `extensions/locallab-flow-unlock/` keeps
  `flow.google.com` usable on a laptop with no stack component, and the stack
  profiles honor `MEDIA_STUDIO_BLOCK_GEO_REDIRECT` and
  `MEDIA_STUDIO_FREEZE_ON_READY` so a container profile stays usable where
  Flow enforces the unsupported-country redirect. Flow credit prompts are
  auto-approved and the download quality selector is calibrated
  automatically.
- **Topic-driven drafts** - send a topic instead of a link and the Content
  Bot searches and drafts from the results; link drafts are unchanged.
- **Media approval flow** - proposals ask whether media is needed; AI images,
  operator-supplied images, and video prompt flow are attached to the draft
  for preview and Approve/Reject before publishing.
- **Unified media publish** - the draft and its media publish together with
  caption continuation when the post is split across Telegram messages. The
  title stays bold and RTL-safe, the source link appears once at the end of
  the final part, and mojibake writer replies are repaired.
- **Corner brand chip** - `MEDIA_STUDIO_BRAND_TEXT` stamps a configurable
  corner brand on AI-generated and operator-uploaded images before approval.
- **Selectable router backend** - `main` supports both `9router` and
  `omniroute` Compose profiles with install-time selection and backend
  switching; Hermes, Open WebUI, n8n, and the Smart Router follow the active
  backend. The legacy `hermes-omniroute-linux-stack` branch is obsolete.
- **Standalone pipeline installs** - installer options 5/6/7 provision the
  Content Bot, Media Studio, or the combined pipeline without a router, and
  `./manage.sh pipeline-status` reports the combined pipeline health.

## Artifacts

- Content Bot image: `afsharidevops/content-bot:0.2.0` (plus `:latest`),
  published to Docker Hub by the `publish-content-bot` workflow on `main`
  pushes.
- Media Studio image: `afsharidevops/media-studio:0.2.0` (plus `:latest`),
  published to Docker Hub by the `publish-media-studio` workflow on `main`
  pushes.
- Platform: Hermes Linux Stack v0.5.9 (inherited unchanged upstream).

## Documentation

- `docs/CONTENT-PRODUCTION-GUIDE.md` - production, approval, media, and
  operator workflow guide.
- `docs/MEDIA-STUDIO.md` - Media Studio install, sessions, API, and driver
  calibration.
- `docs/FLOW-UNLOCK-STANDALONE.md` - laptop-only Flow unlock guide.
- `docs/CONTENT-PRODUCTION-ARCHITECTURE.md` - pipeline architecture and
  phased rollout.
- `docs/publishing/CONTENT-BOT-DOCKERHUB.md` and
  `docs/publishing/MEDIA-STUDIO-DOCKERHUB.md` - image publishing notes.

## Validation

- Content layer tests pass (`33`), Content Bot tests pass (`88`), and Media
  Studio tests pass (`37`).
- `install.sh` and `manage.sh` pass shell syntax checks; both router-backend
  Compose profile combinations render cleanly.
- GitHub Actions runs the `validate` workflow on every `main` push, and the
  component publish workflows rebuild and publish the images on the tagged
  commit.
