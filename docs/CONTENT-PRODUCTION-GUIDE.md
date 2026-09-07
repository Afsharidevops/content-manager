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

When you select the Content Bot during `./install.sh`, the wizard asks for:

1. A **bot token** created with `@BotFather` (the Content Bot uses its own bot,
   separate from the Hermes agent bot, because two pollers cannot share one
   token).
2. The **channel ID or @username** where approved posts are published. The bot
   must be added to that channel as an administrator.
3. The **numeric operator Telegram IDs** allowed to approve and reject drafts
   (defaults to the Hermes Telegram allowlist when Hermes is enabled).
4. The **writer endpoint, API key, and model**. With the Smart Router enabled,
   the installer defaults to `http://smart-router:8080/v1` with model `auto`
   and stores the trusted router client key automatically.

The token and key are stored in `.env` with mode `0600` and are never printed.

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

Commands in the bot chat: `/start`, `/help`, `/status`.

## Policy-driven behavior (no code changes)

All tunable behavior lives in `data/content-manager/config/editorial-policy.yaml`
and is reloaded on every request and callback:

- `on_demand.enforce_freshness` - set `true` to apply `freshness_hours` to
  links you send the bot yourself (default `false`).
- `on_demand.unlimited_approvals` - set `false` to make your own link
  approvals count against `max_approved_per_day` (default `true`).
- `pipeline.max_approved_per_day` - daily proposal cap.
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

## Platform status

| Platform | State |
| --- | --- |
| Telegram | Live: on-demand drafts and scheduled proposals with Approve/Reject |
| Instagram | Pending: adapter designed in, manual Meta setup blocked for now; see `docs/INSTAGRAM-SETUP.md` |

## Operations

```bash
./manage.sh content-status              # configuration summary, no secrets
./manage.sh content-connect-instagram   # Instagram/Meta pending checklist
./manage.sh logs content                # follow Content Bot logs
./manage.sh configure                   # change Content Bot settings later
```

State (pending drafts, published hashes, daily counters) lives in
`data/content-bot/state.json` (mode 0600). Reconfigure the bot with
`./manage.sh configure`; disabling it keeps all data.

## Image updates

The `content-bot` image is published to Docker Hub as
`afsharidevops/content-bot:0.1.0` (plus `:latest`) whenever Content Bot source
is pushed to the `main` branch of this repository. The server never builds the
image locally; `install.sh` and `./manage.sh start` pull the published image.

To update a running stack to the newest published build:

1. Set `CONTENT_BOT_IMAGE_TAG=latest` in `.env` (or bump it to a pinned
   version), then run `./manage.sh start`.
2. Confirm the new container is healthy with `./manage.sh logs content`.

See `docs/publishing/CONTENT-BOT-DOCKERHUB.md` for the publish workflow,
required repository settings, and version bumps.

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
  that the model/combo exists in 9router.
