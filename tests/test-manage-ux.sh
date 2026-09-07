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
printf 'manage UX tests passed.\n'
