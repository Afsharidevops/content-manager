# Content Manager v0.5.0

Eighth release of the Content Manager fork on the Hermes Linux Stack v0.6.4
platform. This release completes the video production platform (Smart Router
0.6.4, operator panel 0.6.0, Media Studio 0.5.1) and adds several
reliability and operations improvements across all services.

## Highlights

### Video Production Platform

- **Operations Center → Video Studio** - the panel gains a full video
  production workflow: topic/script input, storyboard generation via the
  Storyboard Agent, timeline validation against Media Studio's normalizer,
  approval/rejection gate, and one-click render dispatch to the
  `timeline-video` driver. Drafts are stored in `data/panel/storyboards/`
  as self-contained JSON files (brief, storyboard, render timeline,
  approval trail, and last job) written atomically at mode `600`.
- **Production Jobs API** (`POST /api/production/jobs`,
  `/{id}/storyboard`, `/{id}/timeline`, `/{id}/render`,
  `/{id}/render` GET for polling) - the Smart Router Operations Center
  exposes a structured lifecycle: draft → storyboard_created →
  timeline_ready → rendering → done/failed. Each step calls the
  appropriate content agent (`Script Agent`, `Storyboard Agent`, or
  `Video Director Agent`) and stores partial progress so a restart
  recovers without re-billing.
- **Storyboard API** (`GET /api/video/storyboards`, `POST`, `PUT`,
  `DELETE`, `/{id}/approve`, `/{id}/reject`, `/{id}/render`,
  `/{id}/scenes/{n}/regenerate`) - full lifecycle over HTTP from
  the panel frontend, all guarded by session auth and CSRF.
- **Content Agents** seeded at startup: `Script Agent`, `Storyboard Agent`,
  `Video Director Agent`, `Media Planner`, `Recovery Agent`. Names are
  skipped if they already exist, so operator edits are preserved.

### NotebookLM Worker Reliability

- **Recovery loop** - a configurable retry/recovery loop wraps every
  Playwright automation step. `Recovery Agent` (LLM) receives the page
  state, selects one of `retry | dismiss_overlay | wait | switch_flow |
  abort`, and the runner adapts before the next attempt.
- **Process fallback** - when the main automation path fails after all
  recovery attempts the worker falls back to an alternative browser flow
  before aborting the job.
- **`NOTEBOOKLM_MAX_RECOVERY_ATTEMPTS`** - new config key (default 3).

### Knowledge Sources Ingestion

- **`panel/ingest_utils.py`** - shared helpers for URL fetch, PDF
  extraction, and RSS/Atom parsing. The panel exposes
  `POST /knowledge/documents` which accepts `{"url": "..."}`,
  `{"pdf": "<base64>"}`, `{"rss": "..."}`, or `{"content": "..."}` and
  proxies a cleaned chunk to the Smart Router RAG knowledge base.
- **CSRF order fix** (`panel/server.py`) - `POST /knowledge/documents`
  now checks CSRF before reading the request body, matching every other
  mutating endpoint (was returning 500 instead of 403 on a missing token).

### Smart Router 0.6.4 Hardening

- **Context gate disabled at router level** - `SMART_ROUTER_STRONG_MAX_CONTEXT`
  raised to `10 000 000` and `SMART_ROUTER_CONTEXT_TOKEN_SAFETY_FACTOR` set
  to `1.0`. The Smart Router no longer pre-rejects requests whose estimated
  token count approached the old hard cap of 200 000; the upstream provider
  (`locallab/ai-strong`) enforces its own limit and returns a clear error if
  actually over budget. This eliminates the spurious `422 no configured tier
  can satisfy request capabilities and context` errors seen during Codex
  sessions with large context.
- **Combo-vision provisioning** - the `combo-vision` route profile is seeded
  at startup alongside `combo-strong` and `combo-standard`.

### Operator Panel 0.6.0

- Video Studio tab (storyboard editor, scene list, approve/reject, render
  and job progress).
- Knowledge ingest form (URL, RSS, PDF).
- Hermes overview, orchestration runs, and knowledge proxy endpoints.

## Artifacts

- Smart Router image: `afsharidevops/hermes-smart-router:0.6.4`
- Operator panel image: `afsharidevops/content-panel:0.6.0`
- Media Studio image: `afsharidevops/media-studio:0.5.1`
- Content Bot image: `afsharidevops/content-bot:0.4.3`
- NotebookLM Worker image: `afsharidevops/notebooklm-worker:0.1.0`

## Changed files

| File | Change |
|---|---|
| `smart-router/src/smart_router/content_agents.py` | Added Script Agent definition and `ensure_content_agents` seeding |
| `smart-router/src/smart_router/main.py` | Added `/v1/content/*` endpoints (video-plan, scene, production jobs API) |
| `smart-router/tests/test_api.py` | Tests for production job lifecycle, storyboard agent calls, scene regeneration |
| `notebooklm-worker/app/recovery.py` | Recovery loop, action dispatcher, LLM-based page-state diagnosis |
| `notebooklm-worker/app/runner.py` | Integrated recovery loop into step execution, process-fallback path |
| `notebooklm-worker/app/config.py` | `NOTEBOOKLM_MAX_RECOVERY_ATTEMPTS` |
| `panel/ingest_utils.py` | URL fetch, PDF extract, RSS parse helpers |
| `panel/server.py` | Knowledge ingest/search endpoints; CSRF-before-body fix on `POST /knowledge/documents`; video studio storyboard routes |
| `panel/storyboards.py` | `StoryboardStore` (atomic write, draft lifecycle, approval/rejection, render job tracking) |
| `.env.example` | `SMART_ROUTER_STRONG_MAX_CONTEXT=10000000`, `SMART_ROUTER_CONTEXT_TOKEN_SAFETY_FACTOR=1.0` |
| `docs/SMART-ROUTER-USER-GUIDE.md` | Updated default for `SMART_ROUTER_CONTEXT_TOKEN_SAFETY_FACTOR` |

## Upgrade notes

- `docker compose pull smart-router && docker compose up -d smart-router` picks
  up the new context-gate defaults automatically (the `.env` on the server
  was already updated in-place and the container is running).
- No database migration is needed; the production jobs table
  (`v64_production_jobs`) was created by the 0.6.4 Smart Router migration.
- Panel storyboard drafts land in `data/panel/storyboards/`; that directory
  is created automatically on first use and should be included in backups.

## Validation

All test suites passed against this commit:

| Suite | Tests | Result |
|---|---|---|
| smart-router | 213 | ✅ |
| media-studio | 124 | ✅ |
| content-bot | 416 | ✅ |
| content pipeline | 46 | ✅ |
| notebooklm-worker | 51 | ✅ |
| repo layout / stack | 50 | ✅ |
| **Total** | **900** | **✅** |
