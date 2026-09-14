#!/usr/bin/env bash
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$ROOT_DIR/manage.sh"
help="$($ROOT_DIR/manage.sh help)"
grep -q 'Hermes Linux Stack Manager v0.5.9' <<<"$help"
grep -q 'Interactive groups:' <<<"$help"
grep -q 'router                      Smart Router dashboard' <<<"$help"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cp "$ROOT_DIR/manage.sh" "$tmp/manage.sh"; chmod +x "$tmp/manage.sh"; : > "$tmp/.env"
mkdir -p "$tmp/bin"
cat > "$tmp/bin/docker" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" == info ]] && exit 0
exit 0
EOF
chmod +x "$tmp/bin/docker"
out="$(printf '0\n' | PATH="$tmp/bin:$PATH" "$tmp/manage.sh")"
grep -q 'Overview & health' <<<"$out"
grep -q 'Smart Router' <<<"$out"
grep -q 'Hermes Agent & Telegram' <<<"$out"
grep -q 'Execution & SSH' <<<"$out"
grep -q 'Object storage (S3)' <<<"$out"

grep -q 'instagram-media-enable' <<<"$help"
grep -q 'instagram-media-status' <<<"$help"
grep -q 'content-connect-bale' <<<"$help"
grep -q 'content-connect-eitaa' <<<"$help"
grep -q 'content-connect-linkedin' <<<"$help"
grep -q 'content-aparat-check' <<<"$help"
grep -q 'content-channels' <<<"$help"
grep -q 'domains                     Public host names' <<<"$help"

# Automatic channels: an empty environment reports both channels as waiting,
# and the connect commands write the keys without touching the network.
channels_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-channels)"
grep -q 'Bale: not configured' <<<"$channels_out"
grep -q 'Eitaa: not configured' <<<"$channels_out"
grep -q 'Aparat: not configured' <<<"$channels_out"
connect_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-connect-bale 2>&1 || true)"
grep -q 'CONTENT_BALE_TOKEN' <<<"$connect_out"
PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-connect-bale \
  --token '123:abc' --chat-id '@test_channel' --no-apply >/dev/null
grep -q '^CONTENT_BALE_TOKEN=123:abc$' "$tmp/.env"
grep -q '^CONTENT_BALE_CHAT_ID=@test_channel$' "$tmp/.env"
grep -q '^CONTENT_BALE_API_BASE=https://tapi.bale.ai$' "$tmp/.env"
channels_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-channels)"
grep -q 'Bale: automatic publishing ready' <<<"$channels_out"
grep -q 'LinkedIn: not configured' <<<"$channels_out"

# LinkedIn accepts an author as a person id, an organization id, or an
# explicit URN, and refuses an unknown account type before writing .env.
PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-connect-linkedin \
  --token 'AQXtest' --person-id 'abc123' --no-apply >/dev/null
grep -q '^CONTENT_LINKEDIN_ACCESS_TOKEN=AQXtest$' "$tmp/.env"
grep -q '^CONTENT_LINKEDIN_PERSON_ID=abc123$' "$tmp/.env"
PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-connect-linkedin \
  --token 'AQXorg' --type organization --organization-id '12345678' --no-apply >/dev/null
grep -q '^CONTENT_LINKEDIN_ORGANIZATION_ID=12345678$' "$tmp/.env"
grep -q '^CONTENT_LINKEDIN_ACCOUNT_TYPE=organization$' "$tmp/.env"
channels_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-channels)"
grep -q 'LinkedIn: automatic publishing ready' <<<"$channels_out"
# Aparat reports a stored session from either key and needs no chat id.
PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-aparat-check >/dev/null 2>&1 || true
printf 'CONTENT_APARAT_TOKEN=jwt-value\n' >> "$tmp/.env"
channels_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-channels)"
grep -q 'Aparat: session token stored' <<<"$channels_out"
if PATH="$tmp/bin:$PATH" "$tmp/manage.sh" content-connect-linkedin \
     --token 'x' --type team --no-apply >/dev/null 2>&1; then
  printf 'content-connect-linkedin must refuse an unknown account type\n' >&2
  exit 1
fi
# content-status prints the same lines beside the rest of the configuration.
status_root="$tmp/status-env"
mkdir -p "$status_root"
cp "$ROOT_DIR/manage.sh" "$status_root/manage.sh"
printf 'COMPOSE_PROFILES=content\n' > "$status_root/.env"
mkdir -p "$status_root/data/content-manager/config"
printf 'daily_proposal_time: "08:00"\n' \
  > "$status_root/data/content-manager/config/editorial-policy.yaml"
status_out="$(PATH="$tmp/bin:$PATH" "$status_root/manage.sh" content-status)"
grep -q 'Eitaa: not configured' <<<"$status_out"
grep -q 'LinkedIn: not configured' <<<"$status_out"

# The media host status resolves the public URL the same way the bot does:
# a pinned file first, then a stable environment value, then the tunnel log.
mkdir -p "$tmp/data/content-bot/tunnel"
printf 'INF Visit it at https://stale-name.trycloudflare.com\n' \
  > "$tmp/data/content-bot/tunnel/trycloudflared.log"
printf 'INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://old.trycloudflare.com\n' >> "$tmp/.env"
grep -q 'INSTAGRAM_MEDIA_PUBLIC_BASE_URL' "$tmp/.env"
status_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" instagram-media-status)"
grep -q 'https://stale-name.trycloudflare.com' <<<"$status_out"
grep -q 'not enabled' <<<"$status_out"

printf 'https://media.example.com/media\n' > "$tmp/data/content-bot/media-base-url.txt"
status_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" instagram-media-status)"
grep -q 'https://media.example.com/media' <<<"$status_out"

# Stopping only the tunnel must keep nginx's profile enabled.
printf 'COMPOSE_PROFILES=9router,ig-media,ig-media-quick\n' >> "$tmp/.env"
PATH="$tmp/bin:$PATH" "$tmp/manage.sh" instagram-media-tunnel-off >/dev/null
profiles_out="$(sed -n 's/^COMPOSE_PROFILES=//p' "$tmp/.env")"
grep -q 'ig-media' <<<"$profiles_out"
[[ ",$profiles_out," != *,ig-media-quick,* ]]

# nginx-only mode must not pull in a tunnel profile.
PATH="$tmp/bin:$PATH" "$tmp/manage.sh" instagram-media-enable --nginx-only >/dev/null
profiles_out="$(sed -n 's/^COMPOSE_PROFILES=//p' "$tmp/.env")"
grep -q 'ig-media' <<<"$profiles_out"
[[ ",$profiles_out," != *,ig-media-quick,* ]]

# --named without a token is refused, and --quick ignores the token branch.
if PATH="$tmp/bin:$PATH" "$tmp/manage.sh" instagram-media-enable --named >/dev/null 2>&1; then
  printf 'instagram-media-enable --named must fail without IG_MEDIA_TUNNEL_TOKEN\n' >&2
  exit 1
fi

# Media Studio status names the image endpoint and warns when it points at the
# stack chat gateway, which serves chat completions only: api-image jobs fail
# with HTTP 404 there.
sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=9router,content,media/' "$tmp/.env"
printf 'MEDIA_STUDIO_WRITER_BASE_URL=http://smart-router:8080/v1\nMEDIA_STUDIO_WRITER_MODEL=auto\n' >> "$tmp/.env"
media_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" media-status)"
grep -q 'Writer endpoint: http://smart-router:8080/v1' <<<"$media_out"
grep -q 'stack chat gateway' <<<"$media_out"
sed -i 's|^MEDIA_STUDIO_WRITER_BASE_URL=.*|MEDIA_STUDIO_WRITER_BASE_URL=https://images.example.com/v1|' "$tmp/.env"
media_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" media-status)"
grep -q 'Writer endpoint: https://images.example.com/v1' <<<"$media_out"
if grep -q 'stack chat gateway' <<<"$media_out"; then
  printf 'media-status must only warn for the chat gateway endpoint\n' >&2
  exit 1
fi

# Public routes: host names come from STACK_BASE_DOMAIN, a recorded public URL
# is read back as recorded, and a loopback bind is called out with the LAN
# target in the printed proxy block.
printf 'STACK_BASE_DOMAIN=stack.example.com\nPANEL_BIND_IP=192.168.1.50\nPANEL_PORT=8899\n' >> "$tmp/.env"
routes_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" domains)"
grep -q 'Base domain: stack.example.com' <<<"$routes_out"
grep -q 'panel.stack.example.com (suggested)' <<<"$routes_out"
grep -q 'PANEL_PUBLIC_URL=https://panel.stack.example.com' <<<"$routes_out"
grep -q 'reverse_proxy 192.168.1.50:8899' <<<"$routes_out"
grep -q 'SMART_ROUTER_PUBLIC_URL=https://sr.stack.example.com' <<<"$routes_out"
grep -q 'RUSTFS_CONSOLE_BIND_IP is loopback' <<<"$routes_out"
if grep -q 'reverse_proxy 127.0.0.1:9001' <<<"$routes_out"; then
  printf 'domains must print the LAN target for a loopback bind, not loopback\n' >&2
  exit 1
fi
grep -q 'S3_PUBLIC_CONSOLE_URL=https://console.stack.example.com/rustfs/console' <<<"$routes_out"
printf 'SMART_ROUTER_PUBLIC_URL=https://sr.stack.example.com\n' >> "$tmp/.env"
routes_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" domains)"
grep -q 'sr.stack.example.com (recorded)' <<<"$routes_out"
grep -q 'SMART_ROUTER_PUBLIC_URL=https://sr.stack.example.com' <<<"$routes_out"

printf 'manage UX tests passed.\n'
