# Content Manager v0.4.1

Fifth release of the Content Manager fork on the Hermes Linux Stack v0.5.9
platform. It adds Aparat as an automatic video channel that publishes only
when the operator picks it on a draft that carries a video, completes the
media question on scheduled proposals, and lets the built-in daily pass be
switched off. It ships Content Bot 0.4.1, operator panel 0.4.1, and Media
Studio 0.4.0 with video generation over an API.

## Highlights

- **Aparat publishing, on demand** - `content_bot/aparat.py` drives the Aparat
  upload API of a stored browser session: reserve a slot, send the clip in
  chunks, submit the title, description, tags, and category, then keep the
  watch URL as the publication's `remote_id`. Aparat issues no self-service
  upload key, so the session of a signed-in `aparat.com` tab is reused
  (`CONTENT_APARAT_TOKEN`, the `jwt` value, or `CONTENT_APARAT_COOKIE`).
- **Video-only and never automatic** - the daily proposals and the scheduled
  routines never publish to Aparat. The chooser marks the entry
  `Aparat (needs a video)` until a clip is attached, **All targets** skips it
  without one, and picking it without a video asks for the file, remembers the
  target on the live draft, and uploads the clip as soon as it arrives. Text
  and photo drafts keep the copy-ready package, and an oversized clip that
  Telegram cannot hand to the bot fails with a message that says so.
- **Scheduled proposals ask the media question** - review-time and scheduled
  drafts now share one flow, each routine selects its own `media` mode
  (`none`, `auto`, or `ask`), and `pipeline.daily_enabled: false` turns the
  built-in daily pass off for deployments whose routines own the cadence. The
  default stays enabled.
- **Operator console** - **Platforms -> Aparat** is a first-class card (session
  token, session cookie, label, category id, default tags, watermark, publish
  state, API base) whose **Test connection** asks Aparat for an upload server
  and uploads nothing. `./manage.sh content-aparat-check` probes the stored
  session the same way, and `content-channels`/`content-status` report the
  Aparat state beside the other channels.
- **Documentation** - `docs/APARAT-SETUP.md` covers the session copy step, the
  panel and `.env` paths, the verification command, a full publish walkthrough
  with the Telegram buttons, the settings table, the category ids, the
  four-step upload description, and troubleshooting.
- **Smart Router user guide** - `docs/SMART-ROUTER-USER-GUIDE.md` documents the
  console, the OpenAI-compatible API, model and alias selection, routing
  profiles, the Orchestrator, and the client integrations for every project
  that carries the router.
- **Video generation over an API (Media Studio 0.4.0)** - the new `api-video`
  driver submits an asynchronous video job over HTTP
  (`POST <base>/videos/generations`, then `GET <base>/videos/<id>`), downloads
  the finished clip, and bakes in the brand chip, so the video button works
  without a signed-in Google session as soon as the gateway holds a
  video-capable provider. It is configured with `MEDIA_STUDIO_VIDEO_MODEL`
  (for example `xai/grok-imagine-video`), `MEDIA_STUDIO_VIDEO_BASE_URL`, and
  `MEDIA_STUDIO_VIDEO_API_KEY`, which fall back to the image endpoint and key.
  The panel's Media Studio test probes the video route too and names a missing
  model, a chat combo, or a provider without credentials.

## Artifacts

- Content Bot image: `afsharidevops/content-bot:0.4.1` (plus `:latest`),
  published by `.github/workflows/publish-content-bot.yml`.
- Operator panel image: `afsharidevops/content-panel:0.4.1` (plus `:latest`),
  published by `.github/workflows/publish-panel.yml`.
- Media Studio image: `afsharidevops/media-studio:0.4.0` (plus `:latest`),
  published by `.github/workflows/publish-media-studio.yml`.
- Helm chart: `hermes-linux-stack` 0.6.4 carries the new component tags and is
  published by `.github/workflows/publish-helm-chart.yml` on the release tag.
- Platform: Hermes Linux Stack v0.5.9 (inherited unchanged upstream).

## Upgrade notes

- Content Bot and the panel are pulled images: set
  `CONTENT_BOT_IMAGE_TAG=0.4.1` and `PANEL_IMAGE_TAG=0.4.1` in `.env`, then
  `docker compose pull content-bot panel && docker compose up -d content-bot panel`.
  The installer writes both defaults, so a fresh install needs nothing.
- Media Studio is a pulled image too: set `MEDIA_STUDIO_IMAGE_TAG=0.4.0`, then
  `docker compose pull media-studio && docker compose up -d media-studio`. The
  new driver is inert until it is listed in `MEDIA_STUDIO_DRIVERS` and given a
  video model, so an existing deployment keeps its current video behavior.
- Video generation still needs a video-capable provider: connect xAI,
  OpenRouter, or Vertex AI in the gateway and store its model in
  `MEDIA_STUDIO_VIDEO_MODEL`. Chat combos (`ai`, `ai-strong`) never generate
  video - the gateway answers `Combos are not supported for video generation`.
- Aparat stays off until a session is stored, and it never publishes on its
  own: a deployment that stores nothing keeps the package behavior of 0.4.0,
  and the daily pipeline is untouched.
- `data/content-bot/state.json` only gains draft keys (`pending_target`,
  `media_wait_kind`); no migration is needed and an older image ignores them.
- The session of a signed-in browser expires like any other session. When
  Aparat starts answering `401`, sign in again and refresh
  `CONTENT_APARAT_TOKEN`; `./manage.sh content-aparat-check` reports the state.

## Documentation

- `docs/APARAT-SETUP.md` - session copy, configuration, verification, publish
  walkthrough, settings and category tables, troubleshooting.
- `docs/CONTENT-PRODUCTION-GUIDE.md` - the platform table describes Aparat as
  an on-demand video channel.
- `docs/PANEL.md` - the Platforms view and its connection tests.
- `content-bot/V0.4.1-RELEASE-NOTES.md` - the component release notes.

## Validation

- Content Bot suite: 410 tests (was 358), including the new `test_aparat.py`
  and `test_aparat_flow.py`.
- Operator panel suite: 106 tests (was 101), covering the Aparat card state,
  the session probe, and the `errors` array in a `200` response.
- `tests/smoke.sh` passes end to end, including `tests/test-manage-ux.sh` with
  the new `content-aparat-check` checks, and `sha256sum -c MANIFEST.sha256`
  reports no mismatches.
- Not performed here: the live in-place upgrade and a real Aparat upload on the
  reference deployment. Approve it on the server before calling the release
  live: pull both images, run `./manage.sh content-aparat-check`, then publish
  one draft with a video to Aparat.
