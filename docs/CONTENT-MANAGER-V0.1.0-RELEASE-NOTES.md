# Content Manager v0.1.0

First release of the Content Manager fork on the Hermes Linux Stack v0.5.9
platform. It captures the deterministic content layer, the Content Bot
Telegram editorial loop, and the install/manage flows that provision them.

## Highlights

- **Content Bot Telegram MVP** - send any link to the bot to get a policy
  filtered draft with Approve/Reject buttons; approved posts are published to
  the configured Telegram channel. Operator-sent links are never blocked by
  the freshness window or the daily proposal cap.
- **Comment-driven revision loop** - reply to a proposal with edit notes and
  press Reject to revise the draft in place and iterate. Reject without notes
  discards the draft; empty or unchanged writer revisions are surfaced instead
  of re-posting.
- **RTL-safe bold titles** - draft titles render bold with RTL-safe ordering,
  and daily feed parsing is hardened to HTML titles and links.
- **Seeded discovery sources** - `content/config/sources.yaml` ships with
  vetted RSS/Atom feeds per category and an in-file guide for adding and
  pre-testing new feeds.
- **Standalone deployment** - installer option 5 provisions only the Content
  Bot against an external OpenAI-compatible writer API (base URL, key, model).
  `./install.sh --content-reconfigure` and `./manage.sh content-configure`
  reconfigure it while preserving existing components and data.
- **Robust writer handling** - tolerates null or truncated writer responses
  and honors `CONTENT_WRITER_MODEL=auto`, `CONTENT_WRITER_MAX_TOKENS`, and
  `CONTENT_WRITER_REASONING_EFFORT`.
- **Operator commands** - `/forget-link <url>` (alias `/forget_link`) allows a
  previously published link to be drafted again; the Telegram command menu is
  registered on startup in the default and private-chat scopes.

## Artifacts

- Content Bot image: `afsharidevops/content-bot:0.1.0` (plus `:latest`),
  published to Docker Hub by the `publish-content-bot` workflow on `main`
  pushes.
- Platform: Hermes Linux Stack v0.5.9 (inherited unchanged upstream).

## Documentation

- `docs/CONTENT-PRODUCTION-GUIDE.md` - production, approval, and operator
  workflow guide.
- `docs/INSTAGRAM-SETUP.md` - Instagram/Meta setup checklist (guided pending;
  publishing currently supports Telegram).
- `docs/publishing/CONTENT-BOT-DOCKERHUB.md` - image publishing notes.
- `README.md` - stack overview and quickstart.

## Validation

- Content Bot and content layer unit tests pass (`39` tests).
- `install.sh` and `manage.sh` pass shell syntax checks.
- `validate` and `publish-content-bot` workflows passed on the tagged commit.
