# Changelog

This file is the canonical Hermes Linux Stack release history from v0.5.2 onward.
Older component-specific history remains in the component release-note files and Git history.

The current runtime release is **v0.5.9**.

## Content Manager — fork of Hermes Linux Stack v0.5.9 (2026-09-07)

This repository is **Content Manager**: a fork of the Hermes Linux Stack v0.5.9
platform (upstream unchanged) extended with a deterministic daily
content-production layer. The upstream changelog below documents the inherited
platform; this section tracks the fork additions.

### Fork release — Content Manager v0.3.0 (2026-09-10)

- Added `panel/`: an optional operator console (Compose profile `panel`) for
  stack status, validated YAML/JSON editing with backups, `.env` edits, service
  logs, and a fixed whitelist of compose actions. It stays bound to loopback,
  authenticates with a token stored outside the repository, and never returns a
  secret to the browser.
- The panel lists the live drafts and applies the same decisions as the
  Telegram keyboard (text only, AI image, publish, discard). The bot keeps the
  authoritative `state.json`, so the panel appends validated requests to
  `data/content-bot/panel-actions.requests.jsonl` and the bot drains them from
  its main loop, reporting each outcome in the console and in Telegram.
- Added the shared tool registry (`content/config/tools.json`) and scheduled
  per-platform routines (research, draft, media, queue) driven by the policy
  cadence, plus the "edit or publish as-is" choice for operator-recorded
  clips.
- The Instagram long-lived token now refreshes itself: the bot extends it
  weekly, stores the result in `data/content-bot/instagram-token.json`, keeps
  publishing with the previous token when the Graph API refuses, and notifies
  the operator once instead of failing quietly. `/instagram` and the console
  card show the remaining days and can force a refresh.
- The brand mark is now the LocalLab gradient pill (cyan to blue to violet to
  magenta over navy) with a halo, highlight sweep, hairline border, and hexagon
  mark, rendered with Pillow only. `MEDIA_STUDIO_BRAND_STYLE=chip` restores the
  previous flat chip, and the `video-edit` driver bakes the same mark into
  prepared clips through ffmpeg.
- Added the optional `ig-media` compose profile: nginx serves
  `data/content-bot/media` read-only on loopback while a cloudflared sidecar
  publishes it, so the stack provides its own public media host instead of
  depending on a hand-started process. `./manage.sh instagram-media-enable`,
  `-status`, and `-disable` manage it, and the bot resolves the public base URL
  from a pinned `media-base-url.txt`, a non-quick-tunnel
  `INSTAGRAM_MEDIA_PUBLIC_BASE_URL`, or the live tunnel log, rebuilding the
  Graph client when the hostname changes. Setting `IG_MEDIA_TUNNEL_TOKEN`
  switches the same profile set to a named Cloudflare tunnel, which keeps one
  permanent hostname on a domain you own instead of a random quick tunnel, and
  the quick tunnel now lives in its own `ig-media-quick` profile so nginx can
  stay up behind a reverse proxy (`./manage.sh instagram-media-tunnel-off`).
  Installing the content stack on a server can publish the media directory on
  a Caddy domain straight from the wizard, which needs no tunnel at all.
- Documented quick-tunnel versus stable media URLs, since a tunnel hostname
  that changes on restart breaks Instagram publishing with a confusing
  media-processing error.

### Fork release — Content Manager v0.2.0 (2026-09-09)

- Added `media-studio/`: a single-worker media job service with API image
  generation through the selected writer gateway and optional Google
  Flow/Gemini drivers driven over CDP. The `locallab-flow-unlock` extension
  keeps `flow.google.com` usable on a laptop without stack components, and
  `MEDIA_STUDIO_BLOCK_GEO_REDIRECT` / `MEDIA_STUDIO_FREEZE_ON_READY` keep a
  container profile usable where Flow enforces the unsupported-country
  redirect. Flow credit prompts are auto-approved and the download quality
  selector is calibrated automatically.
- The Content Bot accepts topics as well as links: a topic is searched and
  drafted by the writer, and proposals can attach AI-generated or
  operator-supplied images plus a video prompt flow through Media Studio.
- Unified media publishing posts the draft and its media together with
  caption continuation when the post exceeds one Telegram message, keeps the
  title bold and RTL-safe, appends the source link once at the end, and
  repairs mojibake writer replies.
- A configurable corner brand chip (`MEDIA_STUDIO_BRAND_TEXT`) is stamped on
  AI-generated and operator-uploaded images before approval.
- `main` now supports both router backends (`9router` and `omniroute`) as
  Compose profiles with install-time selection and backend switching; the
  `hermes-omniroute-linux-stack` branch is obsolete. Installer options 5/6/7
  keep the standalone and combined content pipeline router-free.
- Component images advance to `0.2.0`: `afsharidevops/content-bot:0.2.0` and
  `afsharidevops/media-studio:0.2.0` (plus `:latest`).

### Features — scheduled per-platform routines (2026-09-10)

- `editorial-policy.yaml` gains an optional `routines:` list. Each enabled
  routine runs one research -> draft -> media -> queue pass per cadence period
  (daily at `time`, or weekly on or after `weekday`), drafts up to `count` of
  the ranked candidates, and drops them into the Telegram approval queue as
  "Scheduled proposal" drafts.
- `media: auto` submits one AI image per queued draft through Media Studio and
  attaches it when the job finishes; `media: none` queues text only. Drafts
  are still queued when Media Studio is not configured, and a failed image job
  is reported to the operator (scheduled drafts have no media question).
- Run markers live in `data/content-bot/state.json` under `routine_last_run`,
  so a restart never repeats a period; `/status` and `/help` report the active
  routines. Routines share the discovery feeds, dedupe/filter/scoring rules,
  and the category-mix rule with the daily proposal; the shipped default is
  `routines: []` and the daily flow is unchanged.

### Features — operator panel for the stack (2026-09-10)

- Added `panel/`: an optional operator console served by the new `content-panel`
  container (Compose profile `panel`). It reads stack status, health, image
  tags, published host ports, `data/` disk usage, pipeline counters, drafts, and
  Media Studio jobs, and warns when an internal endpoint such as n8n/MCP is
  published beyond loopback.
- The console edits the four managed configuration files through validated
  editors that keep a timestamped backup before every atomic write and can
  restore a previous version. `.env` keys are editable in place with secret
  values masked, and state-changing requests require a CSRF header on top of the
  HMAC-derived HttpOnly session cookie.
- Actions are a fixed whitelist (apply changes, restart Content Bot / Media
  Studio / Smart Router, pull published images, status summaries, doctor) and
  never accept a free-form command; `PANEL_ACTIONS_ENABLED=false` serves
  read-only views.
- The panel is a separate container on purpose: the Content Bot image keeps its
  read-only root filesystem and unprivileged uid, while the panel mounts the
  Docker socket and writes repository files as the stack owner. It is bound to
  loopback by default, is enabled or disabled with `./manage.sh panel-enable` /
  `panel-disable`, and stores its operator token in `data/panel/token`.
- `.env.example`, `docker-compose.yml`, `install.sh`, and `manage.sh` carry the
  new `PANEL_*` settings, the panel menu, and the `panel-*` commands;
  `.github/workflows/publish-panel.yml` publishes the image and `docs/PANEL.md`
  documents the security model, remote-access guidance, and the configuration
  reference.

### Features — shared tool registry for the bot, router, and n8n (2026-09-10)

- `content/config/tools.json` is the single source of truth for the remote
  tools the content stack calls (MCP servers, OpenAPI services, plain HTTP
  endpoints). Each entry lists the kind, address, capabilities, allowed
  consumers, and the *name* of the environment variable that holds the
  credential; secrets are never stored in the file, and `${ENV_NAME}`
  placeholders are substituted by each runtime, so one registry works for a
  combined server and for a split deployment.
- The Content Bot loads the registry from its policy directory and exposes
  `/tools` (entries for the bot with credential state and warnings) plus a
  one-line summary in `/status`. A missing or invalid file never stops the
  bot.
- The Smart Router serves `GET /v1/tools` behind the same client auth as
  `/v1/models`, read from `SMART_ROUTER_TOOLS_REGISTRY`; a broken file answers
  with `tools_registry_invalid` and HTTP 500.
- The n8n bootstrap reads the same file (mounted at `/tools` by
  `./manage.sh bootstrap-n8n` or the `N8N_TOOLS_REGISTRY` override) and adds a
  tool summary with the count of missing credential variables to its
  reconcile result.
- Media Studio publishes `GET /openapi.json` (no token, no secrets) so the
  registry entry can point at a live spec, including the `video-edit` driver
  and `POST /uploads`.
- `docs/TOOL-REGISTRY.md` documents the schema, the per-consumer wiring, and
  how to add a tool; `.env.example`, `docker-compose.yml`, `install.sh`, and
  the CI validation workflow ship the new file and settings.

### Features — edit or publish an operator-sent video (2026-09-10)

- When the operator sends a recorded clip to the bot, the media question now
  asks **Edit it** or **Publish as-is** instead of attaching the file
  straight away. **Publish as-is** keeps the recording untouched; **Edit it**
  uploads the clip to Media Studio and runs the new offline `video-edit`
  driver, which re-encodes it to an MP4 with H.264/AAC and faststart, caps
  the long side at 1920 px with the aspect ratio preserved, fixes rotation,
  strips container metadata, and can trim with
  `MEDIA_STUDIO_VIDEO_EDIT_MAX_SECONDS`.
- Media Studio gained `POST /uploads` (raw video bytes in, upload id out),
  the `video-edit` driver and registry entry, and ffmpeg resolution that
  prefers `MEDIA_STUDIO_FFMPEG`, then a system binary, then the
  `imageio-ffmpeg` wheel, so the image needs no extra system package.
  Stored uploads are pruned after `MEDIA_STUDIO_UPLOAD_TTL_SECONDS`.
- A failed edit job never loses the recording: the original file is restored
  as the draft media, the bot explains the failure, and **Approve** publishes
  the clip unchanged. Clips above the 20 MB Bot API limit stay on Telegram
  and skip the question because they cannot be copied into Media Studio.
- `CONTENT_MEDIA_VIDEO_EDIT_DRIVER`, the `MEDIA_STUDIO_VIDEO_EDIT_*` set,
  `MEDIA_STUDIO_UPLOAD_TTL_SECONDS`, and `MEDIA_STUDIO_FFMPEG` are documented
  in `.env.example`, `docker-compose.yml`, and `docs/MEDIA-STUDIO.md`.

### Changes — manual platform packages are opt-in (2026-09-10)

- The copy-ready upload packages (YouTube, Aparat) and their **More
  platforms...** button are hidden unless `CONTENT_PLATFORMS_ENABLED=true`, so
  the live bot stays on Telegram and Instagram only. The packages, the
  `platforms:` policy section, and their callbacks are unchanged behind the
  switch; a callback that arrives while it is off answers with the setting
  name instead of sending a package.
- `.env.example`, `docker-compose.yml`, and
  `docs/CONTENT-PRODUCTION-GUIDE.md` document the switch; the shipped default
  is off.

### Features — Firefox build and ZIP packages for Flow Unlock (2026-09-10)

- `extensions/locallab-flow-unlock-firefox/` is the Flow unlock for Firefox
  140+ as an independent package: the same page-world response patch and
  freeze guard as the Chrome extension, with a Firefox MV3 manifest (event
  page instead of a service worker) and a blocking `webRequest` listener that
  sends a navigation on the unsupported-country route back to the app root of
  the same account and cancels other resources on that route, replacing the
  `declarativeNetRequest` rule set. The package lints clean for AMO.
- `extensions/package-flow-unlock.sh` builds
  `locallab-flow-unlock-chrome-<version>.zip` and
  `locallab-flow-unlock-firefox-<version>.zip` with a per-browser README, so a
  machine can install the unlock without the repository layout. Builds use
  fixed timestamps and are byte-identical for unchanged sources.
- `tests/test-flow-unlock-rules.sh` now validates both manifests and keeps the
  shared scripts byte-identical across the two packages;
  `tests/test-flow-unlock-package.sh` builds the ZIPs on every CI run and
  checks their layout.

### Fixes — oversized video uploads stay on Telegram (2026-09-10)

- A video above the 20 MB Bot API download limit no longer fails with
  `Could not download the file: Telegram getFile HTTP 400`. The bot keeps the
  Telegram `file_id` instead of the bytes, re-sends the clip as the media
  preview, and publishes it to the channel by id, so a reel created in Flow or
  on a phone can be attached without re-encoding it.
- Files kept on Telegram live there only: Instagram approvals and the platform
  upload packages need the file itself and now say so, asking for a copy under
  20 MB instead of failing silently or shipping a package without media.
- Bot API failures include Telegram's own description (for example the
  `getFile` limit message) instead of only the HTTP status.

### Improvements — friendlier copy and RTL-safe captions (2026-09-10)

- The writer prompt now asks for a warm, conversational, second-person voice
  with short everyday sentences and explicitly rules out formal, official, and
  news-desk phrasing, so drafts read like a note from a friend.
- Instagram captions start with an invisible right-to-left mark and mark every
  Latin-led paragraph. Instagram renders the account name inline before the
  caption, and without the mark a Latin account name flips the first paragraph
  to LTR and scrambles the Persian title. Telegram and Instagram now share the
  same bidi helpers in `content_bot/rtl.py`.
- Writer titles keep the English product name inside the sentence; the prompt
  already required a Persian opening word and now enforces it together with the
  friendlier voice.

### Improvements — segmented video prompt package and character choice (2026-09-10)

- "My video (get a prompt)" now builds a reel package instead of one prompt:
  a scene setting and the exact Persian narration line per ten-second
  segment, ready for the Google Flow Extend flow. Short packages arrive in
  one message and longer ones as one copy-ready message per segment.
- The flow asks whether the reel should use the saved Flow character
  (`CONTENT_VIDEO_CHARACTER`, for example `@mohammad`) or run as a pure-AI
  promo with no character. Character reels repeat the character block on
  every segment, AI promo reels describe the shot only and list the Persian
  line as an optional voiceover.
- `CONTENT_VIDEO_CHARACTER_PROMPT` overrides the built-in character
  description, `CONTENT_VIDEO_ASPECT` sets the reel frame (default
  `9:16 vertical`), and `CONTENT_VIDEO_SEGMENT_SECONDS` sets the clip length
  Flow generates per segment (default 10). The writer drafts the narration
  lines and falls back to the post text when the writer backend is down.
- Video script requests use a larger token budget (at least 2600) with low
  reasoning effort; reasoning-heavy routers otherwise spend the whole budget
  on hidden reasoning and return empty content, which silently pushed every
  reel package back to the post-text fallback.

### Fixes — Flow unlock for multi-account URLs and long-poll timeouts (2026-09-10)

- The `locallab-flow-unlock` rules only matched the bare
  `/unsupported-country` path. With several Google accounts signed in the
  block page is reached through `/u/<n>/unsupported-country`, which the rules
  missed; the rule set now covers the account-scoped paths, `flow.google-*`
  hosts, and the `labs.google` tool path, and the companion filter file was
  extended the same way. The content script also watches the address bar and
  restarts the app root (at most twice per tab) when the web app swaps the
  route client-side, where no request exists for the rule set to block.
- The recovery navigation is no longer cancelled by the freeze that targets
  the block page: scheduled `window.stop()` calls are cleared before bouncing,
  which previously left the tab sitting on the block view.
- New `background.js` service worker: when a blocked navigation still strands
  a tab on Chrome's `ERR_BLOCKED_BY_CLIENT` page, the tab is returned to the
  app root up to twice a minute. The block view itself is server-decided for a
  signed-in session, so the extension can only avoid the dead end, not bypass
  the verdict.
- Recovery navigations keep the `/u/<n>/` account prefix. Restarting the app
  on the bare root silently fell back to the first signed-in Google account,
  which looked like the browser switching accounts on its own.
- The create-button matcher used by the freeze step is back to its original
  form: the wider selectors added earlier could fire the freeze during the app
  bootstrap and abort data the dashboard needs.
- The Content Bot polling loop survives transient network failures: Telegram
  long polls that time out through a slow route are logged as a warning and
  retried after a short backoff instead of dumping a full traceback into the
  container log. The long-poll HTTP read timeout is now derived from the poll
  timeout instead of a fixed 35 seconds.

### Fixes — Flow app-config response patch (2026-09-10)

- The `locallab-flow-unlock` extension no longer depends on winning a race
  against the region check. Flow decides the unsupported-country verdict inside
  the page, from two batchexecute answers, and both are now rewritten in
  flight: `VideoFxService.GetFlowAppConfig` (`cPZSdc`) carries the country flag
  in field 31 and the age flag in field 32, `AiSandbox.CheckToolAvailability`
  (`KV2T2d`) carries the per-tool status in field 1. Only those fields change
  and the rest of the answer passes through untouched.
- The patch runs in the page world (`unlock.js`, `world: MAIN`, loaded at
  `document_start`) and handles both encodings the backend uses, the JSON
  protocol-buffer form and the base64 one, so the dashboard renders for an
  account the Flow backend marks as an unsupported country.
- `freeze.js` stands down as soon as the config answer is known to be patched
  (the page carries a `data-locallab-flow-config="patched"` marker), which
  keeps the freeze a fallback for tabs where the patch did not apply.
- The declarative rules are anchored to the `/unsupported-country` path
  (`regexFilter`) instead of matching the string anywhere in the URL. The old
  wildcard filters also blocked the app's own `batchexecute` calls, because
  their `source-path` parameter contains the blocked route.
- Rebuilt answers keep the batchexecute framing valid: each frame's length
  token counts the JSON plus the newlines around it in UTF-16 units (not
  bytes) and moves with the frame, and the closing `e` frame's value is
  re-computed to the byte size of the rewritten answer, so the page accepts a
  patched response instead of staying on the block route.
- `tests/test-flow-unlock-patch.mjs` covers both payload encodings, the
  omitted-field case, the length-token and closing-size framing, and the
  pass-through of unrelated RPCs; the rules test now also guards the second
  content script and the anchored filters.

### Fixes — Instagram API version default (2026-09-10)

- The default Graph API version moves from `v23.0` to `v26.0`, matching the
  version the Meta dashboard now shows. Live checks against
  `graph.instagram.com` confirm `v26.0` is the newest accepted version
  (`v27.0` is rejected as unknown) and that account lookup, media listing,
  and publishing all keep working on it.

### Fixes — Instagram approval flow and Media Studio reachability (2026-09-10)

- Media previews now carry the Instagram approval buttons: photo and video
  previews show them inline, and albums (user photo collections) send a
  follow-up message with the same buttons, because Telegram albums cannot hold
  an inline keyboard.
- The photo-collection preview offers the **Done** button that its own ask
  message describes; every upload replaces the previous preview and both
  preview messages are removed when a draft is replaced, rejected, or
  published.
- `Approve to Instagram` publishes to Instagram only. Previously it also posted
  to the Telegram channel, which made it identical to the combined button, and
  `content_bot/bot.py` never imported the Instagram module, so every Instagram
  approval failed with a `NameError`.
- The callback query is acknowledged before the slow Instagram publish instead
  of after it, so Graph API processing can no longer fail the update with a
  stale callback id.
- Media Studio listens on every container interface so `content-bot` can reach
  `http://media-studio:8850`; the published host port still follows
  `MEDIA_STUDIO_BIND_IP`.

### Improvements — platform upload packages (2026-09-10)

- Draft and media previews gained a **More platforms...** button that opens a
  chooser for platforms that need a human upload step. Picking a platform
  sends a copy-ready package: the title cut to the platform limit, the
  description with the source link and hashtags, the direct upload URL, and
  the stored media file re-sent for a quick download.
- Packages are driven by the `platforms:` section of `editorial-policy.yaml`:
  YouTube and Aparat ship as defaults, any key can be overridden, added, or
  set to `null` to hide its button. Telegram and Instagram keep publishing
  through their APIs; a package never consumes the draft or counts toward the
  daily limit.

### Fork additions — content layer v0.1.0

- Added the `content/` Python layer for deterministic discovery normalization,
  deduplication, filtering, and candidate scoring; see `content/README.md`.
- Added owner-editable policy under `content/config/` (`editorial-policy.yaml`,
  `categories.yaml`). Persian copy is produced at runtime, never stored.
- `install.sh` now provisions the Content Manager workspace: it seeds a
  gitignored working copy of the policy under `data/content-manager/config`,
  defaults the n8n timezone to `Asia/Tehran`, and reports the content layer at
  the end of install.
- Quickstart and installer identity now point at this repository.

### Fork additions — Media Studio worker v0.1.0 (2026-09-09)

- Added `media-studio/`: a single-worker job service with a JSON API on
  `127.0.0.1:8850`, JSON-backed job state, artifact storage, and pluggable
  drivers.
- Added the `api-image` driver (OpenAI-compatible `/images/generations`) so
  media generation works with the existing router or hosted writer API,
  without any Google subscription.
- Added Google drivers (`flow-video`, `gemini-image`) that drive a signed-in
  Chrome over CDP (operator desktop) or a persistent container profile; both
  support the Flow unsupported-country region workaround
  (`MEDIA_STUDIO_BLOCK_GEO_REDIRECT`, `MEDIA_STUDIO_FREEZE_ON_READY`).
- Added `extensions/locallab-flow-unlock/`: a standalone Chrome extension and uBlock
  filter that keep `flow.google.com` usable on a laptop without any stack
  component (`docs/FLOW-UNLOCK-STANDALONE.md`).
- `install.sh` gained a Media Studio wizard and `--media-reconfigure`;
  `manage.sh` gained the `media` menu group and `media-status`,
  `media-guide`, and `media-configure` commands; the compose profile is
  `media` with image `afsharidevops/media-studio:0.1.0`.
- Documentation: `docs/MEDIA-STUDIO.md` (install, sessions, API, selector
  calibration) and `docs/publishing/MEDIA-STUDIO-DOCKERHUB.md`.

### Fork additions — selectable router backend: 9router or OmniRoute (2026-09-09)

- `install.sh` now asks on fresh installs whether to use the `9router` or the
  `omniroute` backend profile and can switch an existing install between them
  without losing data; the `hermes-omniroute-linux-stack` branch is obsolete.
- Added the OmniRoute Compose service (dashboard on 20128, OpenAI-compatible
  API on 20129, profile `omniroute`) and a backend-agnostic
  `router-upstream-probe` one-shot that waits for the selected backend before
  the Smart Router starts.
- OmniRoute installs default the Smart Router route profiles, observe model,
  n8n hosted-chat model, Hermes provider, and Open WebUI connection to the
  `auto/best-*` aliases (`http://omniroute:20129/v1`); 9router installs keep
  the `combo-*` defaults. Switching backends resets Smart Router upstream
  URLs, route profiles, and stale upstream keys.
- `manage.sh` n8n provisioning now creates, stores, and validates a dedicated
  OmniRoute API key for the hosted-chat router credential; `set-router-mode`,
  `set-backend-api-key`, `verify-n8n`, `reconcile-n8n`, and the n8n MCP
  commands all work with either backend.
- The standalone pipeline options (5/6/7) and the combined Content Bot +
  Media Studio wiring described below are unchanged and remain router-free
  when no backend is installed.

### Fork additions — combined standalone content pipeline install (2026-09-09)

- `install.sh` fresh-install option 7 installs the Content Bot and Media
  Studio together as one content pipeline with no router, Hermes, n8n, or
  Open WebUI. Options 5 and 6 ask whether the other pipeline service should
  be added too, then walk through both configuration wizards in one run.
- A combined install writes the Content Bot -> Media Studio link into `.env`:
  `CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850`, the Media Studio API
  token mirrored into `CONTENT_MEDIA_STUDIO_TOKEN`, and image/video driver
  names that match the enabled Media Studio drivers. Reconfiguring either
  service keeps the link in sync, so a rotated Media Studio API token is
  propagated to the Content Bot.
- `manage.sh` gained `pipeline-status`: a combined status view of the Content
  Bot, Media Studio, and their API link (URL, token sync, driver coverage).
- Documentation: `docs/CONTENT-PRODUCTION-GUIDE.md`, `docs/MEDIA-STUDIO.md`,
  and the README describe the one-server content pipeline install.

### Fork additions — Content Bot Telegram MVP (2026-09-07)


- Added `content/content_pipeline/score.py`: deterministic weighted scoring,
  configured penalties, freshness mapping, and candidate ranking.
- Added `content/config/sources.yaml`: seeded RSS/Atom discovery template for
  the daily run.
- Added the `content-bot/` service (Compose profile `content`): Telegram
  long-polling bot with on-demand link drafts, Approve/Reject inline buttons,
  channel publishing, per-day caps, duplicate-post protection, and a daily
  editorial scheduler driven by the working-copy policy.
- `install.sh` now offers the Content Bot interactively, asks for the bot
  token, operator IDs, publish channel, and writer model, and provisions the
  unprivileged container, secrets, and state directory automatically.
- `manage.sh` gained `content-status`, `content-connect-instagram`, the
  interactive Content Bot group, and `./manage.sh logs content`.
- Added `.github/workflows/publish-content-bot.yml`: pushing Content Bot
  source to `main` builds and publishes
  `afsharidevops/content-bot:0.1.0` (plus `:latest`) to Docker Hub.
  `install.sh` now pulls that published image instead of building the
  container locally, and the daily scheduler honors `daily_proposal_time`
  from `editorial-policy.yaml`.
- Added `docs/CONTENT-PRODUCTION-GUIDE.md` and
  `docs/INSTAGRAM-SETUP.md`; Instagram remains a guided pending checklist.

### Fork additions — editorial loop and standalone deployment (2026-09-09)

- Draft behavior is now policy-driven: on-demand freshness and the daily
  proposal cap are read from the working-copy editorial policy, and
  operator-sent links are never blocked by the freshness window or the daily
  cap.
- Added the reply-based revision loop: replying to a proposal with edit notes
  and pressing Reject revises the draft in place; pressing Reject without
  notes discards it. Empty or unchanged writer revisions are surfaced to the
  operator instead of re-posting the original draft.
- Draft titles render bold with RTL-safe ordering, and daily feed parsing is
  hardened to HTML titles and links instead of raw page text.
- Seeded `content/config/sources.yaml` with vetted RSS/Atom feeds per category
  and an in-file guide for adding and pre-testing new feeds.
- Added standalone Content Bot deployment: installer option 5 provisions only
  the Content Bot against an external OpenAI-compatible writer API (base URL,
  key, model), and `./install.sh --content-reconfigure` or
  `./manage.sh content-configure` reconfigures it while preserving existing
  components and data.
- Writer calls tolerate null or truncated responses and honor
  `CONTENT_WRITER_MODEL=auto`, `CONTENT_WRITER_MAX_TOKENS`, and
  `CONTENT_WRITER_REASONING_EFFORT`.
- Added the operator `/forget-link <url>` command (plus `/forget_link` alias)
  so a previously published link can be drafted again; the Telegram command
  menu is registered on startup in the default and private-chat scopes.

### Fork additions — media handoff, uploads, RTL-safe copy (2026-09-09)

- The media question after a draft now offers four choices: text only, an
  AI-generated image, a photo the operator uploads, or a ready-to-use video
  prompt so the operator creates the clip elsewhere and uploads it.
- Upload handling is guarded per draft: a Cancel button, wrong-kind rejection,
  size checks, and blocked new drafts while an upload is pending; uploaded
  files are stored locally, and uploaded photos are branded by Media Studio
  like AI images while uploaded videos stay unchanged.
- Media preview buttons now match the media source: retry options for
  AI-generated media and Approve/Reject options for operator uploads.
- Mixed Persian/Latin post bodies render RTL-safe in Telegram: every line that
  needs it carries a right-to-left mark, and writer prompts require paragraphs
  to open with a Persian word so Latin product names never flip the line.
- Writer replies that arrive as legacy-charset mojibake are repaired
  automatically; replies that cannot be repaired are refused instead of being
  published as garbage.

### Fork additions — configurable corner brand chip (2026-09-09)

- Added `media-studio/media_studio/branding.py`: a translucent rounded chip
  with the configured brand label is drawn in a corner of every raster
  artifact produced by an image driver. The chip is disabled when the label
  is blank, and jobs can opt out with `"params": {"brand": false}`.
- Media Studio exposes `POST /brand` (raw image bytes in, branded bytes out);
  the Content Bot calls it for every uploaded photo so operator media carries
  the same brand as AI images. Branding failures never fail a job or an
  upload; the original file is kept.
- Configuration: `MEDIA_STUDIO_BRAND_LABEL` (default `Locallab`, blank
  disables) and `MEDIA_STUDIO_BRAND_POSITION` (default `bottom-right`, also
  `bottom-left`, `top-right`, `top-left`), wired through `docker-compose.yml`
  and `.env.example`.
- Media Studio gained Pillow as a runtime dependency for the overlay.

---

## Hermes Linux Stack — v0.5.9 Changelog — 2026-08-13

### v0.5.9 release focus

Visual Flow Connections across Workflow, Agent, Router Pipeline, and Knowledge Pipeline studios, while preserving Hermes execution trust boundaries.

### v0.5.9 release acceptance

Automated candidate, API/persistence, regression, Compose, manifest, and full Smart Router smoke validation passed during release preparation. The release owner explicitly requested early finalization and waived the remaining manual browser light/dark rendering and mouse/trackpad interaction gate. The waived checks are not represented as passed.


- Fixed direct Users, Policies, and Plugins create APIs returning HTTP 500 after commit because audit logging dereferenced expired SQLAlchemy rows outside their sessions.

- Added the v0.5.9 visual-flow implementation work without changing the runtime release version.
- Updated Hermes stack plugins for the current plugin registration API.
- Persist Telegram enablement for `stack-execution-policy` and `stack-package-policy` during installation.
- Updated `manage.sh` runtime diagnostics to verify the current stack plugin toolset names.
- Added regression coverage for Telegram plugin-toolset persistence and runtime registration.
- Implemented v0.5.9 Phase 1 shared graph contract for Workflow and Knowledge studios: port-aware edges, legacy graph normalization, typed compatibility, named outputs, drag-to-connect preview/target validation, selectable/deletable edges, dirty state, and server-side duplicate/self/cycle checks.
- Completed the v0.5.9 visual-flow development candidate across all four studios: Agent and Router shared-canvas integration, persisted agent composition graphs, legacy graph synthesis, named Router branch outputs (classifier/condition/capability/health/approval), branch-aware runtime traversal, edge reconnection, drop-on-empty quick add, undo/redo, pan/zoom/fit controls, keyboard/pointer accessibility, explicit save states, and backward-compatible port-aware persistence.
- Preserved the execution trust boundary: visual orchestration and Telegram UX do not grant Docker, SSH, signing-key, or Execution Admin authority.

---

## Hermes Linux Stack — v0.5.8 Changelog

### Release focus

v0.5.8 is the visual-building and operator-UX release for the 9router branch. It keeps the v0.5.7 trust-separated execution design while making browser connectivity failures diagnosable and adding visual studios for workflows, agents, router pipelines, and knowledge pipelines.

### Execution & Approvals reliability

- Execution Broker target advances to `0.1.3`.
- The Operations Center now probes the Execution Admin `/health` endpoint before attempting key authentication. Network/bind/CORS failures are reported separately from key failures.
- Added `./manage.sh configure-execution-admin-browser ORIGIN [PRIVATE_IPV4]` to configure a private bind plus exact browser origin without printing the Execution Admin key.
- Execution Admin CORS preflight supports the Private Network Access response header for explicitly allowed origins.
- The key remains browser-memory-only and is still sent directly from the operator browser to Execution Admin; Smart Router does not receive it.
- Wildcard/public bind shortcuts are intentionally refused by the helper.

### Operations Center redesign

- Reworked dark and light palettes around semantic surface/text/border tokens.
- Reorganized navigation into Observe, Build, Tools, Routing, Access, and System.
- Added Dify-inspired visual interaction patterns without copying Dify branding or assets.
- Added Workflow Studio with draggable nodes, visible edges, node inspector, references, and save/edit lifecycle.
- Added Agent Studio with visible Input → Knowledge → Agent → Tools → Answer path and configuration inspector.
- Added Router Pipeline Studio for conditions, capability/health filters, scoring, load balance, route, retry, fallback, and approval stages.
- Added Knowledge Pipeline Studio and persistent `v58_knowledge_pipelines` registry.
- Added Publish & Monitor workspace that links API, Flight Deck, agent testing, and runtime status.
- Improved Flight Deck light-mode surfaces, sidebar, controls, and status pills.

### Data and API

- Smart Router runtime/control schema marker advances in place to `0.5.8`.
- Compatibility database filename remains `control-v0.5.2.sqlite3`; persistent Operations Center state is upgraded in place.
- New Operations API routes:
  - `GET/POST /control/api/knowledge-pipelines`
  - `PUT/DELETE /control/api/knowledge-pipelines/{id}`
- Knowledge pipeline graph validation accepts only managed ingestion/indexing node types and validates node IDs, edge references, and graph limits.

### Image targets

- Smart Router: `afsharidevops/hermes-smart-router:0.5.8`
- Execution Broker: `afsharidevops/hermes-execution-broker:0.1.3`
- Intended platforms: `linux/amd64`, `linux/arm64`

### Validation performed in packaging environment

- Smart Router pytest suite: 96 passed per branch.
- Execution Admin focused unittest suite: 5 passed per branch.
- `manage.sh` UX tests: passed per branch.
- `bash -n` for `manage.sh` and `install.sh`: passed.
- Python compilation for modified Smart Router UI/control files: passed.
- Operations Center JavaScript syntax (`node --check`): passed.

The broader root Python test discovery also contains environment-dependent tests requiring `ssh-keygen` and a fully usable npm prefix. Those checks could not complete in this packaging environment and should be run in the normal CI/release host before production publication.

### Security invariant

UI convenience must not collapse execution authority into Smart Router. Execution Admin continues to exclude the Ed25519 approval-signing private key, Docker socket, and SSH private credentials, while Smart Router continues to exclude the Execution Admin key and execution approval secrets.

### Post-release private-ingress hotfix

- Execution Admin now joins a dedicated `execution-admin-ingress-net` in addition to the internal `execution-control-net`.
- The control network remains `internal: true`; only Execution Admin receives the host-ingress bridge.
- This allows `${EXECUTION_ADMIN_BIND_IP}:${EXECUTION_ADMIN_PORT}:8752` to be actually published on Docker while preserving broker isolation.
- Smart Router and Execution Broker application images are unchanged by this Compose-only networking fix.
- Smart Router publishing documentation/workflow now uses only the plain version tag and `latest`; no `v<version>` or SHA alias is emitted.
- `MANIFEST.sha256` is regenerated from repository-tracked release files, removing stale ignored cache entries that are not present in a fresh clone.

---

## Hermes Linux Stack — v0.5.7 Changelog

### Focus

v0.5.7 integrates secure execution administration with the v0.5.6 Operations Center without collapsing the execution-broker trust boundaries.

### Execution & Approvals

- Added **System → Execution & Approvals** to Hermes Operations Center.
- Added `execution-admin` mode to Hermes Execution Broker `0.1.2`.
- Operations Center talks directly from the operator browser to the separate Execution Admin endpoint with a separate admin key.
- Smart Router backend does not receive the Execution Admin key or dedicated Telegram approval-bot token.
- Execution Admin does not mount the Ed25519 approval signing key, Docker socket, or SSH private credentials.
- Added live redacted health for approver, Docker broker and SSH broker.
- Added live enable/disable policy for already-deployed `local`, `docker`, and `ssh` execution capabilities.
- Added Telegram execution approver management, constrained to IDs already present in `TELEGRAM_ALLOWED_USERS`.
- Added write-only dedicated approval-bot token replacement. The token is never returned by the API.
- Added protection preventing the execution approval bot from reusing the Hermes Telegram bot token when the Hermes token hash is synchronized.
- Added broker control-secret rotation from the separate admin boundary.
- Added redacted SSH profile listing without exposing private keys/passwords.
- Added execution-admin audit events.
- Every execution policy/admin mutation increments the policy generation, invalidating older pending capabilities/approvals.

### Dynamic execution policy

- Added `EXECUTION_FEATURES_FILE` support to the Hermes execution policy plugin and execution brokers.
- Added `EXECUTION_POLICY_GENERATION_FILE` support to Docker, SSH and approver modes.
- Policy files are bind-mounted and rewritten in place to preserve host ownership and permissions.
- Existing environment variables remain compatibility fallbacks.
- First-time broker deployment remains a host `manage.sh` operation; the UI does not need Docker-socket authority.

### Management commands

Added:

```text
./manage.sh enable-execution-admin
./manage.sh disable-execution-admin
./manage.sh execution-admin-status
./manage.sh show-execution-admin-key
./manage.sh rotate-execution-admin-key
```

The existing execution commands remain supported.

### Security defaults

- Execution Admin binds to `127.0.0.1:8752` by default.
- Browser CORS uses an exact allowlist (`EXECUTION_ADMIN_ALLOWED_ORIGINS`); wildcard origins are not used.
- The admin credential is separate from Smart Router authentication.
- Operations Center keeps the Execution Admin key only in page memory; it is not written to localStorage.
- Bot token readback is not implemented.
- Signing-key rotation and SSH credential creation/removal remain local operator/CLI operations.

### v0.5.6 platform foundations retained

v0.5.7 retains the v0.5.6 light/dark UI, lifecycle fixes, hybrid vector RAG/pgvector support, Flight Deck traces, guardrails, router pipelines, workflows, prompt versioning, datasets/evaluations, model catalog, marketplace/onboarding and HA foundations.

### Compatibility

- Smart Router runtime/schema marker: `0.5.7`.
- Existing `control-v0.5.2.sqlite3` compatibility filename is preserved and upgraded in place.
- Smart Router image target: `afsharidevops/hermes-smart-router:0.5.7`.
- Execution Broker image target: `afsharidevops/hermes-execution-broker:0.1.2`.
- Branches: `main` (9router) and `hermes-omniroute-linux-stack` (OmniRoute).

---

## Hermes Linux Stack v0.5.6 — Platform Foundations

v0.5.6 advances Hermes from an operator-focused Smart Router into a broader self-hosted AI infrastructure platform while preserving the existing `control-v0.5.2.sqlite3` compatibility filename and explicit-model pass-through behavior.

### Major additions

- Light/dark themes in Hermes Operations Center and Flight Deck, persisted in the browser.
- Consistent lifecycle UX: Agents, Teams, and Groups use reversible Enable/Disable controls; permanent deletion is a separate destructive action. Group purge protects ACL references unless explicit cascade deletion is requested. Plugins and Skills have explicit enable/disable and permanent removal controls.
- Hybrid Knowledge/RAG with lexical + vector retrieval, embedding indexing, score fusion/reranking, PostgreSQL `pgvector` acceleration where available, and a deterministic portable vector fallback for offline/development use.
- Full request trace records for request/auth/authorization/guardrails/RAG/classification/routing/retry/fallback/result stages, visible in Flight Deck and Operations Center.
- Guardrail engine foundations for prompt-injection indicators, PII indicators, content deny rules, tool allow-lists, and high-risk tool confirmation policy. Modes: `off`, `audit`, `enforce`.
- Advanced router pipeline definitions with conditions, route stages, load balancing, retries, and fallback model chains.
- Workflow graph registry and visual workflow preview for Agent/Team orchestration.
- Prompt registry with immutable versions, activation/rollback, notes, and history.
- Evaluation datasets, dataset items, and A/B evaluation-run definitions.
- Model catalog synchronized from the upstream `/models` endpoint with capability/context/pricing/health/latency metadata where available.
- Plugin/Skill marketplace view built on the safe catalog/registry model.
- First-run Operations Center onboarding flow.
- PostgreSQL + pgvector + Redis + two-router HA Compose example and smoke/load-test tooling.
- Enterprise identity readiness page. OIDC remains the completed interactive login path; LDAP/SAML/SCIM are connector/provisioning foundations and require deployment-specific integration before production use.
- Static public-docs starter with release screenshots and demo deployment example.

### Compatibility and non-regression rules

- `model=auto` enters Smart Router automatic selection.
- Explicit upstream model IDs pass through unchanged.
- Existing SQLite data is upgraded in place; the compatibility DB filename is not renamed simply because the software version changes.
- Catalog plugin installation does not download/execute arbitrary untrusted code. Lifecycle state, permissions metadata, endpoint configuration, and safe registration remain controlled by the operator.

### Production notes

For real semantic vector RAG, configure an embeddings endpoint and use PostgreSQL with the `vector` extension. The built-in deterministic embedding fallback is useful for tests/offline operation but is not a substitute for a production embedding model. Run the HA smoke and load-test scripts against your own infrastructure before calling a deployment production-HA certified.

---

## Hermes Linux Stack v0.5.5

### Operations Center runtime controls, agent lifecycle, skills, groups, and built-in docs

v0.5.5 is an operator-control and usability release built on v0.5.4. It preserves the existing Smart Router data directory and the default `control-v0.5.2.sqlite3` compatibility filename while upgrading the Operations Center schema in place to `0.5.5`.

#### Smart Router / Operations Center

- Live UI editing for `router_mode`: `observe` or `route`.
- Live UI editing for `router_policy`: `heuristic`, `calibrated`, or `learned`.
- UI control for HA mode with a guard that requires Redis before HA can be enabled.
- Runtime UI settings persist in the Operations database and override startup environment values until **Reset to environment** is used.
- System page now reports the schema version and explains why the default DB filename can remain `control-v0.5.2.sqlite3`.
- OIDC login button corrected to the actual `/api/auth/oidc/start` route.
- ACL UI corrected to use backend subject type `virtual_key`; `group` subjects are now exposed.

#### Agents

- Create Agent errors are shown instead of failing silently.
- Agent names and referenced Knowledge/Plugin/Skill IDs are validated.
- Agents can be edited in the UI.
- Agents can be disabled reversibly or permanently deleted.
- Agent forms use existing Knowledge, Skill, and Plugin records instead of requiring blind CSV IDs.

#### Skills and plugins

- New reusable Skill registry with curated suggestions for Linux, Docker, networking, MikroTik, automation safety, and incident response.
- Skills can be installed from the built-in catalog or registered manually, including commercial/license metadata without storing license secrets.
- Skills assigned to an agent are injected into that agent's system context during runs.
- Suggested Plugin catalog can install safe registry templates for GitHub MCP, read-only PostgreSQL, Kubernetes observation, and MikroTik observation.
- Plugin catalog installation does not download or execute arbitrary code and installs suggested entries disabled by default.

#### Access groups

- New Access → Groups menu.
- Groups contain Operations Center usernames.
- ACL rules with `subject_type=group` now resolve real group membership.

#### Help and documentation

- New built-in **Docs** menu covers routing, system controls, users/keys/groups/ACLs, Knowledge, Memory, Agents, Skills, Plugins, Teams, upgrades, backups, and troubleshooting examples.
- Memory page now explains scope behavior and warns against using memory as a secret store.

#### Database compatibility

The default filename remains:

```text
sqlite:////data/control-v0.5.2.sqlite3
```

This is intentional. v0.5.5 creates its new tables in the same database and updates the `schema_versions` marker to `0.5.5`, preserving v0.5.1/v0.5.2-era users, routes, keys, policies, budgets, knowledge, memory, agents, teams, plugins, ACLs, audit, and outcome records.

New v0.5.5 tables include:

```text
v55_runtime_settings
v55_access_groups
v55_skills
v55_agent_skills
```

Back up the persistent Smart Router database before any production upgrade.

---

## v0.5.4 change summary — 9router

- Fixed the hidden 200,000 TPM ceiling on `SMART_ROUTER_CLIENT_API_KEY`; trusted stack-client RPM/TPM/daily limits are explicit (2,000,000 TPM default).
- Local quota 429 responses now identify Smart Router as the source, include current/limit/estimated values, and return `Retry-After`.
- Redis/HA quota checks are atomic: denied requests no longer consume additional quota.
- Virtual API-key RPM/TPM/daily limits can be edited in-place in `/control/` without rotating the key.
- Visible **Control Plane** naming is replaced by **Hermes Operations Center**; `/control/` and legacy `SMART_ROUTER_CONTROL_*` variables stay compatible.
- RAG knowledge tables can share the Operations DB or use a separate SQLite/PostgreSQL database through `SMART_ROUTER_KNOWLEDGE_DATABASE_URL`.
- The Operations Center shows RAG storage health/backend and labels the built-in retriever accurately as lexical.
- The persistent default filename remains `control-v0.5.2.sqlite3` so upgrades preserve state.
- Runtime/package/image/chart defaults are `0.5.4`.

---

## v0.5.3 change summary — 9router

### User experience

- `./manage.sh` now opens a grouped interactive manager by default.
- Added dedicated Services, Smart Router, Hermes, n8n, Execution, Maintenance, and Security menu groups.
- Smart Router mode/policy/time-window/feature choices are shown as numbered choices instead of requiring memorized values.
- Direct v0.5.2 commands remain backward compatible for scripts and automation.

### Hermes Smart Router Flight Deck

- Redesigned `/dashboard` as the Hermes Flight Deck with access state, time-window and auto-refresh selectors, routing-flow explanation, telemetry quality, route mix, and explicit zero/pricing states.
- Redesigned `/control/` navigation into Observe, Routing, Access, Intelligence, and System groups.
- Added dropdown/select controls for common finite-value choices such as roles, budget scopes, ACL effects, agent profiles, team strategy, plugin kind/risk, and API-key tiers.
- Dashboard and Control Plane link to each other.

### Compatibility

- Runtime/package version is 0.5.3.
- Fresh installs pin `afsharidevops/hermes-smart-router:0.5.3`.
- Existing v0.5.2 Control Plane SQLite/schema naming is intentionally preserved for in-place data compatibility.
- Corrected the installer status message so it reports the selected Smart Router mode instead of always saying observation mode.

---

## v0.5.2 change summary — main

- fixed router-info endpoint and retained compatibility alias
- hardened installer secret rotation and Smart Router client-key synchronization
- removed fixed Docker GID assumption
- enabled control/client auth by default and disabled Open WebUI signup by default
- made application image tags `.env`-overrideable while retaining `latest`/`main` defaults
- added provider health/circuit breakers, shared Redis state/stickiness, OIDC/ACL/secrets foundations, outcome capture and provider quality metadata
- added doctor/backup/restore/rollback/image-lock operations
- added HA Compose and Helm foundations plus security CI
- reconciled package version/documentation to Smart Router v0.5.2

See `docs/HERMES-SMART-ROUTER-v0.5.2-IMPLEMENTATION-STATUS.md` for items still open and not claimed complete.

### Post-review cleanup

- Replaced the Docker execution GID 0 fallback with fail-closed sentinel GID 65534; `install.sh` still detects the actual Docker socket GID when available.
- Removed a dead duplicate `/router/info` handler definition.
- Deduplicated Redis/Authlib dependency constraints to their prior effective versions.
- Removed duplicate long-range planning documentation from the active release tree.
- Updated smoke validation for configurable Smart Router image tags (`latest` by default, pinnable via `.env`).
