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

printf 'https://media.locallab.ir/media\n' > "$tmp/data/content-bot/media-base-url.txt"
status_out="$(PATH="$tmp/bin:$PATH" "$tmp/manage.sh" instagram-media-status)"
grep -q 'https://media.locallab.ir/media' <<<"$status_out"

printf 'manage UX tests passed.\n'
