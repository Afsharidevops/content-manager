# Content Manager

Content Manager is a self-hosted daily content-production stack. It discovers
candidate stories from RSS/Atom feeds and operator-shared links, filters and
scores them against an owner-editable editorial policy, drafts posts through a
configurable writer, and publishes them only after a human approves the post in
Telegram.

The runtime is built on the [Hermes Linux Stack](https://github.com/Afsharidevops/hermes-linux-stack)
v0.5.9 platform (9router or OmniRoute backend, Hermes Smart Router, Hermes
Agent/Telegram, Open WebUI, optional n8n) and extends it with a deterministic content layer under
`content/` and the `content-bot` Telegram editorial bot. Persian copy is
produced at runtime by the writer; everything stored in this repository is
English-only.

```text
Branch: main   Platform: Hermes Linux Stack v0.5.9   Content Bot: 0.2.0   Media Studio: 0.2.0
```

## What it does

- **On-demand drafts** - send any `http(s)` link to the Content Bot. It fetches
  the page, filters it against the editorial policy, drafts a post, and shows
  **Approve/Reject** buttons. A link with almost no readable text is enriched
  with a web search, and a plain topic message (no link) is searched and
  drafted the same way.
- **Daily proposals** - the bot reads `sources.yaml`, proposes the best scored
  candidates on a schedule, and sends them to the operator with the same
  approval buttons.
- **Approval-gated publishing** - only approved drafts are published to the
  Telegram channel. The scheduled-proposal daily cap, duplicate protection,
  and same-category streak limits are enforced by policy; operator-sent links
  are never blocked by the daily cap.
- **Comment-driven revision** - reply to a proposal with edit notes and press
  Reject; the bot revises the draft in place and lets you iterate until it is
  right. Reject without notes discards the draft.
- **Platform adapters** - the pipeline is platform-agnostic; publishing
  currently supports Telegram.
- **Media attach (optional)** - after a draft the bot asks whether the post
  needs an image or a video, submits the job to Media Studio, shows the media
  preview for approval, and publishes the post plus media to the channel.
- **Media generation (optional)** - Media Studio turns prompts into images or
  video through an OpenAI-compatible API (`api-image`, no Google account) and,
  optionally, through a signed-in Google Flow/Gemini session. See
  `docs/MEDIA-STUDIO.md`. The Flow region unlock is also available standalone
  for laptop use: `docs/FLOW-UNLOCK-STANDALONE.md` and
  `extensions/locallab-flow-unlock/`.

| Platform | State |
| --- | --- |
| Telegram | Live: link/topic drafts, daily proposals, image/video attach, Approve/Reject, channel publish |
| Instagram | Official Meta Graph API: photos, carousels, and video via approve buttons; see `docs/INSTAGRAM-SETUP.md` |
| Aparat / YouTube | Planned: not implemented yet |
| Media assets | Optional: `media-studio` worker (API images now; Google Flow/Gemini via browser session) |

## Architecture

### Content production flow

```text
Operator (Telegram)               Scheduler (once per local day)
  sends a link                      reads RSS/Atom feeds
        │                                  │
        ▼                                  ▼
              content-bot container
        ┌───────────────────────────────────────────┐
        │ fetch → extract → normalize               │
        │ policy gate → dedupe → score → select     │
        └───────────────────────────────────────────┘
                          │  candidate item
                          ▼
        Writer (OpenAI-compatible: Smart Router / selected router)
                          │  Persian draft copy
                          ▼
        Draft preview + [Approve] [Reject]   ──►  operator's Telegram
                          │ approve                    │ reject
                          ▼                            ▼
              Telegram channel publish            draft discarded
```

The pipeline has four cooperating parts:

1. **Content layer** (`content/`, package `content_pipeline`) - deterministic
   normalization, editorial-policy evaluation, deduplication, weighted
   scoring, freshness mapping, and candidate selection. Configuration lives in
   `content/config/`; see [content/README.md](content/README.md).
2. **Editorial working copy** (`data/content-manager/config/`) - a gitignored,
   seeded copy of the policy (`editorial-policy.yaml`), category rules
   (`categories.yaml`), and discovery sources (`sources.yaml`). Install runs
   seed it once; operator edits survive reinstalls.
3. **Content Bot** (`content-bot/`) - a small unprivileged Telegram
   long-polling service that fetches links/feeds, runs the content layer,
   requests draft copy from the writer, and publishes approved posts. Runtime
   state (pending drafts, published hashes, daily counters) lives in
   `data/content-bot/state.json`.
4. **Writer backend** - the OpenAI-compatible endpoint of the selected router
   backend (9router or OmniRoute, optionally behind the Smart Router),
   configured through `CONTENT_WRITER_*`. Content Bot never publishes without
   an operator pressing Approve.

### Platform underneath

```text
Hermes Agent / Open WebUI / n8n ─┐
Content Bot (writer calls) ──────┼──► Smart Router v0.5.9 ──► 9router/OmniRoute ──► Providers
Telegram polling (Hermes + bot) ─┘
```

All services are provisioned by `./install.sh`, run through Docker Compose
profiles, and stay on private Docker networks with loopback-only host binds by
default. The Content Bot needs only outbound HTTPS to the Telegram Bot API and
to the writer endpoint - no public ingress.

## Quick start

Requirements:

- Linux, Bash, Docker Engine with the Compose plugin, Git
- Outbound HTTPS to Docker Hub and the Telegram Bot API
- A Telegram bot token (create one with `@BotFather`) and your numeric Telegram
  user ID
- A Telegram channel (the bot must be added as an administrator to publish)
- At least one AI provider configured in the selected router backend
  (9router or OmniRoute)

Install:

```bash
git clone https://github.com/Afsharidevops/content-manager.git
cd content-manager
chmod +x install.sh manage.sh
./install.sh
```

The installer asks which router backend to use - 9router or OmniRoute - and
which components to enable, prompts for Content Bot settings (bot token,
operator IDs, channel, writer model), writes secrets to `.env` (mode 0600),
seeds the policy working copy, pulls the published images, and starts the
stack. OmniRoute installs expose its dashboard on 20128 and its OpenAI-
compatible API on 20129; the installer points Hermes, Open WebUI, n8n, the
Content Bot, and the Smart Router upstream at the selected backend. Preview or
split configuration from startup with `./install.sh --dry-run` and
`./install.sh --no-start`.

For a standalone content production server, option 7 installs the Content
pipeline (Content Bot + Media Studio with an external model API); option 5
installs Content Bot only and asks whether Media Studio should be added, and
option 6 installs Media Studio only. A combined install wires the bot to the
local Media Studio API automatically (URL, shared token, matching drivers).

Then:

1. Open the Content Bot in Telegram and send `/start`.
2. Send any link - the bot replies with a draft and Approve/Reject buttons.
3. Approve to publish to the channel, or reply to the draft with edit notes
   and press Reject to request a revised version.

## Configuration

All runtime configuration is environment-based (`.env`) plus the editorial
working copy under `data/content-manager/config/`. Key settings:

| Setting | Purpose |
| --- | --- |
| `COMPOSE_PROFILES` | Which services run (e.g. `content`) |
| `CONTENT_BOT_TOKEN` | Content Bot Telegram token |
| `CONTENT_TELEGRAM_USERS` | Numeric operator IDs allowed to approve |
| `CONTENT_TELEGRAM_CHANNEL` | Publish target (`@username` or numeric ID) |
| `CONTENT_WRITER_BASE_URL` | OpenAI-compatible writer endpoint |
| `CONTENT_WRITER_API_KEY` | Writer key (Smart Router client key when enabled) |
| `CONTENT_WRITER_MODEL` | Writer model/alias |
| `CONTENT_SCHEDULER_ENABLED` | Enable the daily scheduler (`true`) |
| `CONTENT_MEDIA_STUDIO_URL` | Media Studio job API (blank disables media asks) |
| `CONTENT_MEDIA_STUDIO_TOKEN` | Bearer token for the Media Studio API |
| `CONTENT_SEARCH_ENABLED` | Web search for topics and short pages (`true`) |
| `CONTENT_TOPIC_DRAFTS_ENABLED` | Draft from plain topic messages (`true`) |
| `CONTENT_BOT_IMAGE_REPOSITORY` / `CONTENT_BOT_IMAGE_TAG` | Published image to pull |

Editorial behavior comes from the policy files:

```yaml
# data/content-manager/config/editorial-policy.yaml
pipeline:
  timezone: "Asia/Tehran"
  daily_proposal_time: "08:00"
  max_approved_per_day: 3
  max_consecutive_same_category: 3

# Links the operator sends directly to the bot chat.
on_demand:
  enforce_freshness: false   # true also applies freshness_hours to these links
  unlimited_approvals: true  # false makes them count toward max_approved_per_day
```

```yaml
# data/content-manager/config/sources.yaml
sources:
  - name: "Example Tech Feed"
    url: "https://example.com/feed.xml"
```

Change values in the working copy only - the bot reloads policy on every
request/callback, so policy edits apply without a code change or restart. The
daily proposal runs once per local day at `daily_proposal_time`;
`CONTENT_SCHEDULER_ENABLED=false` disables it.

## Management

```bash
./manage.sh menu                    # interactive manager
./manage.sh status                  # container status
./manage.sh logs content            # follow Content Bot logs
./manage.sh content-status          # Content Bot summary (no secrets)
./manage.sh pipeline-status         # Content Bot + Media Studio + API link summary
./manage.sh content-connect-instagram
./manage.sh content-configure       # reconfigure Content Bot only (writer API, model, Telegram)
./manage.sh configure               # re-run the installer wizard
./manage.sh start | stop | restart | update
./manage.sh doctor                  # diagnostics and hardening checks
./manage.sh uninstall [--purge]     # remove stack; --purge also removes data
```

Interactive groups cover services, router, Hermes, n8n, Content Bot, execution,
maintenance, and security. Content Bot data is preserved when the bot is
disabled or the stack is uninstalled without `--purge`.

The full operator guide for the Content Bot is
[docs/CONTENT-PRODUCTION-GUIDE.md](docs/CONTENT-PRODUCTION-GUIDE.md).

## Image publishing

The `content-bot` image is built on GitHub Actions and published to Docker Hub
whenever Content Bot source is pushed to `main`:

```text
afsharidevops/content-bot:0.2.0
afsharidevops/content-bot:latest
```

Servers pull it; they never build it. To move a server to the newest build, set
`CONTENT_BOT_IMAGE_TAG=latest` in `.env` and run `./manage.sh start`. See
[docs/publishing/CONTENT-BOT-DOCKERHUB.md](docs/publishing/CONTENT-BOT-DOCKERHUB.md).

## Platform components (inherited, summarized)

| Component | Role | Default bind |
| --- | --- | --- |
| 9router | Provider/model gateway with API keys (profile `9router`) | `127.0.0.1:20128` |
| OmniRoute | Dashboard + OpenAI-compatible API (profile `omniroute`) | `127.0.0.1:20128` / `20129` |
| Hermes Smart Router | Capability routing, aliases, dashboard | `127.0.0.1:8787` |
| Hermes Agent | Telegram/agent runtime, dashboard/API | `127.0.0.1:9119` / `8642` |
| Open WebUI | Chat UI | `127.0.0.1:3000` |
| n8n (optional) | Workflow automation + MCP | `127.0.0.1:5678` |
| Caddy (optional) | Public HTTPS domains | 80/443 |
| Content Bot | Telegram editorial bot (polling, no ingress) | none |

Hermes polls the Telegram Bot API; it does not expose an inbound Telegram port.
Runtime data lives under `data/` (`data/9router`, `data/omniroute`,
`data/hermes`, `data/smart-router`, `data/open-webui`, `data/n8n`,
`data/content-bot`, `data/content-manager`, `data/stack-secrets`). Secrets
stay in `.env` and
`data/stack-secrets/`; never commit runtime secrets or databases.

Application images intentionally default to mutable tags so `docker compose
pull` tracks upstream releases; pin any service with its `*_IMAGE_TAG` in
`.env` after testing. `./manage.sh lock-images` and `./manage.sh verify-images`
pin and verify digests.

Smart Router published image: `afsharidevops/hermes-smart-router:latest`
(platform `linux/amd64`, `linux/arm64`). Upstream platform behavior - routing
modes, Operations Center, execution approvals, n8n provisioning, RAG storage -
is documented in [docs/HERMES-OPERATIONS-CENTER-USER-GUIDE-v0.5.9.md](docs/HERMES-OPERATIONS-CENTER-USER-GUIDE-v0.5.9.md)
and [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Router backend policy

- `main` supports both router backends through Compose profiles: `9router`
  (single-port gateway on 20128 with automatic key provisioning) and
  `omniroute` (dashboard on 20128, OpenAI-compatible API on 20129).
- `./install.sh` chooses one backend on fresh installs and can switch an
  existing install between them; `COMPOSE_PROFILES` records the selection and
  `install.sh` points Hermes, Open WebUI, n8n, and the Smart Router upstream
  at the active backend. The Smart Router, Hermes, n8n, and Content Bot
  integrations behave identically for both backends.
- The legacy `hermes-omniroute-linux-stack` branch is obsolete; its OmniRoute
  configuration now lives in `main` behind the `omniroute` profile.

## Validation

CI runs shell syntax checks, Python compilation scans, content-layer tests,
Content Bot tests, Docker builds, and Compose boundary checks on every push.
Run the same checks locally:

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e content[dev]
PYTHONPATH=$PWD/content:$PWD/content-bot ./.venv/bin/pytest -q content/tests
PYTHONPATH=$PWD/content:$PWD/content-bot ./.venv/bin/python -m unittest discover -s content-bot/tests
bash tests/test-manage-ux.sh
```

## Documentation

- [Content layer](content/README.md) - package layout, policy schema, scoring
- [Content production guide](docs/CONTENT-PRODUCTION-GUIDE.md) - bot flows and operations
- [Instagram/Meta setup](docs/INSTAGRAM-SETUP.md) - official Graph API publishing
- [Content Bot Docker Hub](docs/publishing/CONTENT-BOT-DOCKERHUB.md) - image publishing
- [Operations Center user guide](docs/HERMES-OPERATIONS-CENTER-USER-GUIDE-v0.5.9.md)
- [Operations](docs/OPERATIONS.md) and [release process](docs/RELEASE-PROCESS.md)
- [Changelog](CHANGELOG.md)

## License

See `LICENSE`. Third-party images and upstream projects retain their
respective licenses.
