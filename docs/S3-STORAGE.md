# Object storage (S3) for the stack

The stack can keep its files on any S3-compatible service. Two backends are
supported by the same configuration block in `.env`:

- **RustFS** - an Apache-2.0 S3-compatible server that runs inside this stack
  under the optional `rustfs` Compose profile. Nothing leaves the server.
- **External** - an endpoint you already own: AWS S3, Cloudflare R2, Backblaze
  B2, MinIO, Arvan S3, Wasabi, or any other S3-compatible provider.

Every consumer reads one shared set of variables, so switching providers is a
configuration change, not a code change.

## What uses object storage today

| Component | Object storage | Notes |
| --- | --- | --- |
| Open WebUI | Yes | `STORAGE_PROVIDER=s3` moves uploads and generated files into the bucket. `./manage.sh s3-enable` flips it together with the credentials. |
| Content Bot | No (local disk) | Drafts, media, and Instagram state stay under `data/content-bot/`. The media host serves them; a bucket is not required. |
| Media Studio | No (local disk) | Jobs and generated media stay under `data/media-studio/`. |
| n8n | No (free tier) | External binary-data storage is an n8n Enterprise feature. Workflows and credentials stay in `data/n8n/`. |
| Hermes Agent | No | The agent keeps sessions and packages under `data/hermes/`. |
| Smart Router | No | Routing policy, observations, and the cost ledger are local SQLite files. |
| RustFS | Yes | When the bundled server is enabled, its own objects live under `data/rustfs/`. |

Anything can pass the shared block into a container that is not in this table
by using the same `S3_*` variables; the section below documents them.

## Quick start

```bash
./manage.sh s3-status                 # what is configured right now (no secrets)
./manage.sh s3-enable --rustfs        # start the bundled server and wire the stack
./manage.sh s3-verify                 # signed request, credentials, bucket
```

`./manage.sh s3-enable --rustfs` is idempotent. It generates the access key and
the secret once, writes them into `.env`, adds the `rustfs` profile, prepares
`data/rustfs/data` and `data/rustfs/logs`, starts the container, creates the
bucket, and recreates Open WebUI if that profile is enabled.

To use a provider you already own instead:

```bash
./manage.sh s3-enable --external
```

The command asks for the endpoint, bucket, region, and credentials, and stores
them in `.env`. Set the values by hand in `.env` when running unattended.

To go back to local storage:

```bash
./manage.sh s3-disable
```

The bundled server is stopped, the profile is removed, and consumers return to
their on-disk paths. Objects already in the bucket are never deleted.

## Configuration reference

| Variable | Purpose |
| --- | --- |
| `S3_STORAGE_BACKEND` | `rustfs`, `external`, or `off`. |
| `S3_ENDPOINT_URL` | Endpoint the containers use. `http://rustfs:9000` for the bundled server. |
| `S3_HOST_ENDPOINT_URL` | Optional endpoint for tools on the Docker host (`s3-verify`). Empty derives it from the RustFS bind address when the backend is `rustfs`. |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Credentials shared with the services. |
| `S3_BUCKET`, `S3_REGION` | Bucket and region. RustFS accepts any bucket name; keep `us-east-1` as the region unless you changed it. |
| `S3_FORCE_PATH_STYLE` | `true` for RustFS, MinIO, and most self-hosted endpoints. |
| `S3_KEY_PREFIX` | Optional prefix inside the bucket, for example `locallab/`. |
| `S3_PUBLIC_BASE_URL` | Public HTTPS origin that fronts the API, e.g. `https://s3.stack.locallab.ir`. Documentation and status output only. |
| `S3_PUBLIC_CONSOLE_URL` | Public HTTPS origin of the RustFS console, used for browser redirects behind a proxy. |
| `OPENWEBUI_STORAGE_PROVIDER` | `local` (default) or `s3`. `s3-enable` sets it; hand-edit it only when you manage `.env` yourself. |

Bundled server settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `RUSTFS_IMAGE_REPOSITORY` / `RUSTFS_IMAGE_TAG` | `rustfs/rustfs:latest` | Image. Pin a release tag for a frozen deployment. |
| `RUSTFS_BIND_IP` / `RUSTFS_PORT` | `127.0.0.1:9000` | Host address of the S3 API. Use the LAN address when a reverse proxy on another host publishes it. |
| `RUSTFS_CONSOLE_BIND_IP` / `RUSTFS_CONSOLE_PORT` | `127.0.0.1:9001` | Host address of the web console. Keep it on loopback unless you really need remote access. |
| `RUSTFS_CONSOLE_PREFIX` | `/rustfs/console` | Path the console is served under. |
| `RUSTFS_UID` / `RUSTFS_GID` | `10001:10001` | Identity the container runs as, and the owner of `data/rustfs/`. |
| `RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY` | generated | The server credentials. They are mirrored into `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY`. |
| `RUSTFS_REGION` | `us-east-1` | Region the server reports. |

## External providers

| Provider | Endpoint example | Notes |
| --- | --- | --- |
| AWS S3 | `https://s3.eu-central-1.amazonaws.com` | Use an IAM user limited to the bucket. Set `S3_REGION` to the bucket region. |
| Cloudflare R2 | `https://<account-id>.r2.cloudflarestorage.com` | Region `auto` works for signing; keep path-style `true`. |
| Backblaze B2 | `https://s3.<region>.backblazeb2.com` | Create an application key scoped to the bucket. |
| MinIO (self-hosted) | `https://minio.example.com` | Path-style, `S3_REGION` optional. |
| Arvan S3 | the endpoint shown in the Arvan panel | Path-style; keep the bucket private and front it with your own domain if needed. |

After the values are in `.env`, run `./manage.sh s3-verify` to prove the
endpoint, the credentials, and the bucket with a signed request. The check only
needs `curl` with `--aws-sigv4` support (curl 7.75 or newer). Some providers
allocate the bucket themselves; create it in their console first when
`s3-verify --create-bucket` is not allowed to.

Credentials with restricted scope are preferred. A dedicated user that may only
read and write one bucket is enough for every consumer in this stack.

## Publishing the API or the console under a domain

The stack runs behind the LAN address (`192.168.4.11` in the reference setup)
and the MikroTik router publishes domains with its Caddy container. The S3 API
follows the same path as the other services.

1. Point the stack at the LAN address so the proxy can reach it:

   ```bash
   ./manage.sh s3-enable --rustfs --bind-ip 192.168.4.11
   ```

   For the console, set `RUSTFS_CONSOLE_BIND_IP=192.168.4.11` in `.env` and run
   `./manage.sh restart` (or recreate the container).

2. Create the DNS record in ArvanCloud, for example
   `s3.stack.locallab.ir` (and `console.stack.locallab.ir` when the console is
   published).

3. Add the route to the Caddy container on the router:

   ```
   s3.stack.locallab.ir {
       encode zstd gzip
       reverse_proxy 192.168.4.11:9000
   }

   console.stack.locallab.ir {
       encode zstd gzip
       reverse_proxy 192.168.4.11:9001
   }
   ```

   The console lives under `/rustfs/console`, so the public URL is
   `https://console.stack.locallab.ir/rustfs/console/`.

4. Record the public origins so the panel, the documentation, and the console
   redirects agree with the proxy:

   ```
   S3_PUBLIC_BASE_URL=https://s3.stack.locallab.ir
   S3_PUBLIC_CONSOLE_URL=https://console.stack.locallab.ir/rustfs/console
   ```

   Recreate `rustfs` after changing the console URL.

5. Verify from outside:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' https://s3.stack.locallab.ir/health
   ./manage.sh s3-verify
   ```

`./manage.sh s3-guide` prints the same checklist with the addresses detected on
the current host.

### Security notes

- The S3 API must never be published without credentials; both the API and the
  console answer on the same keys. Rotate them with
  `./manage.sh s3-keys --rotate` if they ever leak.
- Prefer publishing only the API. Serve the console over the LAN or a VPN
  unless you accept a login page on the public internet.
- Keep `RUSTFS_BIND_IP` and `RUSTFS_CONSOLE_BIND_IP` on `127.0.0.1` when no
  reverse proxy publishes them. The container still serves the rest of the
  stack over the Compose network.
- The domain is HTTP in front of the proxy and HTTPS behind it; never expose
  port 9000 directly to the internet without TLS.
- The operator panel's exposure view warns when `RUSTFS_BIND_IP` leaves
  loopback, the same way it warns for the n8n MCP endpoint and the router
  dashboards.

## Operating the bundled server

```bash
./manage.sh s3-status                 # endpoints, bucket, consumers, container state
./manage.sh logs rustfs               # server log
./manage.sh s3-keys                   # access key id (secret stays hidden)
./manage.sh s3-keys --show-secrets    # reveal both after a confirmation
./manage.sh s3-keys --rotate          # new credentials, container recreated
./manage.sh backup --only s3          # archive data/rustfs/
```

- Data lives in `data/rustfs/data` (objects) and `data/rustfs/logs`.
- `data/rustfs` is a backup section (`s3`), so section backups and portable
  restores include the bucket contents; see `docs/BACKUP-RESTORE.md`.
- Rotating keys updates `.env`, recreates RustFS, recreates its consumers, and
  re-creates the bucket when it is missing. Existing objects are untouched.
- The server exposes `/health` on the S3 port; the container health check and
  `s3-verify` use it.

## Open WebUI

`./manage.sh s3-enable` sets `OPENWEBUI_STORAGE_PROVIDER=s3` and hands Open
WebUI the endpoint, bucket, credentials, and path-style addressing. Recreate the
container (the command does it) so the new setting applies.

- Files uploaded before the switch stay under `data/open-webui/uploads`; they
  are not migrated automatically. Copy them into the bucket with
  `aws s3 sync`/`rclone` if you need one location.
- Switching back to `local` restores on-disk storage; objects already uploaded
  to the bucket stay there.
- The bucket must exist before the first upload; `s3-enable` and
  `s3-verify --create-bucket` create it.

## Kubernetes (Helm)

The chart ships the same server as an optional component:

```yaml
components:
  rustfs:
    enabled: true
    existingSecret: rustfs-secrets   # RUSTFS_ACCESS_KEY, RUSTFS_SECRET_KEY
    storage:
      size: 20Gi
```

```bash
kubectl create secret generic rustfs-secrets \
  --from-literal=RUSTFS_ACCESS_KEY=locallab-example \
  --from-literal=RUSTFS_SECRET_KEY="$(openssl rand -hex 16)"
helm upgrade --install content-manager ./deploy/helm/hermes-linux-stack \
  -f examples/helm/content-stack-values.yaml
```

Inside the cluster the API is `http://<release>-rustfs:9000`; clients outside it
need an Ingress (see the `rustfs` entry in `ingress.hosts`) or a port-forward.
See `docs/HELM.md` for the full values list and publishing notes.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `s3-verify` returns HTTP 403 | Wrong key/secret or wrong region. Re-run `./manage.sh s3-enable` and check `.env`. |
| `s3-verify` cannot connect | The container is not running or `S3_HOST_ENDPOINT_URL`/`RUSTFS_BIND_IP` points at the wrong address. `./manage.sh s3-status` and `docker compose ps rustfs`. |
| `curl: option --aws-sigv4: is unknown` | The host curl is too old. Verify from a machine with curl 7.75+, or use the provider console. |
| Open WebUI uploads still land on disk | `OPENWEBUI_STORAGE_PROVIDER` is still `local`, or the container was not recreated. Re-run `./manage.sh s3-enable`. |
| RustFS exits with `Permission denied` | `data/rustfs` is not owned by `RUSTFS_UID:RUSTFS_GID`. Fix with `sudo chown -R <uid>:<gid> data/rustfs`, or re-run the installer. |
| Bucket is missing after a restore | Run `./manage.sh s3-verify --create-bucket`; section restores bring the objects back, not the bucket metadata of an external provider. |
| Console shows a wrong redirect | `S3_PUBLIC_CONSOLE_URL` does not match the public console URL; update it and recreate `rustfs`. |

A manual signed request is useful when debugging without the helper:

```bash
curl --aws-sigv4 "aws:amz:us-east-1:s3" --user "$S3_ACCESS_KEY_ID:$S3_SECRET_ACCESS_KEY" \
  "$S3_HOST_ENDPOINT_URL/"
```
