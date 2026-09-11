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
    "pause "*|"unpause "*|"stop "*) exit 0 ;;
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

# 7. Section list is available without Docker and names the scoped paths.
if output="$($FIX/scripts/stack-ops.sh backup-sections)" \
   && grep -q '^env ' <<<"$output" \
   && grep -q '^panel *data/panel' <<<"$output" \
   && grep -q '^s3 *data/rustfs' <<<"$output" \
   && grep -q 'data/content-bot' <<<"$output"; then
  ok "backup-sections lists the scoped backup sections"
else
  not_ok "backup-sections lists the scoped backup sections"
fi

# A fake sudo keeps the restore path testable without root.
cat > "$TMP/bin/sudo" <<'SUDO'
#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "-v" || "${1:-}" == "-n" ]]; then exit 0; fi
exec "$@"
SUDO
chmod +x "$TMP/bin/sudo"

mkdir -p "$FIX/data/panel" "$FIX/data/content-bot/media" "$FIX/data/rustfs/data"
printf 'panel-token\n' > "$FIX/data/panel/token"
printf 'content-media\n' > "$FIX/data/content-bot/media/clip.txt"
printf 'object\n' > "$FIX/data/rustfs/data/object.bin"

# 8. A section backup contains only the selected paths and records them.
section_archive=""
if section_archive="$($FIX/scripts/stack-ops.sh backup --destination "$TMP/backups" --no-pause --label partial --only panel)" \
   && [[ -f "$section_archive" ]] \
   && tar -tzf "$section_archive" | grep -q 'data/panel/token' \
   && ! tar -tzf "$section_archive" | grep -q 'data/content-bot/' \
   && ! tar -tzf "$section_archive" | grep -qE '(^|/)\.env$' \
   && tar -xOzf "$section_archive" manifest.json | python3 -c 'import json,sys; m=json.load(sys.stdin); assert m["format"] == 2; assert m["full"] is False; assert m["sections"] == ["panel"]; assert m["paths"] == ["data/panel"]; assert m["source_root"]' \
   && python3 -c 'import json,sys,os; d=json.load(open(sys.argv[1])); assert d["full"] is False; assert d["sections"] == ["panel"]' "$section_archive.meta.json"; then
  ok "backup --only stores the selected section, manifest, and metadata"
else
  not_ok "backup --only stores the selected section, manifest, and metadata"
fi

# 8b. The bundled object storage server is a section of its own.
if s3_archive="$($FIX/scripts/stack-ops.sh backup --destination "$TMP/backups" --no-pause --only s3)" \
   && tar -tzf "$s3_archive" | grep -q 'data/rustfs/data/object.bin' \
   && ! tar -tzf "$s3_archive" | grep -q 'data/panel/'; then
  ok "backup --only s3 archives the RustFS data directory"
else
  not_ok "backup --only s3 archives the RustFS data directory"
fi

# 9. An unknown section name is rejected before any archive is written.
if $FIX/scripts/stack-ops.sh backup --destination "$TMP/backups" --no-pause --only nosuchsection >/dev/null 2>&1; then
  not_ok "backup --only rejects unknown sections"
else
  ok "backup --only rejects unknown sections"
fi

# 10. A partial restore replaces only the archived paths.
printf 'operator-edited\n' > "$FIX/data/panel/token"
printf 'untouched\n' > "$FIX/data/content-bot/media/clip.txt"
if "$FIX/scripts/stack-ops.sh" restore "$section_archive" --no-start >/dev/null 2>&1 \
   && [[ "$(cat "$FIX/data/panel/token")" == "panel-token" ]] \
   && [[ "$(cat "$FIX/data/content-bot/media/clip.txt")" == "untouched" ]] \
   && ! compgen -G "$FIX/restore-old.*" >/dev/null; then
  ok "partial restore restores its section and leaves other data alone"
else
  not_ok "partial restore restores its section and leaves other data alone"
fi

# 11. Restoring a backup from another root rewrites absolute host paths in .env.
mkdir -p "$TMP/portable"
cat > "$TMP/portable/.env" <<'ENVFILE'
COMPOSE_PROFILES=9router
EXECUTION_WORKSPACE_HOST_PATH=/old/root/data/execution-workspace
PANEL_STACK_PATH=/old/root
ENVFILE
printf '{"format":2,"full":false,"sections":["env"],"paths":[".env"],"source_root":"/old/root"}\n' > "$TMP/portable/manifest.json"
tar -czf "$TMP/portable.tar.gz" -C "$TMP/portable" .env manifest.json
if "$FIX/scripts/stack-ops.sh" restore "$TMP/portable.tar.gz" --no-start >/dev/null 2>&1 \
   && grep -q "^EXECUTION_WORKSPACE_HOST_PATH=$FIX/data/execution-workspace$" "$FIX/.env" \
   && grep -q "^PANEL_STACK_PATH=$FIX$" "$FIX/.env" \
   && grep -q '^COMPOSE_PROFILES=9router$' "$FIX/.env"; then
  ok "restore rewrites host paths when the archive came from another root"
else
  not_ok "restore rewrites host paths when the archive came from another root"
fi

# 12. backup-list reports the sections of every archive.
if output="$($FIX/scripts/stack-ops.sh backup-list --destination "$TMP/backups")" \
   && grep -q 'sections=panel' <<<"$output" \
   && output_json="$($FIX/scripts/stack-ops.sh backup-list --destination "$TMP/backups" --json)" \
   && python3 -c 'import json,sys; items=json.loads(sys.argv[1]); assert any(item.get("sections") == ["panel"] for item in items)' "$output_json"; then
  ok "backup-list reports the sections recorded in each archive"
else
  not_ok "backup-list reports the sections recorded in each archive"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
