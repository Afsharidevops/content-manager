# Media Studio

Media Studio is an optional media generation worker for the Content Manager
stack. It runs jobs one at a time and exposes a small JSON API on
`127.0.0.1:8850` (configurable). Each job belongs to a driver:

| Driver        | Group   | Needs        | Produces               |
|---------------|---------|--------------|------------------------|
| `api-image`   | api     | an OpenAI-compatible image API | PNG/JPEG/WebP images |
| `flow-video`  | google  | a signed-in Google Flow session | MP4 video             |
| `gemini-image`| google  | a signed-in Gemini session      | PNG/JPEG/WebP images |

The `api-image` driver keeps Media Studio fully usable when there is no Google
subscription at all: it calls the same gateway the Content Bot uses
(`MEDIA_STUDIO_WRITER_*` values fall back to `CONTENT_WRITER_*`), so a router
or hosted API is enough. Google drivers are optional extras.

## Install

Run `./install.sh`. The wizard offers a Media Studio-only install (option 6)
and, on an existing install, asks whether to add or reconfigure Media Studio
(menu group 8 in `./manage.sh`, or `./manage.sh media-configure`). The wizard
collects:

- enabled drivers (default `api-image,flow-video`);
- the image API endpoint/model when `api-image` is enabled;
- an API token for the local HTTP API (recommended when the bind IP is not
  loopback);
- the two Google Flow region settings described below.

Runtime data (job store, logs, artifacts, Chromium profile) lives under
`data/media-studio`.

## Sessions for Google drivers

Google drivers drive a real Chrome that is signed in to Google. Two session
modes exist:

### cdp mode (default, desktop)

Attach Media Studio to a desktop Chrome over the Chrome DevTools Protocol.
Start Chrome once with a dedicated profile:

```bash
google-chrome --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.config/content-manager-chrome"
```

Sign in to https://flow.google.com (and https://gemini.google.com) in that
window. The container reaches the host through `host.docker.internal`
(compose adds the host-gateway mapping), so the default
`MEDIA_STUDIO_CDP_URL=http://host.docker.internal:9222` needs no change.
Media Studio opens and closes its own tabs; it never stores Google
credentials.

### persistent mode (server)

`MEDIA_STUDIO_SESSION_MODE=persistent` launches a container-local Chromium
whose profile lives in `data/media-studio/profile`. Complete the Google sign-in
once by attaching to the container Chromium through its debugging port or a
VNC session. This mode is for server deployments that keep one signed-in
profile per Media Studio instance.

## Google Flow region handling

When Google considers the visitor region unsupported, Flow redirects to
`/unsupported-country` and stops. Media Studio ships two layers:

1. request interception that aborts any Flow request to
   `flow.google.com/unsupported-country` (and `flow.google-*.com`), controlled
   by `MEDIA_STUDIO_BLOCK_GEO_REDIRECT`;
2. an optional short page freeze once the project creation button appears
   (`MEDIA_STUDIO_FREEZE_ON_READY`), which stops the background region check
   from replacing the editor UI.

Both default to enabled. The same two techniques are available standalone for
manual browser use, independent of Media Studio:

- `extensions/flow-unlock/` — an unpacked Chrome extension (MV3): a
  declarative rule blocks the unsupported-country page and a content script
  applies the freeze. Load it from `chrome://extensions` with Developer mode
  enabled.
- `extensions/flow-unlock/ublock-filter.txt` — the two filter lines for
  uBlock Origin.

Flow availability still depends on the Google account and region policy of the
Google AI subscription. Media Studio never depends on Flow: `api-image` jobs
keep working without any Google service.

For a laptop-only setup (no Media Studio, no Docker) follow
`docs/FLOW-UNLOCK-STANDALONE.md`; the same `extensions/flow-unlock` files are
used there.

## API

The API binds to `127.0.0.1:8850` by default (`MEDIA_STUDIO_BIND_IP` /
`MEDIA_STUDIO_PORT`). When `MEDIA_STUDIO_API_TOKEN` is set, every request
except `/healthz` needs `Authorization: Bearer <token>`.

```bash
# submit an image job to the configured API driver
curl -X POST http://127.0.0.1:8850/jobs \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"driver": "api-image", "prompt": "A red cube on a table",
       "params": {"count": 2, "size": "1024x1024"}}'

# submit a Flow video job
curl -X POST http://127.0.0.1:8850/jobs \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"driver": "flow-video", "prompt": "Slow drone shot over a misty forest"}

# poll a job
curl http://127.0.0.1:8850/jobs/<id> -H 'Authorization: Bearer <token>'

# download an artifact
curl -OJ http://127.0.0.1:8850/artifacts/<id>/<filename> \
  -H 'Authorization: Bearer <token>'

# cancel a queued job
curl -X DELETE http://127.0.0.1:8850/jobs/<id> \
  -H 'Authorization: Bearer <token>'

# capture a calibration snapshot of a Google driver page
curl -X POST http://127.0.0.1:8850/session/probe \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"driver": "flow-video", "wait_seconds": 30}'
```

Endpoints: `GET /healthz`, `GET /session/info`, `POST /session/probe`,
`POST /jobs`, `GET /jobs`, `GET /jobs/<id>` (includes the log tail),
`DELETE /jobs/<id>`, `GET /artifacts/<id>/<name>`.

## Selector calibration

Google web UIs change often. Every interaction step in the Google drivers is a
selector list that can be overridden with environment variables, without code
changes:

```text
MEDIA_STUDIO_FLOW_NEW_PROJECT_SELECTOR
MEDIA_STUDIO_FLOW_PROMPT_BOX_SELECTOR
MEDIA_STUDIO_FLOW_SUBMIT_SELECTOR
MEDIA_STUDIO_FLOW_READY_SELECTOR
MEDIA_STUDIO_FLOW_EXPORT_SELECTOR
MEDIA_STUDIO_FLOW_DOWNLOAD_SELECTOR
MEDIA_STUDIO_GEMINI_INPUT_SELECTOR
```

When a step fails, the worker writes a failure snapshot (screenshot plus an
interactive-element dump) into the job artifact directory. A probe job does
the same on demand: submit one, wait for the operator to sign in during the
wait window, then download `probe.json` and `probe.png` to pick selectors.

## Notes

- Jobs run serially: Google sessions are single-profile and generation
  credits should not be bursted.
- Video generation can take minutes; `MEDIA_STUDIO_JOB_TIMEOUT_SECONDS`
  defaults to 900 and Flow export waits up to 180 seconds.
- Job state is a JSON document (`data/media-studio/jobs.json`); artifacts are
  immutable files under `data/media-studio/artifacts/<job_id>/`.
- For credentials: only the operator's own Chrome holds Google cookies; the
  container stores no Google password.
