# NotebookLM Studio

NotebookLM Studio is the video provider of the Content Manager pipeline that
produces a **Video Overview** with Google NotebookLM instead of a paid video
API. A subscription replaces the per-second billing of a generation provider,
and the result is a narrated overview built from the sources of a draft: its
page, its text, and any file or link the operator adds.

The provider is independent from Media Studio. Media Studio keeps generating
images and browser-driven clips; NotebookLM Studio only answers the
`NotebookLM video` button on a draft, so the two can run side by side, one of
them, or neither.

- Compose profile: `notebooklm`
- Container: `notebooklm-worker`
- Image: `afsharidevops/notebooklm-worker` (built on GitHub Actions)
- Data: `data/notebooklm-worker`
- Guide: this file

## 1. How it works

```
Telegram (operator)                 Content Bot                    NotebookLM Worker              Google
-------------------                 -----------                    ------------------             ------
"NotebookLM video" button    ->     POST /jobs              ->     queue one job
"Technical / Educational /          poll GET /jobs/<id>            create the notebook        ->  notebooklm.google.com
 News" profile                      edit the ask message           add the draft as a source
                                    per stage (⏳ 📚 📤 🎬 ⬇️)     add the page link as a source
                                                                   open Studio                ->  Video Overview
                                                                   generate, wait             ->  render
                                                                   download the mp4
video preview + Approve      <-     GET /artifacts/<id>/<name> <-  store videos/<id>.mp4
```

The worker owns a small JSON job store, a serial runner (one video at a
time), and a Playwright browser session. The bot never talks to Google; it
only submits a job, follows its stage, and downloads the finished file. That
keeps the Google session, the browser and its profile inside one container.

Job states (`GET /jobs/<id>` → `status`) and what the chat shows:

| Worker state | Meaning | Chat line |
| --- | --- | --- |
| `created` | queued, not started yet | `⏳ Preparing the sources...` |
| `uploading` | creating the notebook and adding sources | `📚 Creating the notebook...` / `📤 Uploading the sources...` |
| `processing` | waiting for NotebookLM to read the sources | `📤 Uploading the sources...` |
| `generating` | the Video Overview is being produced | `🎬 Generating the video...` |
| `downloading` | fetching the mp4 from the player | `⬇️ Downloading the output...` |
| `ready` | the video is stored and served | the preview is sent, `Approve` publishes |
| `failed` | the job stopped with an error | the error plus a retry button |
| `canceled` | cancelled before it started | nothing |

Every state change is validated against a fixed transition table, so a job can
never jump from `created` straight to `ready` or move backwards.

## 2. Requirements

- A Google account with NotebookLM access (a Pro subscription raises the
  limits; the free tier works for short videos).
- One browser session that is already signed in to `notebooklm.google.com`.
  The worker reuses that session and never stores a password.
- The `notebooklm` compose profile enabled, and about 1 GB of RAM plus disk
  space for the Chromium sandbox and the stored videos.

## 3. Session modes

Google blocks automated sign-in, so the worker reuses a session that a human
created. Two modes exist, the same two Media Studio uses for Google:

### cdp (default, recommended on servers)

`NOTEBOOKLM_SESSION_MODE=cdp` attaches Playwright to a Chromium that is
already running with the DevTools protocol open, and opens a new tab for each
job. The browser stays yours: the worker never closes it, never touches the
profile, and never sees a credential.

Start that Chromium on the Docker host:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-debug"
# or: chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-debug"
```

Sign in to `https://notebooklm.google.com` in that window, then verify:

```bash
./manage.sh notebooklm-login 15
```

The container reaches the host through `host.docker.internal` (compose adds
the host-gateway mapping), so the default
`NOTEBOOKLM_CDP_URL=http://host.docker.internal:9222` needs no change.

When Chrome runs on another machine, expose its debugging port and point the
worker at it:

```bash
# on the machine with the signed-in Chrome
google-chrome --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0
# in .env
NOTEBOOKLM_CDP_URL=http://192.168.1.20:9222
```

Keep that port on a trusted network only: an open DevTools port gives full
control over the browser profile.

### persistent (one profile inside the container)

`NOTEBOOKLM_SESSION_MODE=persistent` launches a container-local Chromium whose
profile lives in `NOTEBOOKLM_BROWSER_PROFILE` (default
`/data/notebooklm-browser-profile`, persisted in
`data/notebooklm-worker/notebooklm-browser-profile`). The sign-in is completed
once in that browser.

```bash
NOTEBOOKLM_SESSION_MODE=persistent
NOTEBOOKLM_HEADLESS=false        # a visible window is needed for the sign-in
./manage.sh notebooklm-login 600
```

A container without a display cannot show that window, so persistent mode is
for hosts that already run an X server, a VNC session, or a remote-desktop
container, or for a one-time profile that you copy into
`data/notebooklm-worker/notebooklm-browser-profile`. On a headless server use
cdp mode.

## 4. Enable, verify, disable

```bash
./manage.sh notebooklm-enable      # add the profile, store the shared token, start the worker
./manage.sh notebooklm-status      # profile, session mode, token state, container state
./manage.sh notebooklm-login 15    # open NotebookLM and report the sign-in state
./manage.sh notebooklm-guide       # pointer to this document
./manage.sh notebooklm-disable     # stop the worker and disable the profile
```

`notebooklm-enable` performs three steps:

1. adds `notebooklm` to `COMPOSE_PROFILES`;
2. generates `NOTEBOOKLM_API_TOKEN` when it is empty (the worker and the bot
   share that bearer token);
3. starts `notebooklm-worker` and prints the sign-in steps.

The bot only offers the button when `CONTENT_NOTEBOOKLM_URL` is set. Enable and
disable it without touching the worker:

```bash
CONTENT_NOTEBOOKLM_ENABLED=false   # hide the button everywhere
```

Restart the bot after changing it: `./manage.sh restart content-bot`.

The interactive manager exposes the same operations under
`14) NotebookLM video`.

## 5. Using it from Telegram

1. Send a link or a topic to the bot; it answers with the draft preview.
2. Answer the media question with **NotebookLM video**.
3. Pick a profile:
   - **Technical for developers** (`technical_fa`)
   - **Educational for everyone** (`educational_fa`)
   - **Short news overview** (`news_fa`)
4. The ask message turns into the progress line and updates at every stage.
5. When the video is ready the bot sends it as a preview with the usual
   `Approve` / `Edit` / `Reject` buttons.
6. Approving publishes the post with that video; `Reject` discards it.

Sources the bot sends for a draft:

| Source | Kind | Notes |
| --- | --- | --- |
| The original link of the draft | `auto` (becomes `url` or `youtube`) | NotebookLM reads the page itself |
| The draft title and body | `text` | capped at 20 000 characters, added as `Draft text` |

The rendered prompt (English template, Persian output) always includes the
topic, the audience, the target length, the narrator voice, the style, the
tone, the content rules, and the three-part structure (`opening`, `main
explanation`, `closing`), plus an accuracy rule that forbids information
without a source. `Sources:` lists what the job adds.

### Profiles

| Profile | Language | Length | Voice | Style | Audience |
| --- | --- | --- | --- | --- | --- |
| `technical_fa` | Persian | 5–8 min | male | technical | developers, DevOps engineers |
| `educational_fa` | Persian | 8–10 min | female | educational | general users |
| `news_fa` | Persian | 3–5 min | neutral | documentary | general audience |

### Length buckets

A job may override the profile length with a bucket instead of the profile
default:

| Bucket | Target length |
| --- | --- |
| `short` | 2–3 minutes |
| `standard` | 5–8 minutes |
| `deep` | 10–15 minutes |

The Telegram flow keeps the profile default; the bucket is part of the API, so
scripts and the panel can pick a different length:

```bash
curl -sS http://127.0.0.1:8860/jobs \
  -H "Authorization: Bearer $NOTEBOOKLM_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"topic":"Container image layers","profile":"technical_fa","duration_profile":"deep"}'
```

## 6. API

Every endpoint except `/healthz` and `/session/info` needs
`Authorization: Bearer $NOTEBOOKLM_API_TOKEN` once a token is stored; with an
empty token the worker refuses the request instead of running unauthenticated.
JSON bodies are capped at 256 KiB, uploads at 64 MiB.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | liveness, queue and running counts |
| GET | `/session/info` | session mode, profile path, CDP URL (never a secret) |
| POST | `/session/probe` | open NotebookLM, save a screenshot and an element dump, report `signed_in` |
| GET | `/profiles` | the three profiles and the duration buckets |
| POST | `/jobs` | queue a job (`topic`, `sources`, `profile`, `duration_profile`, `content_id`) |
| GET | `/jobs` | recent jobs, newest first |
| GET | `/jobs/<id>` | one job plus `log_tail` |
| DELETE | `/jobs/<id>` | cancel a job that has not started |
| POST | `/uploads` | store one source file (`X-Filename` header) and return its id |
| GET | `/artifacts/<id>/<name>` | the finished mp4 of that job |

Examples:

```bash
# queue a job with a link and pasted text
curl -sS http://127.0.0.1:8860/jobs \
  -H "Authorization: Bearer $NOTEBOOKLM_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "topic": "Kernel updates in September 2026",
        "profile": "news_fa",
        "content_id": "draft-42",
        "sources": [
          {"kind": "auto", "value": "https://lwn.net/Articles/1094211/"},
          {"kind": "text", "value": "Short summary of the kernel update.", "title": "Notes"}
        ]
      }'

# follow it
curl -sS http://127.0.0.1:8860/jobs/<id> -H "Authorization: Bearer $NOTEBOOKLM_API_TOKEN"

# upload a PDF and use it as a source
upload_id="$(curl -sS -X POST http://127.0.0.1:8860/uploads \
  -H "Authorization: Bearer $NOTEBOOKLM_API_TOKEN" \
  -H 'X-Filename: report.pdf' --data-binary @report.pdf | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')"
# then add {"kind":"file","value":"<upload_id>"} to the sources

# download the result
curl -sS -o video.mp4 http://127.0.0.1:8860/artifacts/<id>/<id>.mp4 \
  -H "Authorization: Bearer $NOTEBOOKLM_API_TOKEN"
```

Source kinds:

| Kind | Value | Behaviour |
| --- | --- | --- |
| `auto` | any value | classified as `youtube`, `url`, or `text` |
| `url` | a website URL | added through the Website dialog |
| `youtube` | a YouTube URL | added through the YouTube dialog |
| `text` | pasted text | added through the Copied text dialog |
| `file` | an upload id | added through the Upload files dialog (PDF, TXT, MD, DOC, DOCX, CSV) |

Unknown ids, empty values, and path traversal attempts are dropped before the
browser starts, so a malformed request cannot reach Google.

## 7. Configuration

| Key | Default | Meaning |
| --- | --- | --- |
| `NOTEBOOKLM_WORKER_IMAGE_REPOSITORY` | `afsharidevops/notebooklm-worker` | image repository |
| `NOTEBOOKLM_WORKER_IMAGE_TAG` | `0.1.0` | image tag |
| `NOTEBOOKLM_RUN_AS` | `10006:10006` | container uid/gid |
| `NOTEBOOKLM_BIND_IP` | `127.0.0.1` | host address the API is published on |
| `NOTEBOOKLM_PORT` | `8860` | host and container port |
| `NOTEBOOKLM_API_TOKEN` | empty | shared bearer token; requests are refused while it is empty |
| `NOTEBOOKLM_SESSION_MODE` | `cdp` | `cdp` or `persistent` |
| `NOTEBOOKLM_CDP_URL` | `http://host.docker.internal:9222` | DevTools endpoint in cdp mode |
| `NOTEBOOKLM_BROWSER_PROFILE` | `/data/notebooklm-browser-profile` | profile directory in persistent mode |
| `NOTEBOOKLM_HEADLESS` | `true` | persistent mode only; set `false` for the sign-in |
| `NOTEBOOKLM_TIMEOUT` | `1800` | whole-job budget in seconds |
| `NOTEBOOKLM_STEP_TIMEOUT_SECONDS` | `45` | one UI interaction |
| `NOTEBOOKLM_SOURCE_TIMEOUT_SECONDS` | `300` | NotebookLM reading the sources |
| `NOTEBOOKLM_VIDEO_TIMEOUT_SECONDS` | `1500` | the Video Overview render |
| `NOTEBOOKLM_DOWNLOAD_TIMEOUT_SECONDS` | `300` | fetching the mp4 |
| `NOTEBOOKLM_POLL_SECONDS` | `15` | worker poll interval |
| `NOTEBOOKLM_DEFAULT_PROFILE` | `technical_fa` | profile used when the caller sends none |
| `NOTEBOOKLM_LOCALE` | `fa-IR` | browser locale |
| `NOTEBOOKLM_TIMEZONE` | `Asia/Tehran` | browser time zone |
| `NOTEBOOKLM_HOME_URL` | `https://notebooklm.google.com/` | entry page |
| `NOTEBOOKLM_KEEP_SCREENSHOTS` | `true` | save a screenshot and an element dump when a job fails |
| `NOTEBOOKLM_UPLOAD_TTL_SECONDS` | `86400` | how long stored source files are kept |
| `NOTEBOOKLM_LOG_LEVEL` | `INFO` | log level |
| `NOTEBOOKLM_SELECTORS_FILE` | empty | JSON file with calibrated selectors |

Content Bot side:

| Key | Default | Meaning |
| --- | --- | --- |
| `CONTENT_NOTEBOOKLM_URL` | `http://notebooklm-worker:8860` | worker API; empty hides the button |
| `CONTENT_NOTEBOOKLM_TOKEN` | `NOTEBOOKLM_API_TOKEN` | bearer token |
| `CONTENT_NOTEBOOKLM_ENABLED` | `true` | master switch for the button |
| `CONTENT_NOTEBOOKLM_TIMEOUT` | `1800` | bot-side job budget; must exceed the worker budget |
| `CONTENT_NOTEBOOKLM_DEFAULT_PROFILE` | `technical_fa` | profile used when a stored draft has none |

## 8. Operations

```bash
./manage.sh notebooklm-status
./manage.sh logs notebooklm-worker          # or: docker compose logs -f notebooklm-worker
docker compose --profile notebooklm up -d notebooklm-worker
```

Data layout:

```
data/notebooklm-worker/
├── jobs.json                 job store (atomic writes; a corrupt file is renamed to .corrupt-<ts>)
├── uploads/                  source files received through POST /uploads
├── videos/<job-id>.mp4       finished videos served as artifacts
├── logs/<job-id>.log         per-job trace plus snapshots of a failure
├── logs/<job-id>.png         screenshot of the page when the job failed
├── logs/<job-id>.json        element dump (tag, id, role, aria-label, text) for calibration
└── probes/<id>.{png,json}    output of POST /session/probe
```

Backups: `data/notebooklm-worker` belongs to the `notebooklm` backup section,
so `./manage.sh backup` archives the job store, the videos, and the logs. The
browser profile is only useful with the same machine and Google account; treat
it as disposable.

Upgrades: change `NOTEBOOKLM_WORKER_IMAGE_TAG` and run
`docker compose --profile notebooklm up -d notebooklm-worker`. The image is
published by `.github/workflows/publish-notebooklm-worker.yml` on every push
that touches `notebooklm-worker/**`.

## 9. Selector calibration

NotebookLM is a Google web app, so its DOM can change without notice. Every
control lives in `notebooklm-worker/app/notebook.py` as an ordered list of
candidate selectors (text match first, then role, then aria-label). When the
UI changes:

1. let one job fail, or call `POST /session/probe {"wait_seconds": 20}`;
2. open `data/notebooklm-worker/logs/<job-id>.png` and `.json` (or
   `probes/<id>.json`) and find the control you need;
3. write an override file and point `NOTEBOOKLM_SELECTORS_FILE` at it:

```json
{
  "new_notebook": ["button:has-text('Create new')"],
  "add_source": ["button:has-text('Add source')"],
  "video_overview": ["button:has-text('Video Overview')"]
}
```

Overrides replace the whole candidate list of that key, so keep two or three
variants. The keys are: `new_notebook`, `notebook_title`, `add_source`,
`source_file`, `source_website`, `source_youtube`, `source_text`,
`file_input`, `url_input`, `text_input`, `source_confirm`, `dialog_close`,
`studio_tab`, `video_overview`, `video_customize`, `video_prompt`,
`video_generate`, `video_ready`, `video_menu`, `video_download`.

Mount the file read-only and recreate the worker:

```yaml
# docker-compose.override.yml
services:
  notebooklm-worker:
    volumes:
      - ./data/notebooklm-worker/selectors.json:/selectors.json:ro
```

```bash
NOTEBOOKLM_SELECTORS_FILE=/selectors.json
docker compose --profile notebooklm up -d --force-recreate notebooklm-worker
```

Report the diff (old selector → new selector) with the screenshot; that is
enough to update the shipped defaults.

## 10. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `The Google session is signed out` | the attached browser lost its session | sign in again, then `./manage.sh notebooklm-login 15` |
| `Executable doesn't exist` / connection refused on 9222 | the DevTools browser is not running, or cdp mode cannot reach it | start Chromium with `--remote-debugging-port=9222`, check `NOTEBOOKLM_CDP_URL` |
| `The connected Chrome has no open window` | the browser has no window | open one window in that Chrome |
| `job timed out` in the chat | the render exceeded `CONTENT_NOTEBOOKLM_TIMEOUT` | raise both budgets (`NOTEBOOKLM_TIMEOUT`, `CONTENT_NOTEBOOKLM_TIMEOUT`) or pick `short` |
| job fails on one step, screenshot shows a new layout | selector drift | calibrate with section 9 |
| `HTTPSConnectionPool` / proxy errors in the log | the container has no outbound route to Google | check DNS and egress from the container: `docker compose exec notebooklm-worker python -c "import urllib.request;print(urllib.request.urlopen('https://notebooklm.google.com/', timeout=10).status)"` |
| `The worker is not configured` toast in Telegram | `CONTENT_NOTEBOOKLM_URL` is empty or `CONTENT_NOTEBOOKLM_ENABLED=false` | `./manage.sh content-status`, then restart the bot |
| HTTP 401 from the worker | the bot token and `NOTEBOOKLM_API_TOKEN` differ | `./manage.sh notebooklm-enable` rewrites both, then restart the bot |
| video missing after `ready` | the artifact was pruned or the job id is wrong | re-run the job; check `data/notebooklm-worker/videos` |
| `NotebookLM refused the source` | a page needs a login or is region blocked | remove that source or paste its text as `text` |

Where to look first:

```bash
./manage.sh logs notebooklm-worker                       # worker trace
tail -50 data/notebooklm-worker/logs/<job-id>.log        # one job
docker compose logs --tail=100 content-bot               # bot side
```

Job failures keep both a PNG and a JSON element dump. `GET /jobs/<id>` also
returns the last lines of the job log as `log_tail`, which the panel shows.

## 11. Limits and notes

- One video at a time. The runner is serial by design: a single browser
  session cannot render two overviews at once, and Google rate-limits rapid
  requests. Queue depth is visible in `GET /healthz` (`queued`, `running`).
- Video Overview length is decided by NotebookLM; the prompt and the profile
  only steer it. Treat the target length as an instruction, not a contract.
- Generated videos live in the worker until they are pruned by hand. The
  Telegram preview is the durable copy the bot needs for publishing.
- The account, not the stack, owns the output. Keep the NotebookLM terms in
  mind: the automation drives the web app the same way a person would.
- The bot hides the button whenever the worker is unreachable, so a broken
  session never blocks the rest of the pipeline.

## 12. See also

- `docs/MEDIA-STUDIO.md` — images, browser-driven clips, and the shared Google
  session patterns (cdp vs persistent, region handling).
- `docs/CONTENT-PRODUCTION-GUIDE.md` — the full operator guide.
- `docs/CONTENT-PRODUCTION-ARCHITECTURE.md` — how the services fit together.
- `docs/APARAT-SETUP.md`, `docs/LINKEDIN-SETUP.md` — publishing destinations
  that accept the produced video.
