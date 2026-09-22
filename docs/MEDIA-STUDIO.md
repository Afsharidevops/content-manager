# Media Studio

Media Studio is an optional media generation worker for the Content Manager
stack. It runs jobs one at a time and exposes a small JSON API on
`127.0.0.1:8850` (configurable). Each job belongs to a driver:

| Driver        | Group   | Needs        | Produces               |
|---------------|---------|--------------|------------------------|
| `api-image`   | api     | an OpenAI-compatible image API | PNG/JPEG/WebP images |
| `api-video`   | api     | an OpenAI-compatible video API | MP4/WebM video       |
| `flow-video`  | google  | a signed-in Google Flow session | MP4 video             |
| `gemini-image`| google  | a signed-in Gemini session      | PNG/JPEG/WebP images |

The `api-image` driver keeps Media Studio fully usable when there is no Google
subscription at all: it calls the same gateway the Content Bot uses
(`MEDIA_STUDIO_WRITER_*` values fall back to `CONTENT_WRITER_*`), so a router
or hosted API is enough. Google drivers are optional extras.

One condition applies to the router case: the endpoint must implement the
OpenAI images API (`POST <base>/images/generations`) and
`MEDIA_STUDIO_WRITER_MODEL` must be an image model that endpoint serves. The
Smart Router of this stack speaks chat completions, Responses, and Messages
only, so pointing `MEDIA_STUDIO_WRITER_BASE_URL` at it makes every image job
fail with `HTTP 404`; a gateway that serves both (for example a router with an
image-capable provider connected) is required.

A working image configuration is three keys in `.env`:

```bash
MEDIA_STUDIO_WRITER_BASE_URL=https://your-image-api.example/v1
MEDIA_STUDIO_WRITER_MODEL=ai-strong
MEDIA_STUDIO_WRITER_API_KEY=...
```

The model may be an alias of the endpoint as long as it resolves to an image
model. `./manage.sh media-status` prints the endpoint the container uses, the
panel's Media Studio test proves the endpoint answers the images route before
a draft asks for one, and a job that still fails reports the provider answer
in the chat.

## Video generation over an API

`api-video` generates a clip over HTTP the same way `api-image` generates an
image, so a deployment without a Google session can still answer the video
button of the Content Bot. It speaks the asynchronous video API of the router
family (the 9router contract that xAI Grok Imagine uses):

1. `POST <base>/videos/generations` with `{"model", "prompt"}` and optional
   `duration` (seconds), `aspect_ratio`, `resolution`, `negative_prompt`, and
   `seed` returns a job id (`request_id`).
2. `GET <base>/videos/<id>` is polled every `MEDIA_STUDIO_VIDEO_POLL_SECONDS`
   until the provider answers `done` (or `failed`).
3. The finished `video.url` is downloaded and stored as `video_1.mp4`; the
   configured brand chip is baked in with ffmpeg unless the job passes
   `"params": {"brand": false}`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEDIA_STUDIO_VIDEO_BASE_URL` | the writer endpoint | Gateway root, including `/v1`. |
| `MEDIA_STUDIO_VIDEO_API_KEY` | the writer key | Bearer token for the video route. |
| `MEDIA_STUDIO_VIDEO_MODEL` | empty | Video model id; a job without one fails with this variable in the message. |
| `MEDIA_STUDIO_VIDEO_PROVIDER` | derived from the model | Adds `?provider=` to the generation request. |
| `MEDIA_STUDIO_VIDEO_DURATION` | provider default | Clip length in seconds for jobs that carry no duration. |
| `MEDIA_STUDIO_VIDEO_ASPECT_RATIO` | provider default | For example `16:9` or `9:16`. |
| `MEDIA_STUDIO_VIDEO_RESOLUTION` | provider default | For example `720p`. |
| `MEDIA_STUDIO_VIDEO_POLL_SECONDS` | `10` | Delay between status lookups. |
| `MEDIA_STUDIO_VIDEO_TIMEOUT_SECONDS` | `900` | Give up on a job that never finishes. |

The model must be one the gateway can really run. A gateway that proxies video
(9router and its forks) keeps a small list of video-capable providers - the
model name selects one of them:

| Provider | Model examples |
| --- | --- |
| `xai` | `xai/grok-imagine-video` |
| `openrouter` | `openrouter/google/veo-3.1`, `openrouter/openai/sora-2-pro`, `openrouter/bytedance/seedance-2.0` |
| `vertex` | `vertex/veo-3.1-fast-generate-preview`, `vertex/veo-3.0-generate-001` |

Two answers are easy to confuse with a broken endpoint and both have their own
message in the job log and in the chat:

- `Combos are not supported for video generation` - the stored model is a chat
  combo (a multi-provider group such as `ai` or `ai-strong`) instead of one
  video model. Combos are a chat feature, so store the concrete model.
- `No credentials for provider: <provider>` - the route works, but the gateway
  holds no key or account for that provider. Connect it in the router, or set
  `MEDIA_STUDIO_VIDEO_BASE_URL` to an API that serves video directly.

### Connecting a video provider

Video is the one capability a chat-only gateway cannot fake, so the gateway
needs an account for one of the three providers above. In the router dashboard
(9router: **Providers**, then add a connection) pick one:

| Option | What to supply | Notes |
| --- | --- | --- |
| xAI (Grok) | **Sign in** (OAuth) or an API key from `console.x.ai` | Cheapest when a Grok subscription already exists: `grok-imagine-video` supports duration, aspect ratio, and resolution. |
| OpenRouter | An API key from `openrouter.ai/settings/keys` | Serves `google/veo-3.1`, `openai/sora-2-pro`, and `bytedance/seedance-2.0`; **video is paid** - a free-tier key answers `402`, so the account needs credits. |
| Vertex AI | A Google Cloud service account JSON with the Vertex AI API enabled | Serves `veo-3.1-fast-generate-preview`, `veo-3.0-generate-001`, and `veo-2.0-generate-001`; new Google Cloud accounts get $300 of credit, which is the cheapest way to try Veo. |

After the connection tests green in the router, store the model that belongs to
it, then restart the two services that read the value:

```bash
MEDIA_STUDIO_DRIVERS=api-image,api-video,flow-video,video-edit,timeline-video
MEDIA_STUDIO_VIDEO_MODEL=xai/grok-imagine-video
CONTENT_MEDIA_VIDEO_DRIVER=api-video
docker compose up -d media-studio content-bot
```

The panel's **Platforms -> Media Studio -> Test** names the missing piece
before a draft asks for a clip: no model, a chat combo, a route the gateway does
not serve, or a provider without credentials.

Example: `api-video` against the same gateway that serves the images.

```bash
MEDIA_STUDIO_DRIVERS=api-image,api-video,video-edit
MEDIA_STUDIO_VIDEO_BASE_URL=https://ai.example.com/v1
MEDIA_STUDIO_VIDEO_MODEL=xai/grok-imagine-video
MEDIA_STUDIO_VIDEO_API_KEY=...
CONTENT_MEDIA_VIDEO_DRIVER=api-video
```

The Content Bot picks its video driver from `CONTENT_MEDIA_VIDEO_DRIVER`
(`flow-video` by default, `api-video` with the block above, `video-edit` for
operator clips). When the bot and Media Studio run on one server, the panel's
Media Studio test also probes the video route and names the missing variable
before an operator presses the video button.

## Install

Run `./install.sh`. The wizard offers a Media Studio-only install (option 6)
and a combined one-server content pipeline install (option 7: Content Bot +
Media Studio with an external model API); choosing `5) Install Content Bot
only` also asks whether Media Studio should be installed to serve that bot.
On an existing install the wizard asks whether to add or reconfigure Media
Studio (menu group 8 in `./manage.sh`, or `./manage.sh media-configure`). The
wizard collects:

- enabled drivers (default `api-image,flow-video,video-edit`);
- the image API endpoint/model when `api-image` is enabled;
- an API token for the local HTTP API (recommended when the bind IP is not
  loopback);
- the two Google Flow region settings described below.

When Media Studio is installed together with the Content Bot on the same
server, the installer wires them automatically: it writes
`CONTENT_MEDIA_STUDIO_URL=http://media-studio:8850`, mirrors the Media Studio
API token into `CONTENT_MEDIA_STUDIO_TOKEN`, and sets the bot image/video
drivers from the enabled Media Studio drivers. Rotating the Media Studio token
through `./manage.sh media-configure` propagates the new value to the Content
Bot. A combined view is available with `./manage.sh pipeline-status`.

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
`/unsupported-country` and stops. Flow reaches that decision inside the page,
from two batchexecute answers, so Media Studio ships three layers:

1. response patching through the bundled `locallab-flow-unlock` extension: the
   country and age flags of `GetFlowAppConfig` and the tool status of
   `CheckToolAvailability` are rewritten before the web app reads them, so the
   dashboard renders for the signed-in account;
2. request interception that aborts any Flow request to
   `flow.google.com/unsupported-country` (and `flow.google-*.com`), controlled
   by `MEDIA_STUDIO_BLOCK_GEO_REDIRECT`;
3. an optional short page freeze once the project creation button appears
   (`MEDIA_STUDIO_FREEZE_ON_READY`), which stops the background region check
   from replacing the editor UI when the patched answer is not what the page
   received.

All three default to enabled. The same techniques are available standalone for
manual browser use, independent of Media Studio:

- `extensions/locallab-flow-unlock/` — an unpacked Chrome extension (MV3): a
  page-world content script patches the two region answers, a declarative rule
  blocks the unsupported-country page, and a second content script applies the
  freeze. Load it from `chrome://extensions` with Developer mode enabled.
- `extensions/locallab-flow-unlock-firefox/` — the same extension for Firefox
  140+ (MV3 event page, blocking `webRequest` instead of a rule file). Load it
  from `about:debugging#/runtime/this-firefox`. The packaged images keep using
  the Chrome folder.
- `extensions/locallab-flow-unlock/ublock-filter.txt` — the two filter lines for
  uBlock Origin.

Flow availability still depends on the Google account and region policy of the
Google AI subscription. Media Studio never depends on Flow: `api-image` jobs
keep working without any Google service.

For a laptop-only setup (no Media Studio, no Docker) follow
`docs/FLOW-UNLOCK-STANDALONE.md`; the same `extensions/locallab-flow-unlock` files are
used there.

## API

The API binds to `127.0.0.1:8850` by default (`MEDIA_STUDIO_BIND_IP` /
`MEDIA_STUDIO_PORT`). Inside the Compose profile the container listens on every
interface so `content-bot` can reach `http://media-studio:8850`, while the
published host port still follows `MEDIA_STUDIO_BIND_IP`. When
`MEDIA_STUDIO_API_TOKEN` is set, every request except `/healthz` needs
`Authorization: Bearer <token>`.

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

# submit an API video job (needs MEDIA_STUDIO_VIDEO_MODEL)
curl -X POST http://127.0.0.1:8850/jobs \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"driver": "api-video", "prompt": "Slow drone shot over a misty forest",
       "params": {"duration": 8, "aspect_ratio": "16:9"}}'

# poll a job
curl http://127.0.0.1:8850/jobs/<id> -H 'Authorization: Bearer <token>'

# download an artifact
curl -OJ http://127.0.0.1:8850/artifacts/<id>/<filename> \
  -H 'Authorization: Bearer <token>'

# brand one raw image (used by the Content Bot for operator uploads)
curl -X POST http://127.0.0.1:8850/brand \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: image/png' \
  --data-binary @photo.png -o branded.png

# store one operator-recorded clip for the video-edit driver
curl -X POST http://127.0.0.1:8850/uploads \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: video/mp4' \
  --data-binary @clip.mp4

# normalise that clip (the id comes back from /uploads)
curl -X POST http://127.0.0.1:8850/jobs \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"driver": "video-edit", "prompt": "prepare the clip",
       "params": {"upload_id": "<id>"}}'

# validate a timeline document (no render)
curl -sS -X POST "$MEDIA_STUDIO_URL/timeline/validate" \
  -H "Authorization: Bearer $MEDIA_STUDIO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"timeline": {"scenes": [{"narration": "hello", "duration": 3}]}}'

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
`DELETE /jobs/<id>`, `GET /artifacts/<id>/<name>`, `POST /brand`,
`POST /uploads`.

### Video edit for operator clips

The Content Bot offers **Edit it / Publish as-is** whenever the operator sends
a recorded clip. "Edit it" uploads the file to `POST /uploads` (raw bytes,
64 MiB limit) and submits a `video-edit` job that carries only the returned
id, so a job can never read outside the uploads directory. The driver runs
ffmpeg and returns one `edited-<name>.mp4` artifact:

- H.264 video and AAC audio in an MP4 container with `faststart`, so Telegram,
  Instagram, and desktop players accept the file,
- the long side capped at `MEDIA_STUDIO_VIDEO_EDIT_MAX_SIDE` (default 1920)
  with the aspect ratio and even pixel dimensions preserved,
- an optional trim to `MEDIA_STUDIO_VIDEO_EDIT_MAX_SECONDS` (default 0, keep
  the full clip),
- container metadata (rotation, GPS, device tags) stripped.

`MEDIA_STUDIO_VIDEO_EDIT_TIMEOUT_SECONDS` bounds one ffmpeg run and
`MEDIA_STUDIO_UPLOAD_TTL_SECONDS` (default 86400) prunes stored uploads.
`MEDIA_STUDIO_FFMPEG` pins a specific binary; when it is empty the image falls
back to `imageio-ffmpeg` from `requirements.txt`, so no system package is
needed. `video-edit` must be listed in `MEDIA_STUDIO_DRIVERS` (it is part of
the shipped default). `Publish as-is` never calls Media Studio.

### Timeline render (deterministic video)

The `timeline-video` driver renders a **timeline document** to one MP4 with
ffmpeg. Planning agents (the Smart Router content agents) produce that
document; the renderer never receives a raw script, so the same timeline
always produces the same video. Submit the timeline inside the job params,
either as an object or as a JSON string:

```bash
curl -sS -X POST "$MEDIA_STUDIO_URL/jobs" \
  -H "Authorization: Bearer $MEDIA_STUDIO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
        "driver": "timeline-video",
        "prompt": "render the timeline",
        "params": {
          "timeline": {
            "version": 1,
            "meta": {"title": "Docker on RouterOS", "aspect_ratio": "9:16",
                      "brand": {"label": "Locallab", "position": "bottom-right"}},
            "audio": {"upload_id": "<upload-id>", "volume": 1.0},
            "scenes": [
              {"duration": 4, "narration": "متن صحنه اول", "animation": "zoom-in"},
              {"duration": 5, "narration": "Second beat", "transition": "fade",
               "upload_id": "<upload-id>", "asset_type": "image"},
              {"duration": 3, "narration": "Closing line", "transition": "wipeleft"}
            ]
          }
        }
      }'
```

Scene fields:

| Field | Values | Notes |
| --- | --- | --- |
| `duration` | 0.5 – 120 seconds | clamped on validation |
| `narration` | text | painted on the card, or into the caption band over an asset |
| `visual` | text | optional heading; also used as the card text when `narration` is empty |
| `asset_type` | `auto`, `text`, `solid`, `image`, `video` | `auto` picks image/video when an asset is present, text otherwise |
| `upload_id` | upload id | resolved inside the uploads directory only |
| `asset_path` | absolute path | must live under the uploads or the job work directory |
| `transition` | `cut`, `fade`, `dissolve`, `slideleft`, `slideright`, `wipeleft`, `wipeup`, `circleopen` | the first scene is always `cut` |
| `animation` | `none`, `zoom-in`, `zoom-out`, `pan-left`, `pan-right` | Ken Burns style motion on stills |
| `emotion` | free text | passed through as metadata for upstream planning |

Meta fields: `aspect_ratio` (`9:16`, `16:9`, `1:1`, `4:5`), `resolution`
(explicit `1080x1920` overrides the preset), `fps` (24/25/30/50/60),
`subtitle` (paint captions over image and video scenes), and `brand`
(`label`, `position`, `style`). The optional `audio` block adds one narration
track (padded with `apad` and trimmed with `-shortest`). Generated cards use
the brand gradient palette, and Persian text is shaped right-to-left when
the image ships `libraqm` (it does).

`MEDIA_STUDIO_TIMELINE_TIMEOUT_SECONDS` (default 1800) bounds each ffmpeg
call. `timeline-video` must be listed in `MEDIA_STUDIO_DRIVERS` (it is part
of the shipped default).

### Validate a timeline before rendering

Editors can check a document without paying for a render. `POST
/timeline/validate` runs the same normalizer the driver uses, so a document
that validates is a document that renders:

```bash
curl -sS -X POST "$MEDIA_STUDIO_URL/timeline/validate" \
  -H "Authorization: Bearer $MEDIA_STUDIO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"timeline": {"meta": {"aspect_ratio": "9:16"},
                    "scenes": [{"narration": "First beat", "duration": 4}]}}'
```

A valid document answers `200` with the normalized timeline (defaults filled
in) plus its `totals`; anything else answers `422` with `ok: false`, the
message, the offending `field`, and a `hint`. The panel's Video Studio calls
this endpoint from its Timeline box.

### Brand chip

Every raster artifact produced by an image driver gets the configured brand
mark in one corner. Operator-uploaded photos reach the same mark through
`POST /brand` (the Content Bot calls it automatically), and the `video-edit`
driver bakes it into the prepared clip, so images and reels carry the same
signature. Configure with:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEDIA_STUDIO_BRAND_LABEL` | `Locallab` | Text in the mark; blank disables branding. |
| `MEDIA_STUDIO_BRAND_POSITION` | `bottom-right` | Also `bottom-left`, `top-right`, `top-left`. |
| `MEDIA_STUDIO_BRAND_STYLE` | `aurora` | `aurora` gradient pill, or `chip` for the plain dark rectangle. |

The `aurora` style draws the label into a rounded pill filled with the
LocalLab gradient (cyan to blue to violet to magenta over dark navy), adds a
soft halo, a diagonal highlight sweep, a hairline border, a hexagon mark, and
a shadowed label, so the mark stays readable on light and dark artwork. It is
rendered with Pillow only — no new dependency, no network call — and the
gradient is composited over a translucent navy base so the same chip works on
busy photos.

For videos the mark is rendered as a transparent PNG sized from the output
resolution (about 5.5% of the short side) and composited by ffmpeg as a second
input, which keeps it pixel-exact instead of re-encoding text. Jobs can opt out
per request with `"params": {"brand": false}`; a clip whose dimensions cannot be
read is still encoded, just without the mark.

Animated formats (GIF/WebP) currently get a static mark. A per-frame fade-in
would multiply the render and encode cost of every artifact, so it is left out
until a job actually needs it.

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

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Image API returned HTTP 404 for model <model>` | The endpoint behind `MEDIA_STUDIO_WRITER_BASE_URL` has no `/images/generations` route. The usual cause is pointing it at the stack's own Smart Router, which serves chat completions only. Point it at an OpenAI-compatible image API and set `MEDIA_STUDIO_WRITER_MODEL` to an image model of that endpoint. |
| `No credentials for provider: openai` (or `gemini`, `google`) | The gateway answered, but holds no key for the provider that owns the model name. Connect that provider in the gateway, or call an image API directly and store its key in `MEDIA_STUDIO_WRITER_API_KEY`. |
| `No video model is configured.` | `api-video` ran without `MEDIA_STUDIO_VIDEO_MODEL`. Store a video model of the endpoint (for example `xai/grok-imagine-video`). |
| `Combos are not supported for video generation` | The stored video model is a chat combo such as `ai` or `ai-strong`. Combos never generate video; store the concrete model of a video provider. |
| `No credentials for provider: xai` (or `openrouter`, `vertex`) | The video route exists, but the gateway has no key or account for that provider. Connect it in the router, or point `MEDIA_STUDIO_VIDEO_BASE_URL` at an API that serves video. |
| `Video API returned HTTP 404 for model <model>` | The endpoint has no `/videos/generations` route (the stack Smart Router serves chat completions only). Point `MEDIA_STUDIO_VIDEO_BASE_URL` at a gateway with the video API. |
| `Video job <id> did not finish within 900s` | Provider rendering ran past the deadline. Raise `MEDIA_STUDIO_VIDEO_TIMEOUT_SECONDS`; long clips can take several minutes. |
| `The session browser is not signed in to Google` | A Google driver (`gemini-image`, `flow-video`) needs the Chrome described under "Sessions for Google drivers"; sign in there once and retry. |
| `Image API is unreachable at ...` | DNS, network, or a base URL without `/v1`. `./manage.sh media-status` prints the endpoint the container uses. |

`./manage.sh media-status` also warns when the writer endpoint is the stack
chat gateway, which is the first case in the table.

## Notes

- Jobs run serially: Google sessions are single-profile and generation
  credits should not be bursted.
- Video generation can take minutes; `MEDIA_STUDIO_JOB_TIMEOUT_SECONDS`
  defaults to 900 and Flow export waits up to 180 seconds.
- Flow may ask for credit-usage approval before generating. Set
  `MEDIA_STUDIO_FLOW_AUTO_APPROVE=true` to accept the prompt automatically;
  leave it false to review every generation.
- Downloads open the Flow quality menu and save the entry selected by
  `MEDIA_STUDIO_FLOW_QUALITY` (`270p` GIF, `720p` original, `1080p` or `4K`
  upscaled; default `720p`).
- Job state is a JSON document (`data/media-studio/jobs.json`); artifacts are
  immutable files under `data/media-studio/artifacts/<job_id>/`.
- For credentials: only the operator's own Chrome holds Google cookies; the
  container stores no Google password.

## Related provider

Narrated overview videos can also be produced by Google NotebookLM through a
separate worker (`notebooklm-worker`, compose profile `notebooklm`). It is an
independent provider: Media Studio keeps handling images and recorded clips,
and the Content Bot shows the NotebookLM button only while that worker is
configured. Both providers can reuse the same signed-in Chrome through their
cdp settings. See `docs/NOTEBOOKLM-STUDIO.md`.
