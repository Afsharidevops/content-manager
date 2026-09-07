#!/usr/bin/env bash
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "$HERE/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

pass=0
fail=0
ok() { printf 'ok - %s\n' "$1"; pass=$((pass+1)); }
not_ok() { printf 'not ok - %s\n' "$1" >&2; fail=$((fail+1)); }

assert_success() {
  local name="$1"; shift
  if "$@"; then ok "$name"; else not_ok "$name"; fi
}

assert_failure() {
  local name="$1"; shift
  if "$@"; then not_ok "$name"; else ok "$name"; fi
}

# 1. Syntax
assert_success "stack-ops shell syntax" bash -n "$SOURCE_ROOT/scripts/stack-ops.sh"

# 2. Fixture with a fake Docker CLI.
FIX="$TMP/repo"
mkdir -p "$FIX/scripts" "$FIX/data" "$TMP/bin"
cp "$SOURCE_ROOT/scripts/stack-ops.sh" "$FIX/scripts/stack-ops.sh"
printf '0.2.0-test\n' > "$FIX/VERSION"
printf 'COMPOSE_PROFILES=9router,n8n,open-webui\n' > "$FIX/.env"
printf 'services: {}\n' > "$FIX/docker-compose.yml"
chmod 600 "$FIX/.env"

cat > "$TMP/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == info ]]; then exit 0; fi
if [[ "${1:-}" == compose && "${2:-}" == version ]]; then exit 0; fi
if [[ "${1:-}" == inspect ]]; then
  fmt="${3:-}"; cid="${4:-}"
  case "$fmt" in
    '{{.Image}}') printf 'sha256:%s\n' "$cid" ;;
    '{{.Config.Image}}') printf 'example/%s:test\n' "$cid" ;;
    *) exit 64 ;;
  esac
  exit 0
fi
if [[ "${1:-}" == image && "${2:-}" == inspect ]]; then
  fmt="${4:-}"; image="${5:-}"
  case "$fmt" in
    '{{join .RepoDigests ","}}') printf 'example/%s@sha256:deadbeef\n' "${image#sha256:}" ;;
    '{{.Id}}') printf '%s\n' "$image" ;;
    *) printf '%s\n' "$image" ;;
  esac
  exit 0
fi
if [[ "${1:-}" == compose ]]; then
  shift
  while (($#)); do
    case "$1" in
      -f|--env-file) shift 2 ;;
      *) break ;;
    esac
  done
  case "${1:-} ${2:-} ${3:-} ${4:-}" in
    "config --services "*)
      printf '%s\n' nine-router n8n-init open-webui
      ;;
    "config --quiet "*)
      exit 0
      ;;
    "ps --all --format json")
      cat <<'JSON'
[
 {"Service":"nine-router","State":"running","Health":"healthy","ExitCode":0},
 {"Service":"n8n-init","State":"exited","Health":"","ExitCode":0},
 {"Service":"open-webui","State":"running","Health":"","ExitCode":0}
]
JSON
      ;;
    "ps -q "*)
      printf 'cid-default\n'
      ;;
    "ps -q nine-router"*) printf 'cid-nine-router\n' ;;
    "ps -q n8n-init"*) printf 'cid-n8n-init\n' ;;
    "ps -q open-webui"*) printf 'cid-open-webui\n' ;;
    "pause "*|"unpause "*) exit 0 ;;
    *)
      printf 'fake docker compose: unsupported args: %s\n' "$*" >&2
      exit 64
      ;;
  esac
  exit 0
fi
printf 'fake docker: unsupported args: %s\n' "$*" >&2
exit 64
DOCKER
chmod +x "$TMP/bin/docker"

export PATH="$TMP/bin:$PATH"

if output="$($FIX/scripts/stack-ops.sh health --json)" && python3 -c 'import json,sys; o=json.load(sys.stdin); assert o["ready"] is True; assert len(o["services"]) == 3' <<<"$output"; then
  ok "health --json accepts healthy, completed init, and running-without-healthcheck"
else
  not_ok "health --json accepts healthy, completed init, and running-without-healthcheck"
fi

if output="$($FIX/scripts/stack-ops.sh status --json)" && python3 -c 'import json,sys; o=json.load(sys.stdin); assert o["stack_version"] == "0.2.0-test"; assert len(o["services"]) == 3' <<<"$output"; then
  ok "status --json emits versioned machine-readable state"
else
  not_ok "status --json emits versioned machine-readable state"
fi

mkdir -p "$FIX/data/hermes"
printf 'test-config
' > "$FIX/data/hermes/config.yaml"
if archive="$($FIX/scripts/stack-ops.sh backup --destination "$TMP/backups" --no-pause --label test)"    && [[ -f "$archive" && -f "$archive.sha256" ]]    && (cd "$(dirname "$archive")" && sha256sum -c "$(basename "$archive.sha256")" >/dev/null)    && tar -tzf "$archive" | grep -q 'manifest.json'; then
  ok "backup creates archive, manifest, and valid checksum"
else
  not_ok "backup creates archive, manifest, and valid checksum"
fi

# 3. Archive validation accepts an ordinary stack backup layout.
mkdir -p "$TMP/good/data/hermes"
printf 'x=1\n' > "$TMP/good/.env"
printf 'hello\n' > "$TMP/good/data/hermes/config.yaml"
printf '{}\n' > "$TMP/good/manifest.json"
tar -czf "$TMP/good.tar.gz" -C "$TMP/good" .env data manifest.json
if STACK_OPS_LIB_ONLY=1 bash -c 'source "$1"; validate_tar_paths "$2"' _ "$FIX/scripts/stack-ops.sh" "$TMP/good.tar.gz"; then
  ok "backup archive path validation accepts normal layout"
else
  not_ok "backup archive path validation accepts normal layout"
fi

# 4. Path traversal is rejected.
python3 - "$TMP/evil.tar.gz" <<'PY'
import io, tarfile, sys
with tarfile.open(sys.argv[1], "w:gz") as tf:
    data=b"bad"
    info=tarfile.TarInfo("../escape")
    info.size=len(data)
    tf.addfile(info, io.BytesIO(data))
PY
if STACK_OPS_LIB_ONLY=1 bash -c 'source "$1"; validate_tar_paths "$2"' _ "$FIX/scripts/stack-ops.sh" "$TMP/evil.tar.gz" >/dev/null 2>&1; then
  not_ok "backup archive path traversal is rejected"
else
  ok "backup archive path traversal is rejected"
fi

# 5. Escaping symlink is rejected.
python3 - "$TMP/symlink.tar.gz" <<'PY'
import tarfile, sys
with tarfile.open(sys.argv[1], "w:gz") as tf:
    info=tarfile.TarInfo("data/link")
    info.type=tarfile.SYMTYPE
    info.linkname="../../etc"
    tf.addfile(info)
PY
if STACK_OPS_LIB_ONLY=1 bash -c 'source "$1"; validate_tar_paths "$2"' _ "$FIX/scripts/stack-ops.sh" "$TMP/symlink.tar.gz" >/dev/null 2>&1; then
  not_ok "backup archive escaping symlink is rejected"
else
  ok "backup archive escaping symlink is rejected"
fi

# 6. Version command is usable without Docker.
if output="$($FIX/scripts/stack-ops.sh version)" && grep -q '0.2.0-test' <<<"$output"; then
  ok "version command does not require Docker"
else
  not_ok "version command does not require Docker"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
