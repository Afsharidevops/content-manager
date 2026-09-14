# Content Manager v0.4.0

Fourth release of the Content Manager fork on the Hermes Linux Stack v0.5.9
platform. It turns the bot's single publish action into multi-channel
publishing: LinkedIn joins Bale and Eitaa as an automatic channel, a draft can
target several destinations at once, each destination receives its own adapted
text, and every attempt is recorded. It ships Content Bot 0.4.0 and operator
panel 0.4.0; Media Studio stays 0.3.0 because nothing in its build context
changed.

## Highlights

- **LinkedIn publishing** - `content_bot/linkedin.py` posts through the
  LinkedIn REST API (`POST /rest/posts`, with an
  `initializeUpload` + binary `PUT` step for an image), retries `429`/`5xx`
  with backoff, and reports the provider message when it finally fails. One
  channel per account: a personal profile (`urn:li:person:<id>`) and a company
  page (`urn:li:organization:<id>`) can publish side by side from the same
  draft, and each one is a `(auto)` entry under **More platforms...**.
- **Social accounts** - `content_bot/accounts.py` reads
  `social-accounts.yaml` from the policy directory (the deployed copy is
  git-ignored), with `CONTENT_LINKEDIN_*` as the single-account fallback and
  `CONTENT_LINKEDIN_ACCOUNTS` for the whole mapping. Tokens stay out of the
  repository, and an account without a token is skipped with a log line.
- **Per-destination adaptation** - `content_bot/adapt.py` rewrites the draft
  body for each destination before publishing. Tone profiles ship for
  `linkedin_personal` (first person, technical, opinionated, founder voice),
  `linkedin_company` (educational, neutral, brand voice), and `telegram`
  (concise, news); a LinkedIn account follows the tone of its type. Profiles
  are overridable in the `tones:` section of `editorial-policy.yaml`, the
  rewrite prompt itself lives in `prompt-templates/<tone>.md`, and any failure
  falls back to the untouched draft text. Only the body is adapted - title,
  source link, and media captions keep the existing helpers, so a deployment
  with adaptation off publishes byte-for-byte what 0.3.0 published.
- **Multi-target publishing and the publication ledger** - a draft may carry
  `targets:`, otherwise `publishing.targets` in the policy applies, otherwise
  **All targets** publishes to every configured automatic channel. Each
  attempt is one row (`id`, `content_id`, `platform`, `account`, `status`,
  `remote_id`, `published_at`, capped `error`) in
  `data/content-bot/state.json`, one destination failing never stops the
  others, and the adapted text is cached as a content variant so a retry never
  calls the writer twice.
- **Operator console** - **Platforms -> LinkedIn** is a first-class card
  (token, account key, type, author URN or person/organization id, API base
  and version) with **Test connection**, which reserves one image upload slot
  to validate the token and the author without publishing anything. YouTube
  and Aparat keep their package behaviour, and the generic LinkedIn package
  entry stays available for hand-offs the adapter cannot do (a video post, for
  example) until a deployment hides it with `linkedin: null`.
- **Command line** - `./manage.sh content-connect-linkedin` stores and verifies
  the same values, `content-channels` and `content-status` report the LinkedIn
  account state beside Bale and Eitaa, and the Content Bot menu gained entry
  10 for it.
- **Documentation** - `docs/LINKEDIN-SETUP.md` walks through the developer app,
  the products and scopes (`w_member_social`, `w_organization_social`), the
  three-legged OAuth flow with curl, finding the author id, the account file,
  the environment fallback, tones, targets, the publish flow, token rotation,
  a configuration table, and troubleshooting.

## Artifacts

- Content Bot image: `afsharidevops/content-bot:0.4.0` (plus `:latest`),
  published by `.github/workflows/publish-content-bot.yml`.
- Operator panel image: `afsharidevops/content-panel:0.4.0` (plus `:latest`),
  published by `.github/workflows/publish-panel.yml`.
- Media Studio image: `afsharidevops/media-studio:0.3.0` (unchanged; no file in
  its build context changed).
- Helm chart: `hermes-linux-stack` 0.6.2 carries the new component tags and is
  published by `.github/workflows/publish-helm-chart.yml` on the release tag.
- Platform: Hermes Linux Stack v0.5.9 (inherited unchanged upstream).

## Upgrade notes

- Content Bot and the panel are pulled images: set
  `CONTENT_BOT_IMAGE_TAG=0.4.0` and `PANEL_IMAGE_TAG=0.4.0` in `.env`, then
  `docker compose pull content-bot panel && docker compose up -d`. The
  installer writes both defaults, so a fresh install needs nothing.
- LinkedIn stays off until an account is configured; Bale, Eitaa, Instagram,
  and the manual packages are untouched, and `content-bot/0.4.0` starts
  cleanly with a 0.3.0-shaped `.env`.
- `data/content-bot/state.json` only gains new keys (`publications`,
  `content_variants`); no migration is needed and an older image ignores them.
- A deployment that wants adaptation can add the `tones:` and `publishing:`
  sections to its policy working copy; the shipped
  `content/config/editorial-policy.yaml` documents both.
- A server upgrade is two `.env` edits plus a pull; the installer is not
  involved (see the Content Bot release notes for the exact commands).

## Documentation

- `docs/LINKEDIN-SETUP.md` - app, OAuth, author id, account file, tones,
  targets, publish flow, token rotation, troubleshooting.
- `docs/CONTENT-PRODUCTION-GUIDE.md` - the platform table and "Extra platforms"
  now describe LinkedIn and per-destination adaptation.
- `docs/PANEL.md` - the Platforms view and its connection tests.
- `content-bot/V0.4.0-RELEASE-NOTES.md` - the component release notes.

## Validation

- Content Bot suite: 358 tests (was 304), including the new
  `test_linkedin.py`, `test_social_accounts.py`, `test_adapt.py`,
  `test_publications.py`, and `test_multichannel.py`.
- Operator panel suite: 101 tests (was 96), covering the LinkedIn card state,
  the author URN builder, the upload-slot probe, and its error paths.
- `tests/smoke.sh` passes end to end, including `tests/test-manage-ux.sh` with
  the new LinkedIn checks.
- Both router-backend Compose profile combinations render cleanly
  (`docker compose config --quiet`).
- Not performed here: the live in-place upgrade and publish test on the
  reference deployment. Approve it on the server before calling the release
  live:
  `docker compose pull content-bot panel && docker compose up -d content-bot panel`,
  then publish one draft to each destination.
