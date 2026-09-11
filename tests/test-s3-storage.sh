#!/usr/bin/env bash
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

pass=0
fail=0
ok() { printf 'ok - %s\n' "$1"; pass=$((pass+1)); }
not_ok() { printf 'not ok - %s\n' "$1" >&2; fail=$((fail+1)); }

# 1. The shared configuration block exists exactly once per key.
for key in S3_STORAGE_BACKEND S3_ENDPOINT_URL S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY \
  S3_BUCKET S3_REGION S3_FORCE_PATH_STYLE S3_KEY_PREFIX S3_PUBLIC_BASE_URL \
  RUSTFS_ACCESS_KEY RUSTFS_SECRET_KEY RUSTFS_BIND_IP RUSTFS_PORT \
  RUSTFS_CONSOLE_BIND_IP RUSTFS_CONSOLE_PORT RUSTFS_UID RUSTFS_GID \
  OPENWEBUI_STORAGE_PROVIDER; do
  count="$(grep -c "^${key}=" "$ROOT/.env.example" || true)"
  if [[ "$count" != 1 ]]; then
    not_ok ".env.example defines ${key} exactly once (found $count)"
    key_failed=true
  fi
done
if [[ "${key_failed:-false}" != true ]]; then
  ok ".env.example defines every object-storage key exactly once"
fi

# 2. The Compose file renders with and without the object-storage profile.
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  if docker compose -f "$ROOT/docker-compose.yml" --env-file "$ROOT/.env.example" config --quiet; then
    ok "docker compose accepts the shared S3 configuration"
  else
    not_ok "docker compose accepts the shared S3 configuration"
  fi

  rendered="$(docker compose -f "$ROOT/docker-compose.yml" --env-file "$ROOT/.env.example" \
    --profile rustfs --profile open-webui config 2>/dev/null || true)"
  if [[ -n "$rendered" ]] \
     && grep -q 'RUSTFS_VOLUMES: /data' <<<"$rendered" \
     && grep -q 'RUSTFS_CONSOLE_ADDRESS: 0.0.0.0:9001' <<<"$rendered" \
     && grep -q 'source: /home\|/data/rustfs/data' <<<"$rendered" \
     && grep -q 'healthcheck:' <<<"$rendered"; then
    ok "the RustFS service renders with its volumes, console and healthcheck"
  else
    not_ok "the RustFS service renders with its volumes, console and healthcheck"
  fi

  if grep -A40 '^  open-webui:' <<<"$rendered" | grep -q 'STORAGE_PROVIDER: local' \
     && grep -A40 '^  open-webui:' <<<"$rendered" | grep -q 'S3_ENDPOINT_URL:'; then
    ok "Open WebUI receives the shared object-storage variables"
  else
    not_ok "Open WebUI receives the shared object-storage variables"
  fi
else
  printf 'skip - docker is not available; Compose rendering was not verified\n'
fi

# 3. manage.sh behaviour with a fixture checkout and a fake Docker CLI.
FIX="$TMP/repo"
mkdir -p "$FIX/bin"
cp "$ROOT/manage.sh" "$FIX/manage.sh"
chmod +x "$FIX/manage.sh"
cp "$ROOT/.env.example" "$FIX/.env"
printf 'services: {}\n' > "$FIX/docker-compose.yml"
cat > "$FIX/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == info ]]; then exit 0; fi
exit 0
DOCKER
chmod +x "$FIX/bin/docker"

manage() { PATH="$FIX/bin:$PATH" "$FIX/manage.sh" "$@" </dev/null; }
env_of() { sed -n "s/^$1=//p" "$FIX/.env" | tr -d '"'; }

status_off="$(manage s3-status)"
if grep -q 'Backend:            off' <<<"$status_off" \
   && grep -q 'content-bot' <<<"$status_off"; then
  ok "s3-status reports an unconfigured stack without secrets"
else
  not_ok "s3-status reports an unconfigured stack without secrets"
fi

if manage s3-enable --rustfs >/dev/null 2>"$TMP/enable.err"; then
  ok "s3-enable --rustfs succeeds with the bundled backend"
else
  not_ok "s3-enable --rustfs succeeds with the bundled backend"
fi
if [[ "$(env_of S3_STORAGE_BACKEND)" == rustfs ]] \
   && [[ "$(env_of S3_ENDPOINT_URL)" == "http://rustfs:9000" ]] \
   && [[ "$(env_of S3_ACCESS_KEY_ID)" == "$(env_of RUSTFS_ACCESS_KEY)" ]] \
   && [[ -n "$(env_of RUSTFS_SECRET_KEY)" ]] \
   && [[ "$(env_of S3_HOST_ENDPOINT_URL)" == "http://127.0.0.1:9000" ]] \
   && [[ "$(env_of OPENWEBUI_STORAGE_PROVIDER)" == s3 ]] \
   && [[ "$(env_of RUSTFS_UID)" == "$(if [[ "$(id -u)" == 0 ]]; then printf 10001; else id -u; fi)" ]] \
   && grep -q 'rustfs' <<<"$(env_of COMPOSE_PROFILES)"; then
  ok "s3-enable writes the shared block, pins the identity and enables the profile"
else
  not_ok "s3-enable writes the shared block, pins the identity and enables the profile"
fi

if manage s3-enable --rustfs >/dev/null 2>&1 \
   && [[ "$(env_of RUSTFS_ACCESS_KEY)" == "$(sed -n 's/^RUSTFS_ACCESS_KEY=//p' "$FIX/.env" | tr -d '"')" ]]; then
  ok "s3-enable keeps the generated credentials on a second run"
else
  not_ok "s3-enable keeps the generated credentials on a second run"
fi

keys_out="$(manage s3-keys)"
if grep -q '^RustFS access key id: locallab-' <<<"$keys_out" \
   && ! grep -qi "$(env_of RUSTFS_SECRET_KEY)" <<<"$keys_out"; then
  ok "s3-keys prints the access key id without revealing the secret"
else
  not_ok "s3-keys prints the access key id without revealing the secret"
fi

# Renaming the backend must keep the bucket and reject unset endpoints.
if manage s3-enable --external >/dev/null 2>"$TMP/external.err"; then
  not_ok "s3-enable --external refuses to run without endpoint values"
else
  ok "s3-enable --external refuses to run without endpoint values"
fi

if manage s3-verify >/dev/null 2>"$TMP/verify.err"; then
  not_ok "s3-verify fails when the endpoint cannot be reached"
else
  ok "s3-verify fails when the endpoint cannot be reached"
fi
if grep -q 'Endpoint:' "$TMP/verify.err" || grep -q 'Endpoint:' <<<"$(manage s3-verify 2>/dev/null || true)"; then
  ok "s3-verify reports the endpoint it tried"
else
  not_ok "s3-verify reports the endpoint it tried"
fi

guide_out="$(manage s3-guide)"
if grep -q 'docs/S3-STORAGE.md' <<<"$guide_out" \
   && grep -q 'reverse_proxy' <<<"$guide_out"; then
  ok "s3-guide prints the reverse-proxy route"
else
  not_ok "s3-guide prints the reverse-proxy route"
fi

if manage s3-disable >/dev/null 2>&1; then
  ok "s3-disable runs cleanly"
else
  not_ok "s3-disable runs cleanly"
fi
if [[ "$(env_of S3_STORAGE_BACKEND)" == off ]] \
   && [[ "$(env_of OPENWEBUI_STORAGE_PROVIDER)" == local ]] \
   && [[ ",$(env_of COMPOSE_PROFILES)," != *,rustfs,* ]]; then
  ok "s3-disable turns the backend off and removes the profile"
else
  not_ok "s3-disable turns the backend off and removes the profile"
fi

# The help text advertises the storage commands.
help_out="$("$ROOT/manage.sh" help)"
if grep -q 's3-enable' <<<"$help_out" && grep -q 's3-verify' <<<"$help_out"; then
  ok "manage.sh help lists the object-storage commands"
else
  not_ok "manage.sh help lists the object-storage commands"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
