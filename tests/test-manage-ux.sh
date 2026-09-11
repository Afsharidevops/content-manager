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

printf 'manage UX tests passed.\n'
