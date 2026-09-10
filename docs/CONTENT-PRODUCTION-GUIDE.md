# Content production guide

This guide explains how Content Manager produces daily posts: how the Telegram
Content Bot turns links into approved drafts, how the daily scheduler proposes
items from your feeds, and where the editorial rules live.

## What runs where

- **Content Bot** (`content-bot` container, Compose profile `content`) polls the
  Telegram Bot API directly, so no public ingress or webhook URL is required.
- **Editorial working copy**: `data/content-manager/config/` holds
  `editorial-policy.yaml`, `categories.yaml`, and `sources.yaml`. These files
  are seeded from `content/config/` during install and are yours to edit; later
  install runs never overwrite them.
- **Pipeline code**: `content/content_pipeline/` (normalize, dedupe, filter,
  score). Everything stored in the repository is English-only; Persian post
  copy is produced at runtime by the writer model.

## What the installer asks

The wizard can provision the Content Bot alongside a full stack (select it
during `./install.sh`) or as a standalone content production server. The
standalone menu offers:

- `5) Install Content Bot only (external OpenAI-compatible model API)` —
  Content Bot alone; the wizard then asks whether Media Studio should be
  installed too for AI images and uploaded-photo branding.
- `6) Install Media Studio only (API image generation; Google Flow/Gemini
  optional)` — Media Studio alone; the wizard asks whether the Content Bot
  should also be installed to drive it.
- `7) Install Content pipeline only (Content Bot + Media Studio, external
  model API)` — both services with no router, Hermes, n8n, or Open WebUI.

On a combined pipeline server the wizard wires the two services end to end:
`CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850`, the Media Studio API token
is mirrored into `CONTENT_MEDIA_STUDIO_TOKEN`, and the bot image/video drivers
match the enabled Media Studio drivers. Reconfiguring either service keeps the
link in sync. In all cases the wizard asks for:

1. A **bot token** created with `@BotFather` (the Content Bot uses its own bot,
   separate from the Hermes agent bot, because two pollers cannot share one
   token).
2. The **channel ID or @username** where approved posts are published. The bot
   must be added to that channel as an administrator.
3. The **numeric operator Telegram IDs** allowed to approve and reject drafts
   (defaults to the Hermes Telegram allowlist when Hermes is enabled).
4. The **writer endpoint, API key, and model**. With the Smart Router enabled,
   the installer defaults to `http://smart-router:8080/v1` with model `auto`
   and stores the trusted router client key automatically. In the standalone
   mode there is no local router, so the wizard requires your own
   OpenAI-compatible base URL (including `/v1`, for example
   `https://api.openai.com/v1`), its API key, and the exact model id the API
   expects (for example `gpt-4o-mini`).

The token and key are stored in `.env` with mode `0600` and are never printed.
Split deployments work too: a content-only server points
`CONTENT_MEDIA_STUDIO_URL` at the remote Media Studio URL and uses the same API
token on both hosts.

## On-demand flow: link to published post

1. Open your Content Bot in Telegram and send it any `http(s)` link.
2. The bot fetches the page, extracts the article, and runs the deterministic
   editorial filters from `editorial-policy.yaml` (freshness, blocked domains,
   low-value titles, topic blocklists). Rejected links are answered with the
   reason.
3. The writer model turns the source into a fresh Persian draft (title + body)
   and sends you a preview with **Approve** and **Reject** buttons. Titles are
   rendered bold and right-aligned on Telegram, so the writer starts titles
   with a Persian word and keeps Latin brand names later in the title.
   Body paragraphs are RTL-safe as well: every mixed Persian/Latin line is
   marked to keep a right-to-left base direction, and the writer is told to
   open each paragraph with a Persian word.
4. **Approve** publishes the post to the configured channel and records the
   source hash so the same link is never posted twice. Approvals of links you
   send yourself are never limited and do not consume the scheduled daily
   quota (`max_approved_per_day` applies to daily proposals only).
5. **Revise before approving**: reply to the proposal message with edit notes
   (for example, "make the intro shorter") and then press **Reject**. The bot
   asks the writer to revise the draft from your notes and updates the same
   proposal in place. You can repeat the reply-then-Reject cycle any number of
   times until the post reads the way you want.
6. **Reject** without a reply comment discards the draft entirely.

Commands in the bot chat: `/start`, `/help`, `/status`, `/tools`, and `/instagram`.

## Topics and short pages

- Send a plain message without a link (for example "post about container
  image layers") and the bot searches the web for context, then drafts the
  post from the best results. Disable this with
  `CONTENT_TOPIC_DRAFTS_ENABLED=false`.
- A link whose page contains almost no readable text is enriched with a
  search before drafting instead of being rejected; the original link stays
  the post source. Disable the search fallback with
  `CONTENT_SEARCH_ENABLED=false`.
- The default search provider is DuckDuckGo's HTML endpoint and needs no API
  key. Search tuning: `CONTENT_SEARCH_MAX_RESULTS` (default 5) and
  `CONTENT_SEARCH_TIMEOUT` (default 25 seconds).

## Media for a draft

Every fresh on-demand draft is followed by an "Add media to this post?"
question with four choices:

- **Text only** - the draft stays a normal text post.
- **AI image** - the bot submits an image job to Media Studio
  (`CONTENT_MEDIA_IMAGE_DRIVER`, default `api-image`) and shows the result for
  review.
- **Send my image** - the bot waits for a photo you send in the chat and
  attaches it to the draft. Uploaded photos get the same corner brand chip as
  AI-generated images; uploaded videos are stored unchanged.
- **My video (get a prompt)** - the bot composes a copy-ready prompt
  package from the post title and body (see "Video prompt packages" below).
  Create the clip yourself in any tool (for example Google Flow), then send
  the video file back in the chat; the bot attaches it to the draft for
  approval.

While the bot waits for an upload, the media question shows a **Cancel
upload** button and new drafts are blocked until the upload is cancelled or
completed. Sending a file while no draft is waiting is rejected with an
explanation; sending the wrong media kind for a waiting draft keeps the draft
waiting.

Videos above 20 MB stay on Telegram: the Bot API cannot download them, so the
bot keeps the file id, previews the same clip, and publishes it to the channel
from that id. Instagram approvals and the platform upload packages need the
file itself, so they ask you to send a copy under 20 MB.

The brand chip is configurable on Media Studio: `MEDIA_STUDIO_BRAND_LABEL`
(default `Locallab`, blank disables branding) and
`MEDIA_STUDIO_BRAND_POSITION` (default `bottom-right`). Every AI-generated
image and every uploaded photo is branded in a corner before it is previewed
or published.

### Video prompt packages

The **My video (get a prompt)** choice does not generate the clip; it hands
you a package you can paste into an external video tool such as Google Flow,
which generates roughly ten seconds per clip and extends them on demand:

1. Choose the style. When `CONTENT_VIDEO_CHARACTER` names a saved Flow
   character (for example `@mohammad`), the bot asks **With my character** or
   **AI promo (no character)**. With no configured character it goes straight
   to the AI promo package.
2. Choose the length: **~10 seconds** (one segment) or **Up to 30 seconds**
   (three segments by default, from `CONTENT_VIDEO_SEGMENT_SECONDS`).
3. The bot picks one scene setting and one Persian narration line per segment
   from the post and sends the prompts. Short packages arrive in one message;
   longer packages arrive as one copy-ready message per segment.
4. In Flow: paste the segment 1 prompt, generate, press **Extend**, paste the
   next segment prompt, and repeat to the end. Join the clips afterwards if
   you want a single file.
5. Send the finished video back in the chat. The bot then asks **Edit it**
   or **Publish as-is**:
   - **Edit it** uploads the clip to Media Studio, which normalises it with
     ffmpeg (MP4 with H.264/AAC and faststart, long side capped at 1920 px,
     rotation fixed, metadata stripped) and shows the edited preview. If the
     edit fails, the original clip is kept and the bot says so.
   - **Publish as-is** keeps the file exactly as recorded.
   Both answers only decide the media; the draft still needs the usual
   **Approve**. Videos above 20 MB stay on Telegram and skip the question
   because the bot cannot copy them into Media Studio. Uploaded videos are
   never branded.

Character reels repeat the same character description in every segment so
Flow keeps the face and voice consistent, while AI promo reels describe the
shot only and list the Persian line as an optional voiceover.
`CONTENT_VIDEO_CHARACTER_PROMPT` replaces the built-in character block and
must keep the `{handle}` and `{aspect}` placeholders; `CONTENT_VIDEO_ASPECT`
sets the frame (default `9:16 vertical`). When the writer backend is
unavailable the narration lines fall back to the post text.

### Tool registry

The stack keeps one registry file (`content/config/tools.json`, working copy
`data/content-manager/config/tools.json`) that lists the remote tools the
Content Bot, the Smart Router, and n8n may call. Send `/tools` in the bot chat
to see the entries marked for the bot, together with the environment variable
each one needs; `/status` shows how many entries exist. The file format and
the per-service wiring are documented in `docs/TOOL-REGISTRY.md`.

Experimental AI video jobs (`CONTENT_MEDIA_VIDEO_DRIVER`, default
`flow-video`) can still be triggered from older media questions, but Google
Flow requires interactive confirmations (credit usage and storyboard
approval) plus a signed-in browser session, so it is not recommended for
automated runs; see `docs/CONTENT-PRODUCTION-ARCHITECTURE.md`.

When the media is ready:

1. The bot sends a preview (photo or video). AI-generated previews offer
   **New attempt** and **Text only**; uploaded files offer **Approve**,
   **Reject**, and **Text only**.
2. Review the preview, then press **Approve** on the draft message or on the
   media preview to publish. Media posts go to the channel as one photo or
   video whose caption contains the bold title, the post body, and the
   clickable source link. Telegram caps media captions at 1024 characters,
   so when the body is longer the bot keeps as many paragraphs as fit in the
   caption and immediately sends the remaining paragraphs as continuation
   messages; in that case the source link moves to the end of the last
   message. Text-only posts are sent as a single regular message.
3. Reply with edit notes and press **Reject** to revise the text only; the
   attached media stays valid for the next approval.
4. **Reject** without notes discards the draft and its media.

Owner feedback given in replies is stored in the bot state as lessons and is
injected into later copy prompts as guidance (recent six lessons). Media job
history is kept on each draft record (`status`, `history`) under
`data/content-bot/state.json`; downloaded files live in
`data/content-bot/media/`.

## Policy-driven behavior (no code changes)

All tunable behavior lives in `data/content-manager/config/editorial-policy.yaml`
and is reloaded on every request and callback:

- `on_demand.enforce_freshness` - set `true` to apply `freshness_hours` to
  links you send the bot yourself (default `false`).
- `on_demand.unlimited_approvals` - set `false` to make your own link
  approvals count against `max_approved_per_day` (default `true`).
- `pipeline.max_approved_per_day` - daily proposal cap.
- `routines` - scheduled per-platform passes (research -> draft -> media ->
  queue); see the section below.
- `freshness_hours`, `exclusions`, and `scoring` - discovery-time filters.

## Daily flow: scheduled proposals

1. At `daily_proposal_time` (default `08:00`, timezone `Asia/Tehran`) in
   `editorial-policy.yaml`, the bot reads `sources.yaml` from the working copy.
2. Each configured RSS/Atom feed is fetched and parsed; items are normalized,
   deduplicated, filtered, and scored with `content_pipeline/score.py`.
3. The top candidates (default 5, `max_candidates`) are drafted and sent to the
   first operator ID with Approve/Reject buttons. Approvals count against the
   daily cap (`max_approved_per_day`, default 3); operator-sent links do not.
4. `max_consecutive_same_category` prevents same-category streaks across the
   batch and previously published items.

Add feeds by editing `data/content-manager/config/sources.yaml`:

```yaml
sources:
  - name: "Example Tech Feed"
    url: "https://example.com/feed.xml"
```

With no sources configured, the daily run notifies the operator and skips.
Disable the scheduler entirely with `CONTENT_SCHEDULER_ENABLED=false`.

## Scheduled routines (per-platform cadence)

`routines:` in `editorial-policy.yaml` adds scheduled multi-step passes on top
of the single daily proposal: research -> draft -> media -> queue. Each enabled
routine runs once per cadence period, drafts up to `count` of the ranked
candidates, optionally attaches an AI image, and drops everything into the same
Telegram approval queue (the draft card reads "Scheduled proposal").

```yaml
routines:
  - id: instagram-daily      # unique key; remembered in data/content-bot/state.json
    platform: instagram      # label only, shown in /status and on drafts
    enabled: true
    cadence: daily           # daily | weekly
    time: "10:00"            # local time in pipeline.timezone
    weekday: monday          # weekly cadence only
    count: 1                 # drafts queued per run
    media: auto              # auto (AI image) | none
```

- A daily routine runs once per local day; a weekly routine runs once per ISO
  week on or after `weekday`. The run marker lives in
  `data/content-bot/state.json` under `routine_last_run`, so a restart never
  repeats a period.
- `media: auto` submits one AI image per queued draft through Media Studio
  (`CONTENT_MEDIA_IMAGE_DRIVER`, default `api-image`) and delivers it as the
  usual media preview when the job finishes. `media: none` queues text only.
  Without Media Studio configured the drafts are still queued, and a failed
  image job is reported to the operator.
- Routines share the discovery feeds, dedupe/filter/scoring rules, and the
  `max_consecutive_same_category` rule with the daily run; `/status` lists the
  active routines.
- The shipped default is `routines: []`, so nothing is scheduled until you add
  one; the daily proposal flow above keeps working unchanged.

## Telegram setup checklist

1. Create the bot: open `@BotFather`, run `/newbot`, and copy the token.
2. Find your numeric Telegram ID (for example via `@userinfobot` or by checking
   a bot `getUpdates` response).
3. Create the channel (or reuse one) and note its `@username` or numeric
   `-100...` ID.
4. Add the bot to the channel as an **administrator** (the bot posts through
   the Bot API, so admin rights are required).
5. Run `./install.sh`, choose the Content Bot, and paste the values. After the
   stack starts, send `/start` to the bot and test with a link.
   On a dedicated server choose `7) Install Content pipeline only` for Content
   Bot plus Media Studio on one host, or `5) Install Content Bot only` and
   answer the Media Studio follow-up question. Answer the external API
   questions; no router or Hermes services are installed.

## Platform status

| Platform | State |
| --- | --- |
| Telegram | Live: link/topic drafts, image/video attach, Approve/Reject, channel publish |
| Instagram | Official Meta Graph API: photos, carousels, and video; the long-lived token refreshes itself, `/instagram` reports the expiry, and the `ig-media` profile publishes the media directory at a public URL (`./manage.sh instagram-media-status`); see `docs/INSTAGRAM-SETUP.md` |
| YouTube / Aparat | Optional copy-ready upload package (off by default); see "Manual upload platforms" |
| Media assets (images/video) | Optional Media Studio worker; API-image driver live, Google Flow/Gemini drivers ready for session calibration |

## Manual upload platforms

Telegram and Instagram publish through their APIs; this part of the bot is
off by default and only appears when the manual upload packages are enabled
with `CONTENT_PLATFORMS_ENABLED=true` (a live bot otherwise shows Approve and
Reject, and nothing else). Once enabled, platforms that need a human upload
step (YouTube, Aparat) are reachable from the same previews:

1. Press **More platforms...** on the draft preview or a media preview.
2. Pick the platform. The bot sends a copy-ready package with the title (cut
   to the platform limit), the description with the source link and
   hashtags, the direct upload link, and the stored media file re-sent for a
   quick download.
3. Upload the file in the platform editor and paste the two text blocks.

A package never publishes anything by itself, never consumes the draft, and
never counts toward the daily limit; **Approve** and **Reject** keep working
as before. The package content is driven by the `platforms:` section of
`editorial-policy.yaml`: override the label, upload URL, title and description
limits, note, or hashtags, add your own platform keys, or set a key to `null`
to hide its button. Telegram and Instagram are not listed there because they
publish through their APIs. Packages stay switched off - and the `platforms:`
section is unused - until `CONTENT_PLATFORMS_ENABLED` is set to `true`.

## Media assets

Media Studio generates images through the same OpenAI-compatible gateway as
the Content Bot (`api-image` driver, no Google account required) and can drive
Google Flow video and Gemini images through a signed-in browser session. It
runs as its own container with a JSON job API on `127.0.0.1:8850`; the
Content Bot submits image/video jobs and publishes returned media after
approval. The Google Flow region unlock is available standalone for laptop
use (`docs/FLOW-UNLOCK-STANDALONE.md`), independent of the stack. See
`docs/MEDIA-STUDIO.md` for install, sessions, API, and calibration, and
`docs/CONTENT-PRODUCTION-ARCHITECTURE.md` for the end-to-end flow design.

## Operations

```bash
./manage.sh pipeline-status            # Content Bot + Media Studio + API link status
./manage.sh content-status              # configuration summary, no secrets
./manage.sh content-connect-instagram   # Instagram/Meta setup checklist
./manage.sh content-configure           # reconfigure Content Bot settings only
./manage.sh logs content                # follow Content Bot logs
./manage.sh configure                   # change Content Bot settings later
```

State (pending drafts, published hashes, daily counters) lives in
`data/content-bot/state.json` (mode 0600). Reconfigure the bot with
`./manage.sh content-configure`; it preserves every other component, bind IP,
secret, and data file. To disable the bot, run `./manage.sh configure` and
answer No to "Keep the Content Bot enabled?"; all data is kept.

## Operator panel

The optional operator console shows the same status without the CLI and lets
you edit the policy files, tail logs, and run the whitelisted stack actions:

```bash
./manage.sh panel-enable     # optional "panel" profile; loopback only
./manage.sh panel-token      # operator token for http://127.0.0.1:8899/
```

It runs as its own `content-panel` container, keeps configuration backups under
`data/panel/backups/`, and never publishes n8n or any MCP endpoint. See
`docs/PANEL.md` for the security model, remote-access guidance, and settings.

## Image updates

The `content-bot` image is published to Docker Hub as
`afsharidevops/content-bot:0.2.0` (plus `:latest`) whenever Content Bot source
is pushed to the `main` branch of this repository. The server never builds the
image locally; `install.sh` and `./manage.sh start` pull the published image.

To update a running stack to the newest published build:

1. Set `CONTENT_BOT_IMAGE_TAG=latest` in `.env` (or bump it to a pinned
   version), then run `./manage.sh start`.
2. Confirm the new container is healthy with `./manage.sh logs content`.

See `docs/publishing/CONTENT-BOT-DOCKERHUB.md` for the publish workflow,
required repository settings, and version bumps.

### Local test builds

While developing, build the image locally and tag it with a `-local` suffix,
for example `CONTENT_BOT_IMAGE_TAG=0.2.0-local`. Compose reuses that local image
on `docker compose up -d content-bot` (or `./manage.sh start`); never run
`docker compose pull` on a `-local` tag, because it is not published to any
registry and the pull fails with `not found`. To rebuild and switch over:

```bash
docker build -f content-bot/Dockerfile -t afsharidevops/content-bot:0.2.0-local .
docker compose up -d --pull never content-bot
```

The same naming works for `afsharidevops/media-studio:0.2.0-local` with
`-f media-studio/Dockerfile`.

The `media-studio` image is published the same way as
`afsharidevops/media-studio:0.2.0`; see
`docs/publishing/MEDIA-STUDIO-DOCKERHUB.md`.

## Troubleshooting

- **Bot does not answer**: confirm the token in `.env`
  (`CONTENT_BOT_TOKEN`) and your numeric ID in `CONTENT_TELEGRAM_USERS`; check
  `./manage.sh logs content`.
- **Approve fails**: the bot must be an administrator of the channel, and the
  channel must be set in `CONTENT_TELEGRAM_CHANNEL`.
- **Daily run skips**: no sources configured, or every item was rejected; the
  bot reports the rejected/duplicate counts in Telegram.
- **Writer errors**: confirm `CONTENT_WRITER_BASE_URL` is reachable from the
  content-bot container (the Smart Router service name is `smart-router`) and
  that the model/alias exists in the configured backend (a 9router `combo-*`
  or an OmniRoute `auto/best-*` alias).
- **Garbled Persian draft**: some writer endpoints occasionally return Persian
  text decoded as a legacy charset (words that look like `Ø§Ú¯Ø±`). The bot
  detects and repairs that pattern, and refuses to publish copy it cannot
  repair; send the link or topic again in that case.
- **A paragraph looks scrambled in Telegram**: the bot already forces every
  mixed Persian/Latin line to render right-to-left, but a paragraph whose
  first visible word is English can still read awkwardly. Reply to the draft
  with "start each paragraph with a Persian word" and press **Reject** to get
  a corrected revision.
