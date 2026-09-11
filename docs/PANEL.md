# Operator panel

`docs/PANEL.md` describes the optional **operator panel**: a small web console
for reading stack status, editing the validated configuration files, tailing
service logs, and running a fixed whitelist of stack actions. It is an optional
Compose profile (`panel`) and nothing else in the stack depends on it.

Screens from a running console (demonstration data) are published with the
README: `docs-site/assets/content-console-overview-v0.3.0.png`,
`content-console-pipeline-state-v0.3.0.png`,
`content-console-storage-v0.3.0.png`, `content-console-backups-v0.3.0.png`, and
`content-console-actions-v0.3.0.png` in the same directory. They are captured
from the panel Docker image against a seeded demo stack directory, so nothing
in them comes from a real deployment.

## Why a separate container

The panel ships as its own service (`content-panel`) instead of living inside
the Content Bot container, even though both belong to the same Compose project:

- The Content Bot image has a read-only root filesystem, runs as an unprivileged
  uid, holds Telegram and Instagram credentials, and is published to Docker Hub.
  The panel needs to write `.env` and the policy files and to talk to the Docker
  socket, so mixing them would widen the bot's blast radius and couple two very
  different release cycles.
- The panel must be able to restart, recreate, or roll back the Content Bot and
  Media Studio containers. A process cannot reliably manage the container it
  runs in.
- Keeping it separate lets the panel be stopped entirely (`./manage.sh
  panel-disable`) while the content pipeline keeps publishing.

The panel joins the same `agent-net` network, mounts the repository at the
identical host path, and is managed by the same `manage.sh`, so it still feels
like one stack.

## Quick start

```bash
./manage.sh panel-enable     # enable the profile, create a token, start it
./manage.sh panel-token      # print the operator token
```

Then open <http://127.0.0.1:8899/> and paste the token. Useful commands:

| Command | Purpose |
| --- | --- |
| `./manage.sh panel-status` | Profile state, URL, token state, container status |
| `./manage.sh panel-enable` | Add the `panel` profile, create the token, start the console |
| `./manage.sh panel-disable` | Stop the console and remove the profile |
| `./manage.sh panel-token` | Print the operator token (creates one if missing) |
| `./manage.sh panel-rotate-token` | Replace the token and restart the panel |
| `./manage.sh panel-build` | Build the image locally from `panel/Dockerfile` |

With the profile enabled, the same result through Compose is:

```bash
docker compose --profile panel up -d panel
```

## What the console shows

- **Overview** - containers, health, image tags, published host ports, disk
  usage under `data/`, the pipeline counters, the Instagram credential card
  (token state, last automatic refresh, expiry, last error, and a **Refresh
  token now** button), Media Studio jobs, and warnings when an internal
  endpoint (n8n/MCP, router dashboards) is published beyond loopback.
- **Pipeline state** - `data/content-bot/state.json` counters, the configured
  routines with their last run, and the most recent drafts with per-draft
  console actions (see below).
- **Configuration** - validated editors for `editorial-policy.yaml`,
  `sources.yaml`, `categories.yaml`, and `tools.json`. Every save validates a
  copy first, keeps a timestamped backup under `data/panel/backups/`, and writes
  the file atomically. Previous versions can be restored from the same view,
  and a file that has no working copy yet can be created from the shipped
  default in `content/config/` with **Create from shipped default**.
- **Environment** - the `.env` keys with secret values masked. Editing a key
  rewrites that line only and asks for **Apply changes** afterwards.
- **Logs** - `docker compose logs` tails per service with an optional
  auto-refresh.
- **Object storage** - the shared S3 block every consumer reads:
  `S3_STORAGE_BACKEND`, the in-network and host endpoints, the bucket and key
  prefix, path-style addressing, the public base URL, the Open WebUI storage
  provider, and a per-service table showing which component stores where. The
  bundled RustFS card reports the live container state and links its console
  when `RUSTFS_BIND_IP` publishes one. **Run status** and **Verify endpoint**
  run the same `manage.sh s3-status` / `s3-verify` checks the CLI offers, and
  the view warns when Open WebUI points at storage that is not running or an
  external backend has no endpoint.
- **Backups** - the archives in the backup directory (`hermes-stack-*.tar.gz`,
  plus `.age` when encryption is on) with creation time from the `.meta.json`
  sidecar, size, stack version, and whether each archive is a full or partial
  backup. **Create backup** runs `manage.sh backup --label panel --no-pause`
  after a confirmation, and **Reload list** re-reads the directory. The panel
  never opens an archive and never parses it beyond the sidecar metadata.
  **Partial backup** lists the section names from `./manage.sh backup-sections`
  as toggles: pick any combination and **Back up selected sections** runs
  `manage.sh backup --only SECTION[,...] --label panel-section --no-pause`. The
  section list comes from `scripts/stack-ops.sh` at request time, so it cannot
  drift from the CLI, and a name the CLI would reject never reaches the command.
- **Actions** - a fixed whitelist: apply changes, restart Content Bot / Media
  Studio / Smart Router, pull published images, `content-status`,
  `media-status`, `router-status`, `doctor`, `s3-status`, `s3-verify`, and
  create backup / section backup. Free-form commands are never accepted; the
  backup, section backup, and `Apply changes` entries ask for confirmation
  first. Only the section names the CLI publishes are accepted as action
  input, and the panel rejects anything else before a command is built. Set
  `PANEL_ACTIONS_ENABLED=false` in `.env` to keep the console read-only.

### Backups created from the panel

The panel runs as the operator uid, which cannot read every data directory
(`data/smart-router` is root-owned, for example), so **Create backup** starts a
throwaway container from the panel image instead: it mounts the Docker socket,
the checkout, and the backup directory, runs the very same `manage.sh backup`,
and exits. Two consequences are worth knowing:

- The run is a live backup (`--no-pause`), because the other containers keep
  serving while a container the panel did not start is running. Run
  `./manage.sh backup` on the host when you want a paused, consistent snapshot;
  every action of the host command is available there.
- `scripts/stack-ops.sh` writes archives as `0600` - they contain `.env`
  secrets - so the wrapper hands the `hermes-stack-*` files back to the owner of
  the backup directory. Without that step the operator could read the listing
  but not the archive, and the sidecar metadata could not be parsed.

`./manage.sh restore` and `./manage.sh backup-list` work on those archives
exactly like on host-created ones.

## Draft actions

The **Pipeline state** view lists the live drafts from
`data/content-bot/state.json` and offers the same decisions as the Telegram
keyboard, without leaving the console:

| Action | Draft status | Effect |
| --- | --- | --- |
| `Text only` | `media_ask`, `awaiting_media`, `media_failed` | Answers the media question with no media. |
| `AI image` | `media_ask`, `media_failed` | Submits the configured image driver to Media Studio. |
| `Publish` | `text`, `text_only`, `media_ready` | Runs the normal approval path and publishes to Telegram. |
| `Discard` | any | Drops the draft and deletes its Telegram messages. |

The console never writes `state.json`: the bot keeps the authoritative copy in
memory and rewrites the whole file, so an external edit would be lost. Instead
the panel appends a validated request to `data/content-bot/panel-actions.requests.jsonl`
under a `flock` (`panel-actions.lock`), and the bot drains that queue from its
main loop — within a second or two, even while Telegram itself is unreachable —
through the same code path the Telegram buttons use. The outcome is appended to
`data/content-bot/panel-actions.results.json` and shown under **Console action
results** in the panel, and the operator also gets a Telegram message. Draft
ids and action names are validated against a fixed list, so the queue can only
carry supported actions.

`Publish` is the one action that is not reversible: it uses the same limits as
Telegram (daily publish cap, media-still-running guard) and the browser asks
for confirmation first.

## Security model

- Authentication is a single operator token stored outside the repository at
  `data/panel/token` (mode 0600). A successful login exchanges it for an
  HttpOnly, `SameSite=Strict` session cookie derived with HMAC-SHA256; the token
  itself is never stored in the browser. State-changing requests additionally
  require the `X-Panel-Csrf` header, so a cross-site form cannot submit edits.
- Secrets never leave the server: `.env` values whose key matches
  `SECRET|TOKEN|PASSWORD|_KEY|API_KEY|HASH` are returned as `null`, and the UI
  only shows whether they are set.
- The panel is bound to `127.0.0.1` by default. Requests are validated against a
  fixed action list and a fixed set of configuration files; path traversal is
  rejected.
- The panel mounts `/var/run/docker.sock` to manage the stack. Socket access is
  root-equivalent, so anyone who reaches the console with the token can control
  every container on the host. Treat the token like a root password, keep the
  port on loopback or a trusted network, and use `./manage.sh panel-disable`
  when the console is not needed.
- n8n, its MCP endpoint, and the router dashboards are never published by the
  panel. The overview only warns when `.env` already exposes them.

### Remote access

Publish the console through HTTPS only:

1. Point a reverse proxy (the stack `caddy` profile or a proxy on another host)
   at `http://<stack-host>:8899`. `install.sh` asks whether a reverse proxy on
   another host will publish the console and suggests the detected LAN address
   for the bind; answer the prompt or set the bind later.
2. Set `PANEL_BIND_IP` to the interface the proxy reaches (for example the LAN
   address) or `0.0.0.0` behind a firewall.
3. Set `PANEL_COOKIE_SECURE=true` so the session cookie is HTTPS-only.
4. Run `./manage.sh panel-enable` (or `panel-status`) again and reload the page.

Caddy example for a dedicated hostname:

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:8899
}
```

Keep the token out of proxy logs; the console never puts it in a URL.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PANEL_IMAGE_REPOSITORY` | `afsharidevops/content-panel` | Image repository |
| `PANEL_IMAGE_TAG` | `0.3.0` | Image tag; the Compose service also builds locally when the image is missing |
| `PANEL_BIND_IP` | `127.0.0.1` | Host address the console binds to |
| `PANEL_PORT` | `8899` | Host port |
| `PANEL_ACTIONS_ENABLED` | `true` | `false` serves read-only views |
| `PANEL_COOKIE_SECURE` | `false` | Add `Secure` to the session cookie (HTTPS) |
| `PANEL_RUN_AS` | installing user | uid:gid the container runs as, so edited files keep their owner |
| `PANEL_DOCKER_GID` | docker socket group | Supplementary group that opens the mounted socket |
| `PANEL_STACK_PATH` | repository root | Host path mounted at the identical path inside the container |

## Operations notes

- The container mounts the repository at the same absolute path as the host, so
  `docker compose` inside the panel resolves relative bind mounts exactly like a
  host invocation. Moving the checkout only requires re-running
  `./manage.sh panel-enable`, which refreshes `PANEL_STACK_PATH`.
- `data/panel/` holds the token and the configuration backups; both survive
  `panel-disable` and image upgrades. `./manage.sh uninstall --purge` removes
  them with the rest of the runtime data.
- `./manage.sh panel-enable` creates the backup directory next to the checkout
  (`<checkout>-backups`, mode 0700) and mounts it at the identical host path, so
  the Backups view and the wrapper container write to the same place the host
  commands use. Set `CONTENT_MANAGER_BACKUP_DIR` in `.env` to move it; the panel
  mounts and passes that directory too.
- The image is published by `.github/workflows/publish-panel.yml`
  (`afsharidevops/content-panel:<version>`), and can always be built locally
  with `./manage.sh panel-build`.
- Panel logs are plain HTTP access lines: `docker compose logs panel` or option
  5 of the panel menu.
