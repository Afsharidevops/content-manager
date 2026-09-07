#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

pass=0
fail=0
ok() { printf 'ok - %s\n' "$1"; pass=$((pass + 1)); }
not_ok() { printf 'not ok - %s\n' "$1" >&2; fail=$((fail + 1)); }

if bash -n "$SOURCE_ROOT/install.sh" "$SOURCE_ROOT/manage.sh"; then
  ok "installer and manager shell syntax"
else
  not_ok "installer and manager shell syntax"
fi

SOURCE_ROOT="$SOURCE_ROOT" python3 <<'PY'
import os
from pathlib import Path

root = Path(os.environ["SOURCE_ROOT"])
compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
install = (root / "install.sh").read_text(encoding="utf-8")
manage = (root / "manage.sh").read_text(encoding="utf-8")

assert "  hermes-init:\n" in compose
assert "- ./data/hermes/logs:/logs" in compose
assert "touch /logs/agent.log /logs/errors.log" in compose
assert "condition: service_completed_successfully" in compose
assert "compose up -d --force-recreate hermes" in manage
assert "compose up -d --no-deps --force-recreate hermes" not in manage
assert "Enable the built-in Hermes dashboard with generated username/password authentication?" in install
assert "Hermes dashboard username:" in install
assert "Hermes dashboard password" in install
assert "./manage.sh dashboard-access" in install
assert "Basic Auth" in install
assert "ssh -L" in install
assert "hash_hermes_dashboard_password()" in install
assert "dotenv_literal_quote()" in install
assert "dashboard-access)" in manage
assert "hermes_dashboard_access()" in manage
PY
if (( $? == 0 )); then
  ok "compose lifecycle and dashboard access model"
else
  not_ok "compose lifecycle and dashboard access model"
fi

FIX="$TMP/repo"
mkdir -p "$FIX/data/hermes/logs/nested"
cp "$SOURCE_ROOT/manage.sh" "$FIX/manage.sh"
chmod +x "$FIX/manage.sh"
printf '%s\n' 'COMPOSE_PROFILES=hermes' 'HERMES_UID=10001' 'HERMES_GID=10002' > "$FIX/.env"
printf 'root log\n' > "$FIX/data/hermes/logs/agent.log"
printf 'nested log\n' > "$FIX/data/hermes/logs/nested/errors.log"
chmod 755 "$FIX/data/hermes/logs" "$FIX/data/hermes/logs/nested"
chmod 644 "$FIX/data/hermes/logs/agent.log" "$FIX/data/hermes/logs/nested/errors.log"

before="$(stat -c '%u:%g:%a' "$FIX/data/hermes/logs/agent.log")"
if output="$($FIX/manage.sh migrate-hermes-permissions --dry-run)" \
  && [[ "$(stat -c '%u:%g:%a' "$FIX/data/hermes/logs/agent.log")" == "$before" ]] \
  && grep -q '\[dry-run\].*agent.log' <<<"$output"; then
  ok "migration dry-run reports without changing files"
else
  not_ok "migration dry-run reports without changing files"
fi

# Run the real migration as the current uid/gid so the fixture does not require root.
printf 'HERMES_UID=%s\nHERMES_GID=%s\nCOMPOSE_PROFILES=hermes\n' "$(id -u)" "$(id -g)" > "$FIX/.env"
if output="$($FIX/manage.sh migrate-hermes-permissions)" \
  && [[ "$(stat -c '%a' "$FIX/data/hermes/logs")" == 700 ]] \
  && [[ "$(stat -c '%a' "$FIX/data/hermes/logs/nested")" == 700 ]] \
  && [[ "$(stat -c '%a' "$FIX/data/hermes/logs/agent.log")" == 600 ]] \
  && [[ "$(stat -c '%a' "$FIX/data/hermes/logs/nested/errors.log")" == 600 ]]; then
  ok "migration recursively repairs directory and file modes"
else
  not_ok "migration recursively repairs directory and file modes"
fi

if output="$($FIX/manage.sh migrate-hermes-permissions)" \
  && grep -q 'already correct' <<<"$output"; then
  ok "migration is idempotent"
else
  not_ok "migration is idempotent"
fi

if "$FIX/manage.sh" migrate-hermes-permissions unexpected >"$TMP/unknown.out" 2>&1; then
  not_ok "migration rejects unknown arguments"
elif grep -q '^Usage: ./manage.sh migrate-hermes-permissions' "$TMP/unknown.out"; then
  ok "migration rejects unknown arguments"
else
  not_ok "migration rejects unknown arguments"
fi

mv "$FIX/data/hermes/logs" "$FIX/data/hermes/real-logs"
ln -s real-logs "$FIX/data/hermes/logs"
if "$FIX/manage.sh" migrate-hermes-permissions >"$TMP/symlink.out" 2>&1; then
  not_ok "migration rejects a symlinked logs root"
elif grep -q 'Refusing unsafe Hermes logs symlink' "$TMP/symlink.out"; then
  ok "migration rejects a symlinked logs root"
else
  not_ok "migration rejects a symlinked logs root"
fi

# Test doctor check for incomplete dashboard auth
rm -rf "$FIX"
mkdir -p "$FIX/data/hermes/logs"
printf '%s\n' 'COMPOSE_PROFILES=hermes' 'HERMES_DASHBOARD=1' > "$FIX/.env"
cp "$SOURCE_ROOT/manage.sh" "$FIX/manage.sh"
chmod +x "$FIX/manage.sh"
if doctor_out="$("$FIX/manage.sh" doctor 2>&1)" \
  && grep -q 'HERMES_DASHBOARD is enabled but Basic Auth credentials are incomplete' <<<"$doctor_out"; then
  ok "doctor warns about incomplete dashboard auth"
else
  not_ok "doctor warns about incomplete dashboard auth"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
