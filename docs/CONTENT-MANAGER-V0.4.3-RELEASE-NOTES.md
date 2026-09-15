# Content Manager v0.4.3

Seventh release of the Content Manager fork on the Hermes Linux Stack v0.5.9
platform. It adds Google NotebookLM as a second video provider next to Media
Studio, completes the Aparat upload transport the previous tag stopped short
of, and carries the platform files that keep both stack installs in step. It ships Content Bot
0.4.3, NotebookLM Worker 0.1.0, operator panel 0.4.1, and Media Studio 0.4.0.

## Highlights

- **NotebookLM Video Overviews** - a new optional worker
  (`notebooklm-worker`, compose profile `notebooklm`) produces narrated
  overview videos with Google NotebookLM, so a subscription replaces a paid
  video API. It drives the web app in a real browser session (cdp against the
  operator's Chrome by default, or a container profile), adds the draft title
  and body plus the original link as sources, renders the Video Overview, and
  serves the mp4 as a job artifact.
- **A fifth media choice** - the content question gains **🎬 NotebookLM
  video** with three profiles (`technical_fa`, `educational_fa`, `news_fa`).
  The ask message is edited at every stage (`⏳ 📚 📤 🎬 ⬇️`), the finished
  video arrives as the usual preview, and a failed job reports the worker
  error with a retry button that reuses the stored profile. The button is
  absent while the worker is unconfigured, so existing deployments behave
  exactly as before.
- **Two providers, one pipeline** - NotebookLM is independent from Media
  Studio: the bot resolves the worker per draft from the stored media driver,
  keeps a separate timeout budget (`CONTENT_NOTEBOOKLM_TIMEOUT`), and stores
  the downloaded file under `data/content-bot/media/` like any other result.
- **Aparat upload repair** - the multipart transport returns its documented
  `(status, body)` contract again, the upload server is read from the nested
  `data.attributes` answer, and the metadata call carries the fields the live
  endpoint demands, so a stored session completes an upload instead of failing
  on the first chunk, the header, or the final call.
- **Operator tooling** - `./manage.sh notebooklm-enable|status|login|guide|disable`,
  a `NotebookLM video` entry in the interactive manager, `logs notebooklm`,
  the Content Bot -> NotebookLM link in `./manage.sh pipeline-status`,
  `data/notebooklm-worker` as a scoped backup section, and the profile kept
  across `./manage.sh configure`.
- **Documentation** - `docs/NOTEBOOKLM-STUDIO.md` covers the session modes,
  the API with curl examples, the profiles and length buckets, the settings
  tables, selector calibration with `NOTEBOOKLM_SELECTORS_FILE`, the failure
  playbook, limits, and operations.

## Artifacts

- Content Bot image: `afsharidevops/content-bot:0.4.3` (plus `:latest`),
  published by `.github/workflows/publish-content-bot.yml`.
- NotebookLM Worker image: `afsharidevops/notebooklm-worker:0.1.0` (plus
  `:latest`), published by
  `.github/workflows/publish-notebooklm-worker.yml`.
- Operator panel image: `afsharidevops/content-panel:0.4.1` (unchanged).
- Media Studio image: `afsharidevops/media-studio:0.4.0` (unchanged).
- Platform: Hermes Linux Stack v0.5.9 (inherited unchanged upstream).

## Upgrade notes

- Content Bot is a pulled image: set `CONTENT_BOT_IMAGE_TAG=0.4.3` in `.env`,
  then `docker compose pull content-bot && docker compose up -d content-bot`.
  The bot starts exactly as before; the NotebookLM button appears only after
  the worker exists.
- The worker starts off. Enable it with `./manage.sh notebooklm-enable`, which
  adds the `notebooklm` profile, stores a shared `NOTEBOOKLM_API_TOKEN`, maps
  `NOTEBOOKLM_RUN_AS` to the invoking user (or 10006 on root installs), creates
  `data/notebooklm-worker` with the matching owner, and starts the container.
- The Google session must exist before the first job: sign in to
  `https://notebooklm.google.com` in the browser the worker drives (cdp mode)
  and confirm with `./manage.sh notebooklm-login 15`. The worker never stores a
  password and never signs in on its own.
- `CONTENT_NOTEBOOKLM_TIMEOUT` must stay above the worker budget
  (`NOTEBOOKLM_TIMEOUT`); both default to 1800 seconds.
- Nothing changes for a deployment that keeps the profile disabled: no new
  container, no new secret, and the media question keeps its four choices.

## Documentation

- `docs/NOTEBOOKLM-STUDIO.md` - the provider guide (sessions, API, profiles,
  calibration, failures, limits, backup).
- `docs/CONTENT-PRODUCTION-GUIDE.md` - the media chapter describes the new
  choice and its enable steps.
- `docs/CONTENT-PRODUCTION-ARCHITECTURE.md` - the worker contract as an
  independent provider.
- `docs/BACKUP-RESTORE.md` - the new `notebooklm` backup section.
- `content-bot/V0.4.3-RELEASE-NOTES.md` - the Content Bot component notes.

## Validation

- Content Bot suite: 414 tests, including nine NotebookLM handler tests
  (button visibility, profile picker, job start with draft sources, one
  progress edit per stage, ready preview, failure, retry, missing worker).
- NotebookLM Worker suite: 51 tests (job store and transitions, prompts and
  profiles, source resolution, runner stages, HTTP surface).
- Operator panel suite: 113 tests; Media Studio suite: 82 tests.
- The worker image was built and run locally: `docker build -f
  notebooklm-worker/Dockerfile .`, Chromium launches inside the container as
  uid 10006, `/healthz`, `/session/info`, bearer enforcement, job creation,
  and the failure path (error text plus screenshot and element dump under
  `data/notebooklm-worker/logs/`) were all verified.
- `tests/smoke.sh`, `tests/test-manage-ux.sh`, `bash -n` on the edited
  scripts, `docker compose config` for both router backends, and
  `sha256sum -c MANIFEST.sha256` all pass.
- Not performed here: a live NotebookLM job against a signed-in Google
  account. Approve it on the server before calling the feature live: enable
  the profile, sign in, and produce one video from a real draft.
