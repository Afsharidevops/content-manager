# Content production architecture

This document describes the end-to-end content flow: how an operator request
becomes a reviewed post with optional media, how it is published, and where
the design is heading. It is implementation guidance, not a promise that every
listed phase already exists in code.

## Flow

```text
Input (link | topic | pasted text)
   |
   +-- link: fetch + extract
   |        +-- little text -> web search for more context (original link stays source)
   +-- topic/text: web search for the best sources
   |
   v
Research step (short-lived role: gather 3-6 sources, never invent facts)
   |
   v
Writer step (short-lived role: Persian title+body, channel voice)
   |
   v
Media decision (operator answers on Telegram)
   |-- Text only
   |-- AI image      -> Media Studio api-image/gemini-image job (brand chip added)
   |-- Upload image  -> Media Studio /brand chip added to the operator photo
   +-- Video prompt  -> operator creates the clip and uploads it
   |
   v
Media Studio worker (queued jobs, artifact download, brand chip overlay)
   |
   v
Preview (text draft + media preview) with Approve / Reject / feedback reply
   |
   v
Publisher step (per-platform adapter; Telegram live, others planned)
```

## Roles as short-lived agents

Instead of one monolithic model call, every task runs as a sequence of small
roles, each with its own prompt context, inputs, and audit trail:

- `research` - turns a link or topic into a bounded source snapshot. Today it
  is the fetch/extract + search step; later it can call MCP/OpenAPI tools.
- `writer` - turns the source snapshot into the Persian post (title+body)
  following the channel voice and owner lessons.
- `media-planner` - asks the operator whether media is needed and what kind.
- `media-worker` - Media Studio job for image or video generation plus the
  configured corner brand chip on every raster output.
- `publisher` - platform-specific publishing adapter (Telegram live).

Roles map to Rakazo-style delegation without adding sandboxes or a second
platform: each role is a bounded step with logs and artifacts, approvals
happen at decision boundaries, and the operator stays in control.

## Memory

- Published history (dedupe) and category streaks already live in state.
- Owner feedback lessons: every edit-note reply is stored (deduplicated,
  bounded to 30) and the recent six are injected into future writer prompts as
  guidance. This is the beginning of a reusable brand book; voice/topic
  profiles will be added in a later phase.

## Audit trail

Every draft keeps a `history` list of events (`draft_sent`, `feedback_saved`,
`media_job_started`, `media_ready`, `media_failed`, ...) with timestamps, plus
the media job id, artifact name, and local file path. Job logs and failure
snapshots stay in Media Studio (`data/media-studio/`). This satisfies the
"full audit log" need without a database.

## Media Studio contract

- `POST /jobs` with `{"driver", "prompt", "params"}` returns a job id.
- `GET /jobs/<id>` returns status (`queued`/`running`/`done`/`failed`),
  artifacts (`name` + `kind` image/video/file), and the error detail.
- `GET /artifacts/<id>/<name>` downloads the artifact bytes.
- Content Bot submits, polls in the background (single-threaded bot stays
  responsive), downloads, stores under `data/content-bot/media/`, and
  previews for approval.

## Current implementation (Phase 1)

- Link drafting with a search fallback for short pages.
- Topic drafting: plain messages are searched (DuckDuckGo HTML, no key) and
  drafted. Toggles: `CONTENT_SEARCH_ENABLED`, `CONTENT_TOPIC_DRAFTS_ENABLED`.
- After an on-demand draft, the bot asks a four-choice media question: text
  only, AI image, send your own image, or a video prompt the operator uses to
  create the clip and upload it back.
- Media jobs run through `CONTENT_MEDIA_STUDIO_URL` (Media Studio), with
  driver selection via `CONTENT_MEDIA_IMAGE_DRIVER` and
  `CONTENT_MEDIA_VIDEO_DRIVER` (Flow stays experimental).
- Operator uploads are stored locally; photos are sent through the Media
  Studio `/brand` endpoint so they carry the same corner chip as AI images.
- Media preview buttons match the source: AI previews offer New attempt /
  Text only, upload previews offer Approve / Reject / Text only; publishing
  sends one media message with a bold-title caption and continuation
  messages when the body exceeds the 1024-character caption limit.
- Owner feedback is learned into writer guidance; body copy renders with
  RTL-safe lines and writer replies with legacy-charset mojibake are repaired
  or refused.
- Telegram API client supports multipart photo/video uploads and raw binary
  requests.

### Limits in Phase 1

- Google Flow returns one clip of roughly 8-10 seconds per job and needs
  interactive confirmations plus a signed-in session; true multi-scene
  composition is Phase 3, and the supported video path is the operator-created
  prompt + upload flow.
- The media question is only asked for operator-driven (`on_demand`) drafts,
  not for scheduled daily proposals.
- Publish adapters beyond Telegram are not implemented yet.

## Roadmap

Phase 2 - tool registry and platform publishers:

- Remote MCP/OpenAPI tool sources registered in one place so the bot, n8n,
  and the router can call the same tools (search, media, publish).
- Publisher agents per platform (Instagram, YouTube, Aparat) with
  per-platform copy profiles, aspect ratios, and direct manual-action links
  where an API needs a human step.
- A platform selection question before publishing, and a combined approval
  summary for multi-platform posts.

Phase 3 - routines and longer video:

- Scheduled multi-step routines (research -> draft -> media -> queue) with
  per-platform cadence.
- Multi-scene Flow videos up to ~30 seconds composed from several clips.
- Optional n8n connector that drives the same tool registry; Telegram remains
  the operator front-end.

Out of scope by design: a second full agent platform. If tasks ever need a
real desktop/browser sandbox, Rakazo itself can run beside this stack as an
optional peer service instead of being reimplemented here.

## Configuration

```text
CONTENT_MEDIA_STUDIO_URL            Media Studio API base URL (blank disables)
CONTENT_MEDIA_STUDIO_TOKEN          Bearer token for Media Studio
CONTENT_MEDIA_IMAGE_DRIVER          image driver (default api-image)
CONTENT_MEDIA_VIDEO_DRIVER          video driver (default flow-video)
CONTENT_MEDIA_JOB_TIMEOUT_SECONDS   max wait for one media job (default 1200)
MEDIA_STUDIO_BRAND_LABEL           brand chip text, blank disables (default Locallab)
MEDIA_STUDIO_BRAND_POSITION        chip corner: bottom-right/left, top-right/left
CONTENT_SEARCH_ENABLED              topic/short-page search (default true)
CONTENT_TOPIC_DRAFTS_ENABLED        draft from topic messages (default true)
CONTENT_SEARCH_MAX_RESULTS          search results used (default 5)
CONTENT_SEARCH_TIMEOUT              search timeout seconds (default 25)
CONTENT_VIDEO_CHARACTER            saved Flow character handle (blank disables)
CONTENT_VIDEO_CHARACTER_PROMPT     character block override, keeps {handle}/{aspect}
CONTENT_VIDEO_ASPECT               reel frame for prompt packages (default 9:16 vertical)
CONTENT_VIDEO_SEGMENT_SECONDS      seconds Flow generates per segment (default 10)
```
