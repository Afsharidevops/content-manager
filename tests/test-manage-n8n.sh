#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT

FAKE_BIN="$TEST_DIR/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/docker" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail

printf '%s\n' "$1" >> "${FAKE_DOCKER_LOG:?}"
if [[ "$1" == info ]]; then
  exit 0
fi
if [[ "$1" == compose ]]; then
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--env-file) shift 2 ;;
      *) break ;;
    esac
  done
  case "${1:-}" in
    exec)
      if [[ " $* " == *" PROVISION_N8N=true "* ]]; then
        printf 'N8N_API_KEY=fixture-router-key\n'
      fi
      ;;
    up|config|ps|run|logs|pull|stop|restart) ;;
    *) printf 'Unexpected fake Compose command: %s\n' "$*" >&2; exit 97 ;;
  esac
  exit 0
fi
if [[ "$1" != run ]]; then
  printf 'Unexpected fake Docker command: %s\n' "$*" >&2
  exit 98
fi

env_file=""
for ((index=1; index <= $#; index++)); do
  if [[ "${!index}" == --env-file ]]; then
    next=$((index + 1))
    env_file="${!next}"
  fi
done
[[ -n "$env_file" && -f "$env_file" ]] || {
  printf 'Fake Docker did not receive an environment file.\n' >&2
  exit 96
}
value() {
  sed -n "s/^$1=//p" "$env_file"
}
joined=" $* "
if [[ "$joined" == *hermes-n8n-token-validator* ]]; then
  case "$(value N8N_INSTANCE_MCP_TOKEN)" in
    eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature|opaque-fixture-token) exit 0 ;;
    *) exit 1 ;;
  esac
fi
if [[ "$joined" == *'/stack/scripts/bootstrap-n8n.mjs'* ]]; then
  printf 'reconcile mode=%s router=%s model=%s\n' \
    "$(value N8N_MCP_MODE)" "$(value N8N_ROUTER_BASE_URL)" "$(value N8N_CHAT_MODEL)" \
    >> "$FAKE_DOCKER_LOG"
  exit 0
fi
if [[ "$joined" == *'/stack/scripts/verify-n8n.mjs'* ]]; then
  count=0
  [[ -f "${FAKE_VERIFY_COUNT:?}" ]] && count="$(<"$FAKE_VERIFY_COUNT")"
  count=$((count + 1))
  printf '%s' "$count" > "$FAKE_VERIFY_COUNT"
  if [[ "${FAKE_VERIFY_FAIL_ONCE:-false}" == true && "$count" == 1 ]]; then
    exit 1
  fi
  exit 0
fi
printf 'Unexpected fake Docker run.\n' >&2
exit 95
SH
chmod 755 "$FAKE_BIN/docker"

new_fixture() {
  local name="$1" mode="${2:-off}" stack
  stack="$TEST_DIR/$name"
  mkdir -p "$stack/data/hermes" "$stack/data/stack-secrets" "$stack/scripts"
  : > "$stack/scripts/bootstrap-openwebui.mjs"
  cp "$ROOT_DIR/manage.sh" "$stack/manage.sh"
  chmod 755 "$stack/manage.sh"
  : > "$stack/docker-compose.yml"
  cat > "$stack/.env" <<EOF
COMPOSE_PROFILES=9router,hermes,n8n
N8N_MCP_MODE=$mode
N8N_IMAGE=n8nio/n8n:latest
EOF
  cat > "$stack/data/hermes/.env" <<'EOF'
N8N_TRIGGER_MCP_TOKEN=valid-trigger-token
N8N_INSTANCE_MCP_TOKEN=eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature
N8N_TRIGGER_MCP_URL="http://n8n:5678/mcp/hermes"
N8N_INSTANCE_MCP_URL="http://n8n:5678/mcp-server/http"
EOF
  chmod 600 "$stack/data/hermes/.env"
  cat > "$stack/data/hermes/config.yaml" <<EOF
model: test
mcp_servers:
  existing:
    url: "http://existing:1234/mcp"
EOF
  if [[ "$mode" == instance ]]; then
    cat >> "$stack/data/hermes/config.yaml" <<'EOF'
  # >>> hermes-stack n8n mcp (managed) >>>
  n8n:
    url: "${N8N_INSTANCE_MCP_URL}"
    headers:
      Authorization: "Bearer ${N8N_INSTANCE_MCP_TOKEN}"
  # <<< hermes-stack n8n mcp (managed) <<<
EOF
  elif [[ "$mode" == trigger ]]; then
    cat >> "$stack/data/hermes/config.yaml" <<'EOF'
  # >>> hermes-stack n8n mcp (managed) >>>
  n8n:
    url: "${N8N_TRIGGER_MCP_URL}"
    headers:
      Authorization: "Bearer ${N8N_TRIGGER_MCP_TOKEN}"
  # <<< hermes-stack n8n mcp (managed) <<<
EOF
  fi
  printf 'N8N_API_KEY=fixture-owner-key\n' > "$stack/data/stack-secrets/n8n-bootstrap.env"
  printf '{"version":1,"workflows":{"chat":{"id":"chat","name":"Hermes Hosted Chat (managed)","fingerprint":"fixture"}}}\n' \
    > "$stack/data/stack-secrets/n8n-bootstrap-state.json"
  chmod 700 "$stack/data/stack-secrets"
  chmod 600 "$stack/data/stack-secrets/"*
  printf '%s' "$stack"
}

run_manage() {
  local stack="$1" output="$2"
  shift 2
  PATH="$FAKE_BIN:$PATH" \
    FAKE_DOCKER_LOG="$stack/fake-docker.log" \
    FAKE_VERIFY_COUNT="$stack/fake-verify-count" \
    "$stack/manage.sh" "$@" > "$output" 2>&1
}

# Secrets supplied in argv are rejected before validation or storage.
stack="$(new_fixture argv-refusal off)"
if run_manage "$stack" "$stack/output" set-n8n-instance-mcp-token argv-secret; then
  printf 'Instance token supplied in argv unexpectedly passed.\n' >&2
  exit 1
fi
grep -q 'do not pass the Instance MCP token in argv' "$stack/output"
! grep -q 'argv-secret' "$stack/data/hermes/.env"

# A rejected token must not change the secret file. Token representation is not
# hard-coded: live MCP authentication is authoritative and future n8n releases may
# use JWT-like or opaque tokens. Valid tokens remain redacted from output.
stack="$(new_fixture token-validation off)"
remove_before="$(sha256sum "$stack/data/hermes/.env")"
if printf '%s\n' definitely-rejected-token | run_manage "$stack" "$stack/invalid.out" set-n8n-instance-mcp-token; then
  printf 'Rejected Instance token unexpectedly passed.\n' >&2
  exit 1
fi
test "$remove_before" = "$(sha256sum "$stack/data/hermes/.env")"
grep -q 'rejected the Instance MCP token' "$stack/invalid.out"
! grep -Fq definitely-rejected-token "$stack/invalid.out"
valid_instance_token='eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature'
printf '%s\n' "$valid_instance_token" | run_manage "$stack" "$stack/valid.out" set-n8n-instance-mcp-token
printf '%s\n' opaque-fixture-token | run_manage "$stack" "$stack/opaque.out" set-n8n-instance-mcp-token
grep -Fq 'N8N_INSTANCE_MCP_TOKEN=opaque-fixture-token' "$stack/data/hermes/.env"
! grep -Fq "N8N_INSTANCE_MCP_TOKEN=$valid_instance_token" "$stack/data/hermes/.env"
test "$(stat -c '%a' "$stack/data/hermes/.env")" = 600
! grep -Fq "$valid_instance_token" "$stack/valid.out"
! grep -Fq opaque-fixture-token "$stack/opaque.out"

# An active Instance-mode replacement retains the validated new token and emits
# precise recovery guidance if a later reconciliation stage fails.
stack="$(new_fixture active-token-failure instance)"
rm -f "$stack/data/stack-secrets/n8n-bootstrap.env"
if printf '%s\n' eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature | run_manage "$stack" "$stack/output" set-n8n-instance-mcp-token; then
  printf 'Active Instance token update without owner API key unexpectedly passed.\n' >&2
  exit 1
fi
grep -q '^N8N_INSTANCE_MCP_TOKEN=eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature$' "$stack/data/hermes/.env"
grep -q 'replacement token remains stored' "$stack/output"
grep -q 'reconciliation failed before Hermes was recreated' "$stack/output"
! grep -Fq eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature "$stack/output"

# Mode transitions preserve unrelated MCP servers and both tokens. Without Smart
# Router, hosted chat reconciliation must target 9router model ai.
stack="$(new_fixture token-validation off)"
printf '%s\n' eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature | run_manage "$stack" "$stack/valid.out" set-n8n-instance-mcp-token
: > "$stack/fake-docker.log"
if ! run_manage "$stack" "$stack/instance.out" set-n8n-mcp-mode instance; then
  printf 'Instance mode transition failed:\n' >&2
  command grep -v -F -e eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature -e valid-trigger-token "$stack/instance.out" >&2 || true
  exit 1
fi
grep -q '^N8N_MCP_MODE=instance$' "$stack/.env"
grep -q '^  existing:$' "$stack/data/hermes/config.yaml"
grep -q '^  n8n:$' "$stack/data/hermes/config.yaml"
grep -q 'N8N_INSTANCE_MCP_URL' "$stack/data/hermes/config.yaml"
grep -q '^N8N_TRIGGER_MCP_TOKEN=valid-trigger-token$' "$stack/data/hermes/.env"
grep -q '^N8N_INSTANCE_MCP_TOKEN=eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature$' "$stack/data/hermes/.env"
grep -q 'reconcile mode=instance router=http://nine-router:20128/v1 model=ai' "$stack/fake-docker.log"
run_manage "$stack" "$stack/trigger.out" set-n8n-mcp-mode trigger
grep -q '^N8N_MCP_MODE=trigger$' "$stack/.env"
grep -q 'N8N_TRIGGER_MCP_URL' "$stack/data/hermes/config.yaml"
run_manage "$stack" "$stack/off.out" set-n8n-mcp-mode off
grep -q '^N8N_MCP_MODE=off$' "$stack/.env"
grep -q '^  existing:$' "$stack/data/hermes/config.yaml"
! grep -q '^  n8n:$' "$stack/data/hermes/config.yaml"
grep -q '^N8N_TRIGGER_MCP_TOKEN=valid-trigger-token$' "$stack/data/hermes/.env"
grep -q '^N8N_INSTANCE_MCP_TOKEN=eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature$' "$stack/data/hermes/.env"
! grep -Fq eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature "$stack/instance.out"
! grep -Fq valid-trigger-token "$stack/trigger.out"

# Malformed markers fail closed after server reconciliation, restore every local
# file, and request restoration of the prior controllable publication state.
stack="$(new_fixture marker-failure off)"
printf '%s\n' '  # >>> hermes-stack n8n mcp (managed) >>>' >> "$stack/data/hermes/config.yaml"
env_before="$(sha256sum "$stack/.env")"
hermes_before="$(sha256sum "$stack/data/hermes/.env")"
config_before="$(sha256sum "$stack/data/hermes/config.yaml")"
if run_manage "$stack" "$stack/output" set-n8n-mcp-mode instance; then
  printf 'Malformed managed markers unexpectedly passed.\n' >&2
  exit 1
fi
test "$env_before" = "$(sha256sum "$stack/.env")"
test "$hermes_before" = "$(sha256sum "$stack/data/hermes/.env")"
test "$config_before" = "$(sha256sum "$stack/data/hermes/config.yaml")"
grep -q 'Hermes configuration update failed; prior files' "$stack/output"
grep -q 'reconcile mode=instance' "$stack/fake-docker.log"
grep -q 'reconcile mode=off' "$stack/fake-docker.log"

# If target verification fails once, local state and Trigger publication are
# rolled back and the prior mode is verified before the command reports failure.
stack="$(new_fixture verification-rollback off)"
if PATH="$FAKE_BIN:$PATH" \
  FAKE_DOCKER_LOG="$stack/fake-docker.log" \
  FAKE_VERIFY_COUNT="$stack/fake-verify-count" \
  FAKE_VERIFY_FAIL_ONCE=true \
  "$stack/manage.sh" set-n8n-mcp-mode instance > "$stack/output" 2>&1; then
  printf 'Injected mode verification failure unexpectedly passed.\n' >&2
  exit 1
fi
grep -q '^N8N_MCP_MODE=off$' "$stack/.env"
grep -q '^  existing:$' "$stack/data/hermes/config.yaml"
! grep -q '^  n8n:$' "$stack/data/hermes/config.yaml"
test "$(<"$stack/fake-verify-count")" = 2
grep -q 'reconcile mode=instance' "$stack/fake-docker.log"
grep -q 'reconcile mode=off' "$stack/fake-docker.log"
grep -q 'restored and verified' "$stack/output"
! grep -Fq eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature "$stack/output"
! grep -Fq valid-trigger-token "$stack/output"

# The Instance token can be removed only while its mode is inactive. Removal
# never changes n8n's global Instance-level MCP setting.
stack="$(new_fixture remove-instance-token off)"
run_manage "$stack" "$stack/output" remove-n8n-instance-mcp-token
! grep -q '^N8N_INSTANCE_MCP_TOKEN=' "$stack/data/hermes/.env"
grep -q '^N8N_TRIGGER_MCP_TOKEN=valid-trigger-token$' "$stack/data/hermes/.env"
grep -q 'Instance-level MCP in n8n was not disabled' "$stack/output"
stack="$(new_fixture refuse-active-removal instance)"
checksum="$(sha256sum "$stack/data/hermes/.env")"
if run_manage "$stack" "$stack/output" remove-n8n-instance-mcp-token; then
  printf 'Active Instance token removal unexpectedly passed.\n' >&2
  exit 1
fi
test "$checksum" = "$(sha256sum "$stack/data/hermes/.env")"
grep -q 'Switch to trigger or off' "$stack/output"

# A rejected transition must not perform legacy migration before preflight and
# must leave the original Hermes env byte-for-byte unchanged.
stack="$(new_fixture preflight-no-migration off)"
printf 'N8N_MCP_TOKEN=legacy-trigger-token\n' >> "$stack/data/hermes/.env"
rm -f "$stack/data/stack-secrets/n8n-bootstrap.env"
checksum="$(sha256sum "$stack/data/hermes/.env")"
if run_manage "$stack" "$stack/output" set-n8n-mcp-mode trigger; then
  printf 'Mode transition without owner key unexpectedly passed.\n' >&2
  exit 1
fi
test "$checksum" = "$(sha256sum "$stack/data/hermes/.env")"
! grep -q '^N8N_TRIGGER_MCP_TOKEN=legacy-trigger-token$' "$stack/data/hermes/.env"

# Refuse a symlinked stack-secret directory before chmod/chown can affect its
# target, even if the caller has elevated filesystem permissions.
stack="$(new_fixture secret-dir-symlink off)"
target="$TEST_DIR/secret-dir-target"
mkdir -p "$target"
rm -rf "$stack/data/stack-secrets"
ln -s "$target" "$stack/data/stack-secrets"
if printf '%s\n' eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJtY3Atc2VydmVyLWFwaSJ9.fixture-signature | run_manage "$stack" "$stack/output" set-n8n-instance-mcp-token; then
  printf 'Symlinked stack-secret directory unexpectedly passed.\n' >&2
  exit 1
fi
grep -q 'Refusing unsafe data/stack-secrets path' "$stack/output"

printf 'n8n management command tests passed.\n'
