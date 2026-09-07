#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
VERSION_FILE="$ROOT_DIR/VERSION"
LOCK_FILE="$ROOT_DIR/stack.lock.json"
STATE_ROOT="$ROOT_DIR/data/stack-state"
RELEASE_STATE_DIR="$STATE_ROOT/releases"
BACKUP_DIR_DEFAULT="${HERMES_STACK_BACKUP_DIR:-$(dirname "$ROOT_DIR")/$(basename "$ROOT_DIR")-backups}"
OPS_LOCK="$STATE_ROOT/ops.lock"

DOCKER=()
FS_ADMIN=()
PAUSED=0
LOCK_FD=""

log() { printf '[stack-ops] %s\n' "$*" >&2; }
warn() { printf '[stack-ops] WARNING: %s\n' "$*" >&2; }
die() { printf '[stack-ops] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: ./manage.sh COMMAND [OPTIONS]

Operational-safety commands:
  version
      Show stack version and source revision.

  status --json
      Emit stable machine-readable service/container status.

  health [--json] [--wait SECONDS]
      Check all services selected by COMPOSE_PROFILES. Docker healthchecks are
      authoritative when present; one-shot *-init services are healthy after a
      successful exit.

  backup [--destination DIR] [--label NAME] [--no-pause]
         [--age-recipient RECIPIENT]
      Create a checksum-protected backup of .env and data/. Running containers
      are paused by default so SQLite/WAL files are copied from a stable point.

  backup-list [--destination DIR] [--json]
      List backups.

  restore ARCHIVE [--wait SECONDS] [--no-start]
      Validate and restore a backup. A pre-restore backup is created first.
      The current data directory is retained until configuration validation
      succeeds, then services are restarted and checked.

  update [--plan] [--wait SECONDS] [--no-backup] [--force]
      Pull and recreate selected services. Before updating, record current image
      IDs and create local rollback tags. On failed readiness, automatically
      restore the previous image set. Persistent data is never silently rolled
      backward; the pre-update backup is retained for explicit restore.

  rollback [STATE_ID] [--wait SECONDS]
      Roll back to the image set captured by a previous update. If STATE_ID is
      omitted, use the newest available update state.

  lock-images [--output FILE]
      Resolve configured image references to immutable repository digests and
      write stack.lock.json. Intended for release maintainers after compatibility
      testing, not as an automatic upgrade mechanism.

  verify-images [--lock FILE]
      Verify local image IDs against stack.lock.json.
USAGE
}

cleanup() {
  if (( PAUSED == 1 )); then
    compose unpause >/dev/null 2>&1 || true
    PAUSED=0
  fi
}
trap cleanup EXIT INT TERM

require_file() {
  [[ -f "$1" && ! -L "$1" ]] || die "required regular file is missing or unsafe: $1"
}

init_docker() {
  if ((${#DOCKER[@]})); then return 0; fi
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    die "cannot access the Docker daemon"
  fi
  "${DOCKER[@]}" compose version >/dev/null 2>&1 || die "docker compose plugin is required"
}

compose() {
  init_docker
  "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" "$@"
}

compose_with_override() {
  local override="$1"; shift
  init_docker
  "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" -f "$override" --env-file "$ENV_FILE" "$@"
}

ensure_configured() {
  require_file "$ENV_FILE"
  require_file "$ROOT_DIR/docker-compose.yml"
}

ensure_state_dirs() {
  mkdir -p "$STATE_ROOT" "$RELEASE_STATE_DIR"
  chmod 700 "$STATE_ROOT" "$RELEASE_STATE_DIR" 2>/dev/null || true
}

acquire_lock() {
  ensure_state_dirs
  if command -v flock >/dev/null 2>&1; then
    exec {LOCK_FD}>"$OPS_LOCK"
    flock -n "$LOCK_FD" || die "another stack operation is already running"
  else
    local lock_dir="${OPS_LOCK}.d"
    mkdir "$lock_dir" 2>/dev/null || die "another stack operation may already be running ($lock_dir exists)"
    trap 'rmdir "'"$lock_dir"'" 2>/dev/null || true; cleanup' EXIT INT TERM
  fi
}

require_fs_admin() {
  FS_ADMIN=()
  if (( EUID == 0 )); then return 0; fi
  command -v sudo >/dev/null 2>&1 || die "this operation must preserve service UID ownership; run as root or install/configure sudo"
  sudo -v || die "sudo authentication is required to preserve backup/restore ownership"
  FS_ADMIN=(sudo)
}

env_value() {
  local key="$1"
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || return 2
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n1
}

stack_version() {
  if [[ -f "$VERSION_FILE" ]]; then
    tr -d '[:space:]' < "$VERSION_FILE"
  elif command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$ROOT_DIR" describe --tags --always --dirty 2>/dev/null || printf 'development'
  else
    printf 'development'
  fi
}

source_revision() {
  if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true
  fi
}


normalize_compose_ps() {
  # Docker Compose versions differ: some emit one JSON array, others JSONL.
  python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    print("[]")
    raise SystemExit
try:
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        parsed = [parsed]
    print(json.dumps(parsed, separators=(",", ":")))
    raise SystemExit
except json.JSONDecodeError:
    pass
items = []
for line in raw.splitlines():
    line = line.strip()
    if line:
        items.append(json.loads(line))
print(json.dumps(items, separators=(",", ":")))
'
}

compose_ps_json() {
  local raw
  raw="$(compose ps --all --format json)"
  printf '%s' "$raw" | normalize_compose_ps
}

status_json() {
  ensure_configured
  local ps profiles version revision
  ps="$(compose_ps_json)"
  profiles="$(env_value COMPOSE_PROFILES)"
  version="$(stack_version)"
  revision="$(source_revision)"
  STACK_PS="$ps" STACK_PROFILES="$profiles" STACK_VERSION_VALUE="$version" STACK_REVISION="$revision" python3 -c '
import json, os
services=json.loads(os.environ["STACK_PS"])
print(json.dumps({
  "format": 1,
  "stack_version": os.environ.get("STACK_VERSION_VALUE") or None,
  "source_revision": os.environ.get("STACK_REVISION") or None,
  "compose_profiles": [x for x in os.environ.get("STACK_PROFILES", "").split(",") if x],
  "services": services,
}, indent=2, sort_keys=True))
'
}

health_snapshot() {
  local mode="${1:-text}" expected_file ps_file
  expected_file="$(mktemp)"
  ps_file="$(mktemp)"
  compose config --services > "$expected_file"
  compose_ps_json > "$ps_file"
  set +e
  python3 - "$expected_file" "$ps_file" "$mode" <<'PY'
import json, sys
expected_path, ps_path, mode = sys.argv[1:]
expected = [x.strip() for x in open(expected_path, encoding="utf-8") if x.strip()]
rows = json.load(open(ps_path, encoding="utf-8"))
by_service = {}
for row in rows:
    service = row.get("Service") or row.get("service")
    if service:
        by_service[service] = row

result = []
overall = True
for service in expected:
    row = by_service.get(service)
    one_shot = service.endswith("-init")
    if not row:
        status = "missing"
        ready = False
        state = "missing"
        health = ""
        exit_code = None
    else:
        state = str(row.get("State") or row.get("state") or "unknown").lower()
        health = str(row.get("Health") or row.get("health") or "").lower()
        exit_code = row.get("ExitCode", row.get("exitCode"))
        try:
            exit_num = int(exit_code) if exit_code not in (None, "") else None
        except (TypeError, ValueError):
            exit_num = None
        if one_shot:
            ready = (state in {"exited", "stopped"} and exit_num == 0) or state == "running"
            status = "completed" if ready and state != "running" else ("running" if ready else state)
        elif state != "running":
            ready = False
            status = state
        elif health in {"unhealthy", "starting"}:
            ready = False
            status = health
        elif health == "healthy":
            ready = True
            status = "healthy"
        else:
            ready = True
            status = "running"
    overall = overall and ready
    result.append({
        "service": service,
        "state": state,
        "health": health or None,
        "status": status,
        "ready": ready,
        "one_shot": one_shot,
        "exit_code": exit_code,
    })

payload = {"ready": overall, "services": result}
if mode == "json":
    print(json.dumps(payload, indent=2, sort_keys=True))
elif mode == "quiet":
    pass
else:
    print(f"{'SERVICE':32} {'STATUS':12} READY")
    for item in result:
        print(f"{item['service'][:32]:32} {item['status'][:12]:12} {'yes' if item['ready'] else 'no'}")
sys.exit(0 if overall else 1)
PY
  local rc=$?
  set -e
  rm -f -- "$expected_file" "$ps_file"
  return "$rc"
}

health_wait() {
  local seconds="$1" mode="${2:-text}" started now
  started="$(date +%s)"
  while true; do
    if health_snapshot quiet; then
      health_snapshot "$mode"
      return 0
    fi
    now="$(date +%s)"
    if (( now - started >= seconds )); then
      health_snapshot "$mode" || true
      return 1
    fi
    sleep 3
  done
}

image_manifest_json() {
  local tmp service cid image_id image_ref repo_digests
  tmp="$(mktemp)"
  while IFS= read -r service; do
    [[ -n "$service" ]] || continue
    cid="$(compose ps -q "$service" 2>/dev/null || true)"
    [[ -n "$cid" ]] || continue
    image_id="$("${DOCKER[@]}" inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
    image_ref="$("${DOCKER[@]}" inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null || true)"
    repo_digests=""
    if [[ -n "$image_id" ]]; then
      repo_digests="$("${DOCKER[@]}" image inspect --format '{{join .RepoDigests ","}}' "$image_id" 2>/dev/null || true)"
    fi
    printf '%s\t%s\t%s\t%s\n' "$service" "$image_ref" "$image_id" "$repo_digests" >> "$tmp"
  done < <(compose config --services)
  python3 - "$tmp" <<'PY'
import json, sys
items=[]
for line in open(sys.argv[1], encoding="utf-8"):
    service, ref, image_id, digests = line.rstrip("\n").split("\t",3)
    items.append({"service":service,"configured_image":ref,"image_id":image_id,
                  "repo_digests":[x for x in digests.split(",") if x]})
print(json.dumps(items, indent=2, sort_keys=True))
PY
  rm -f -- "$tmp"
}

pause_for_backup() {
  if compose ps -q | grep -q .; then
    log "pausing running containers for a consistent filesystem backup"
    compose pause >/dev/null
    PAUSED=1
  fi
}

resume_after_backup() {
  if (( PAUSED == 1 )); then
    compose unpause >/dev/null
    PAUSED=0
  fi
}

backup_create() {
  local destination="$BACKUP_DIR_DEFAULT" label="manual" pause=true age_recipient=""
  while (($#)); do
    case "$1" in
      --destination) [[ $# -ge 2 ]] || die "--destination requires a directory"; destination="$2"; shift 2 ;;
      --label) [[ $# -ge 2 ]] || die "--label requires a value"; label="$2"; shift 2 ;;
      --no-pause) pause=false; shift ;;
      --age-recipient) [[ $# -ge 2 ]] || die "--age-recipient requires a recipient"; age_recipient="$2"; shift 2 ;;
      *) die "unknown backup option: $1" ;;
    esac
  done
  [[ "$label" =~ ^[A-Za-z0-9._-]+$ ]] || die "backup label may contain only letters, digits, dot, underscore, and hyphen"
  if [[ -n "$age_recipient" ]]; then
    command -v age >/dev/null 2>&1 || die "age is required for --age-recipient"
  fi
  ensure_configured
  compose config --quiet
  mkdir -p "$destination"
  chmod 700 "$destination" 2>/dev/null || true
  local ts version tmp manifest archive checksum final_archive host profiles revision
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  version="$(stack_version)"
  revision="$(source_revision)"
  host="$(hostname 2>/dev/null || printf unknown)"
  profiles="$(env_value COMPOSE_PROFILES)"
  tmp="$(mktemp -d)"
  manifest="$tmp/manifest.json"
  archive="$destination/hermes-stack-${ts}-${label}.tar.gz"
  python3 - "$manifest" "$version" "$revision" "$host" "$profiles" "$label" <<'PY'
import json, os, sys, datetime
path, version, revision, host, profiles, label = sys.argv[1:]
payload = {
  "format": 1,
  "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
  "stack_version": version,
  "source_revision": revision or None,
  "hostname": host,
  "compose_profiles": [x for x in profiles.split(",") if x],
  "label": label,
}
json.dump(payload, open(path,"w",encoding="utf-8"), indent=2, sort_keys=True)
PY
  image_manifest_json > "$tmp/images.json"
  if [[ "$pause" == true ]]; then pause_for_backup; else warn "creating live backup without pausing containers"; fi
  local tar_args=(--numeric-owner --exclude='./data/stack-state/ops.lock' -czf "$archive" -C "$ROOT_DIR" .env data docker-compose.yml)
  [[ -f "$LOCK_FILE" ]] && tar_args+=(stack.lock.json)
  if (( EUID == 0 )); then
    tar "${tar_args[@]}" -C "$tmp" manifest.json images.json
  elif command -v sudo >/dev/null 2>&1 && sudo -v; then
    sudo tar "${tar_args[@]}" -C "$tmp" manifest.json images.json
    sudo chown "$(id -u):$(id -g)" "$archive"
  else
    warn "sudo is unavailable; attempting backup as the current user"
    tar "${tar_args[@]}" -C "$tmp" manifest.json images.json || {
      resume_after_backup
      die "backup could not read all stack data; run the command as root or with sudo available"
    }
  fi
  resume_after_backup
  chmod 600 "$archive"
  checksum="$(sha256sum "$archive" | awk '{print $1}')"
  printf '%s  %s\n' "$checksum" "$(basename "$archive")" > "$archive.sha256"
  chmod 600 "$archive.sha256"
  final_archive="$archive"
  if [[ -n "$age_recipient" ]]; then
    if ! age -r "$age_recipient" -o "$archive.age" "$archive"; then
      rm -f -- "$archive.age"
      die "age encryption failed; plaintext backup was retained at $archive"
    fi
    chmod 600 "$archive.age"
    rm -f -- "$archive" "$archive.sha256"
    checksum="$(sha256sum "$archive.age" | awk '{print $1}')"
    printf '%s  %s\n' "$checksum" "$(basename "$archive.age")" > "$archive.age.sha256"
    chmod 600 "$archive.age.sha256"
    final_archive="$archive.age"
  fi
  rm -rf -- "$tmp"
  log "backup created: $final_archive"
  printf '%s\n' "$final_archive"
}

validate_tar_paths() {
  local archive="$1"
  python3 - "$archive" <<'PY'
import posixpath, sys, tarfile
archive = sys.argv[1]
allowed_files = {".env", "docker-compose.yml", "stack.lock.json", "manifest.json", "images.json"}

def clean(name: str) -> str:
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/")

def safe_path(name: str) -> bool:
    if not name or name.startswith("/"):
        return False
    normalized = posixpath.normpath(name)
    return normalized != ".." and not normalized.startswith("../") and "/../" not in f"/{normalized}/"

with tarfile.open(archive, "r:gz") as tf:
    for member in tf.getmembers():
        name = clean(member.name)
        if not name:
            continue
        if not safe_path(name):
            raise SystemExit(f"unsafe archive path: {name}")
        top = name.split("/", 1)[0]
        if top != "data" and name not in allowed_files:
            raise SystemExit(f"unexpected archive path: {name}")
        if member.ischr() or member.isblk() or member.isfifo():
            raise SystemExit(f"special device/fifo is not allowed in backup: {name}")
        if member.issym():
            target = member.linkname
            if target.startswith("/"):
                raise SystemExit(f"absolute symlink is not allowed: {name} -> {target}")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if not safe_path(resolved) or not (resolved == "data" or resolved.startswith("data/")):
                raise SystemExit(f"symlink escapes data/: {name} -> {target}")
        if member.islnk():
            target = clean(member.linkname)
            if not safe_path(target) or not (target == "data" or target.startswith("data/")):
                raise SystemExit(f"hardlink escapes data/: {name} -> {target}")
PY
}

decrypt_if_needed() {
  local input="$1" out_var="$2"
  if [[ "$input" == *.age ]]; then
    command -v age >/dev/null 2>&1 || die "age is required to restore this backup"
    local tmp_archive
    tmp_archive="$(mktemp --suffix=.tar.gz)"
    age -d -o "$tmp_archive" "$input"
    printf -v "$out_var" '%s' "$tmp_archive"
  else
    printf -v "$out_var" '%s' "$input"
  fi
}

restore_archive() {
  local input="${1:-}" wait_seconds=180 start=true
  [[ -n "$input" ]] || die "restore requires a backup archive"
  shift || true
  while (($#)); do
    case "$1" in
      --wait) [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || die "--wait requires seconds"; wait_seconds="$2"; shift 2 ;;
      --no-start) start=false; shift ;;
      *) die "unknown restore option: $1" ;;
    esac
  done
  ensure_configured
  [[ -f "$input" && ! -L "$input" ]] || die "backup archive is missing or unsafe"
  require_fs_admin
  acquire_lock
  local archive cleanup_archive=false checksum_file extract old_data old_env prebackup
  archive=""
  decrypt_if_needed "$input" archive
  [[ "$archive" == "$input" ]] || cleanup_archive=true
  checksum_file="$input.sha256"
  if [[ -f "$checksum_file" ]]; then
    (cd "$(dirname "$input")" && sha256sum -c "$(basename "$checksum_file")") || die "backup checksum verification failed"
  else
    warn "no checksum sidecar found for $(basename "$input")"
  fi
  validate_tar_paths "$archive" || die "backup contains unsafe paths"
  extract="$(mktemp -d)"
  "${FS_ADMIN[@]}" tar --numeric-owner -xzf "$archive" -C "$extract"
  "${FS_ADMIN[@]}" test -f "$extract/.env" && "${FS_ADMIN[@]}" test -d "$extract/data" || die "backup does not contain .env and data/"
  log "creating pre-restore safety backup"
  prebackup="$(backup_create --label pre-restore)"
  log "pre-restore backup: $prebackup"
  compose stop
  local ts="$(date -u +%Y%m%dT%H%M%SZ)"
  old_data="$ROOT_DIR/data.restore-old.$ts"
  old_env="$ROOT_DIR/.env.restore-old.$ts"
  "${FS_ADMIN[@]}" mv "$ROOT_DIR/data" "$old_data"
  "${FS_ADMIN[@]}" cp -a "$ENV_FILE" "$old_env"
  if "${FS_ADMIN[@]}" cp -a "$extract/data" "$ROOT_DIR/data" && "${FS_ADMIN[@]}" cp -a "$extract/.env" "$ENV_FILE"; then
    "${FS_ADMIN[@]}" chown "$(id -u):$(id -g)" "$ENV_FILE"
    "${FS_ADMIN[@]}" chmod 600 "$ENV_FILE"
  else
    "${FS_ADMIN[@]}" rm -rf -- "$ROOT_DIR/data" "$ENV_FILE"
    "${FS_ADMIN[@]}" mv "$old_data" "$ROOT_DIR/data"
    "${FS_ADMIN[@]}" mv "$old_env" "$ENV_FILE"
    die "restore copy failed; original state was put back"
  fi
  if ! compose config --quiet; then
    "${FS_ADMIN[@]}" rm -rf -- "$ROOT_DIR/data" "$ENV_FILE"
    "${FS_ADMIN[@]}" mv "$old_data" "$ROOT_DIR/data"
    "${FS_ADMIN[@]}" mv "$old_env" "$ENV_FILE"
    die "restored configuration is invalid; original state was put back"
  fi
  if [[ "$start" == true ]]; then
    compose up -d --remove-orphans
    if ! health_wait "$wait_seconds" text; then
      warn "restored services did not become ready; putting original state back"
      compose stop || true
      "${FS_ADMIN[@]}" rm -rf -- "$ROOT_DIR/data" "$ENV_FILE"
      "${FS_ADMIN[@]}" mv "$old_data" "$ROOT_DIR/data"
      "${FS_ADMIN[@]}" mv "$old_env" "$ENV_FILE"
      compose up -d --remove-orphans
      health_wait "$wait_seconds" text || warn "original services also failed readiness after rollback"
      die "restore failed readiness and was rolled back"
    fi
  fi
  "${FS_ADMIN[@]}" rm -rf -- "$old_data" "$old_env" "$extract"
  [[ "$cleanup_archive" == true ]] && rm -f -- "$archive"
  log "restore completed successfully"
}

capture_update_state() {
  local state_id="$1" state_dir="$RELEASE_STATE_DIR/$state_id" service cid image_id image_ref tag safe
  mkdir -p "$state_dir"
  chmod 700 "$state_dir" 2>/dev/null || true
  cp -a "$ENV_FILE" "$state_dir/env.before"
  sha256sum "$ROOT_DIR/docker-compose.yml" > "$state_dir/docker-compose.sha256"
  printf 'services:\n' > "$state_dir/rollback.override.yml"
  : > "$state_dir/images.tsv"
  while IFS= read -r service; do
    [[ -n "$service" && "$service" != *-init ]] || continue
    cid="$(compose ps -q "$service" 2>/dev/null || true)"
    [[ -n "$cid" ]] || continue
    image_id="$("${DOCKER[@]}" inspect --format '{{.Image}}' "$cid")"
    image_ref="$("${DOCKER[@]}" inspect --format '{{.Config.Image}}' "$cid")"
    safe="${service//[^A-Za-z0-9_.-]/-}"
    tag="hermes-stack-rollback/${safe}:${state_id}"
    "${DOCKER[@]}" image tag "$image_id" "$tag"
    printf '  %s:\n    image: %s\n' "$service" "$tag" >> "$state_dir/rollback.override.yml"
    printf '%s\t%s\t%s\t%s\n' "$service" "$image_ref" "$image_id" "$tag" >> "$state_dir/images.tsv"
  done < <(compose config --services)
  printf '%s' "$state_dir"
}

write_update_metadata() {
  local state_dir="$1" result="$2" backup_path="${3:-}" message="${4:-}" 
  python3 - "$state_dir/update.json" "$result" "$backup_path" "$message" "$(stack_version)" <<'PY'
import json, sys, datetime
path, result, backup, message, version = sys.argv[1:]
existing={}
try:
    existing=json.load(open(path, encoding="utf-8"))
except Exception:
    pass
payload={
  "format":1,
  "result":result,
  "backup":backup or existing.get("backup"),
  "message":message or None,
  "stack_version":version,
  "recorded_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
json.dump(payload, open(path,"w",encoding="utf-8"), indent=2, sort_keys=True)
PY
  chmod 600 "$state_dir/update.json" 2>/dev/null || true
}

rollback_state() {
  local state_id="${1:-}" wait_seconds="${2:-180}" state_dir override
  ensure_configured
  ensure_state_dirs
  if [[ -z "$state_id" ]]; then
    state_dir="$(find "$RELEASE_STATE_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -r | head -n1)"
    [[ -n "$state_dir" ]] || die "no rollback state is available"
    state_id="$state_dir"
  fi
  [[ "$state_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe rollback state id"
  state_dir="$RELEASE_STATE_DIR/$state_id"
  override="$state_dir/rollback.override.yml"
  require_file "$override"
  log "rolling back service images using state $state_id"
  compose_with_override "$override" up -d --remove-orphans
  if health_wait "$wait_seconds" text; then
    write_update_metadata "$state_dir" "rolled_back" "" "manual or automatic image rollback completed"
    log "image rollback completed"
    return 0
  fi
  write_update_metadata "$state_dir" "rollback_unhealthy" "" "services remained unhealthy after image rollback"
  warn "image rollback completed but readiness failed"
  warn "if the failed update migrated persistent data, restore the pre-update backup recorded in $state_dir/update.json"
  return 1
}

update_plan() {
  ensure_configured
  compose config --quiet
  printf 'Stack version: %s\n' "$(stack_version)"
  printf 'Profiles: %s\n' "$(env_value COMPOSE_PROFILES)"
  printf 'Selected services:\n'
  compose config --services | sed 's/^/  - /'
  printf '\nRunning image set:\n'
  image_manifest_json
  printf '\nUpdate behavior:\n'
  printf '  1. Require current readiness unless --force is used.\n'
  printf '  2. Create a consistent pre-update backup unless --no-backup is used.\n'
  printf '  3. Tag current local images for rollback.\n'
  printf '  4. Pull and recreate selected services.\n'
  printf '  5. Wait for Docker health/readiness.\n'
  printf '  6. Automatically restore the old image set if readiness fails.\n'
}

safe_update() {
  local wait_seconds=180 do_backup=true force=false plan=false
  while (($#)); do
    case "$1" in
      --plan) plan=true; shift ;;
      --wait) [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || die "--wait requires seconds"; wait_seconds="$2"; shift 2 ;;
      --no-backup) do_backup=false; shift ;;
      --force) force=true; shift ;;
      *) die "unknown update option: $1" ;;
    esac
  done
  ensure_configured
  if [[ "$plan" == true ]]; then update_plan; return 0; fi
  acquire_lock
  compose config --quiet
  if [[ "$force" != true ]] && ! health_snapshot text; then
    die "current stack is not ready; repair it first or use --force"
  fi
  local state_id state_dir backup_path="" rc
  state_id="$(date -u +%Y%m%dT%H%M%SZ)"
  state_dir="$(capture_update_state "$state_id")"
  if [[ "$do_backup" == true ]]; then
    backup_path="$(backup_create --label pre-update)"
  else
    warn "updating without a pre-update data backup"
  fi
  write_update_metadata "$state_dir" "updating" "$backup_path" "pulling new images"
  log "pulling selected images"
  if ! compose pull; then
    write_update_metadata "$state_dir" "pull_failed" "$backup_path" "docker compose pull failed"
    die "image pull failed; running services were not recreated"
  fi
  log "recreating selected services"
  if compose up -d --remove-orphans; then rc=0; else rc=$?; fi
  if (( rc == 0 )) && health_wait "$wait_seconds" text; then
    write_update_metadata "$state_dir" "success" "$backup_path" "update passed readiness checks"
    log "update completed successfully (state: $state_id)"
    return 0
  fi
  warn "update failed readiness; restoring previous image set"
  write_update_metadata "$state_dir" "failed_rolling_back" "$backup_path" "new image set failed readiness"
  if rollback_state "$state_id" "$wait_seconds"; then
    write_update_metadata "$state_dir" "rolled_back" "$backup_path" "new image set failed; previous image set restored"
    die "update failed; previous image set was restored. Pre-update backup: ${backup_path:-none}"
  fi
  write_update_metadata "$state_dir" "rollback_failed" "$backup_path" "both new and previous image sets failed readiness"
  die "update and image rollback both failed readiness. Restore backup if needed: ${backup_path:-none}"
}

value_or() {
  local key="$1" fallback="$2" value
  value="$(env_value "$key")"
  printf '%s' "${value:-$fallback}"
}

image_catalog() {
  printf 'NINEROUTER_IMAGE|%s:%s\n' "$(value_or NINEROUTER_IMAGE_REPOSITORY decolua/9router)" "$(value_or NINEROUTER_IMAGE_TAG latest)"
  printf 'HERMES_IMAGE|%s:%s
' "$(value_or HERMES_IMAGE_REPOSITORY nousresearch/hermes-agent)" "$(value_or HERMES_IMAGE_TAG latest)"
  printf 'SMART_ROUTER_IMAGE|%s:%s
' "$(value_or SMART_ROUTER_IMAGE_REPOSITORY afsharidevops/hermes-smart-router)" "$(value_or SMART_ROUTER_IMAGE_TAG latest)"
  printf 'OPENWEBUI_IMAGE|%s:%s
' "$(value_or OPENWEBUI_IMAGE_REPOSITORY ghcr.io/open-webui/open-webui)" "$(value_or OPENWEBUI_IMAGE_TAG main)"
  printf 'N8N_IMAGE|%s:%s
' "$(value_or N8N_IMAGE_REPOSITORY n8nio/n8n)" "$(value_or N8N_IMAGE_TAG latest)"
  printf 'EXECUTION_BROKER_IMAGE|%s
' "$(value_or EXECUTION_BROKER_IMAGE afsharidevops/hermes-execution-broker:0.1.1@sha256:dc88519c8f87d0720e0666e081dc74cd867ea8d5b019d59af50ac44a72bb55ed)"
  printf 'EXECUTION_SANDBOX_IMAGE|%s
' "$(value_or EXECUTION_SANDBOX_IMAGE python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419)"
  printf 'CADDY_IMAGE|%s
' "$(value_or CADDY_IMAGE caddy:2-alpine)"
}

lock_images() {
  local output="$LOCK_FILE"
  while (($#)); do
    case "$1" in
      --output) [[ $# -ge 2 ]] || die "--output requires a file"; output="$2"; shift 2 ;;
      *) die "unknown lock-images option: $1" ;;
    esac
  done
  ensure_configured
  init_docker
  local tmp key ref image_id digests resolved
  tmp="$(mktemp)"
  while IFS='|' read -r key ref; do
    [[ -n "$ref" ]] || continue
    log "resolving $key ($ref)" >&2
    "${DOCKER[@]}" pull "$ref" >/dev/null
    image_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$ref")"
    digests="$("${DOCKER[@]}" image inspect --format '{{join .RepoDigests ","}}' "$ref" 2>/dev/null || true)"
    resolved="$(printf '%s' "$digests" | tr ',' '\n' | head -n1)"
    if [[ -z "$resolved" && "$ref" == *@sha256:* ]]; then resolved="$ref"; fi
    [[ -n "$resolved" ]] || warn "$key has no repository digest; recording image ID only"
    printf '%s\t%s\t%s\t%s\n' "$key" "$ref" "$resolved" "$image_id" >> "$tmp"
  done < <(image_catalog)
  python3 - "$tmp" "$output" "$(stack_version)" <<'PY'
import json, sys, datetime
src, out, version = sys.argv[1:]
images=[]
for line in open(src, encoding="utf-8"):
    key, requested, resolved, image_id = line.rstrip("\n").split("\t")
    images.append({"env_key":key,"requested":requested,"resolved":resolved or None,"image_id":image_id})
payload={"format":1,"stack_version":version,
         "generated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"images":images}
with open(out,"w",encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
  rm -f -- "$tmp"
  log "image lock written: $output"
}

verify_images() {
  local lock="$LOCK_FILE"
  while (($#)); do
    case "$1" in
      --lock) [[ $# -ge 2 ]] || die "--lock requires a file"; lock="$2"; shift 2 ;;
      *) die "unknown verify-images option: $1" ;;
    esac
  done
  require_file "$lock"
  init_docker
  local tmp rc=0
  tmp="$(mktemp)"
  python3 - "$lock" <<'PY' > "$tmp"
import json, sys
obj=json.load(open(sys.argv[1], encoding="utf-8"))
for item in obj.get("images",[]):
    print("\t".join([item.get("env_key", ""), item.get("resolved") or item.get("requested", ""), item.get("image_id", "")]))
PY
  printf '%-28s %-8s %s\n' IMAGE_KEY STATUS REFERENCE
  while IFS=$'\t' read -r key ref expected_id; do
    if [[ -z "$ref" ]]; then
      printf '%-28s %-8s %s\n' "$key" WARN "no resolvable reference"
      rc=1
      continue
    fi
    local actual_id
    actual_id="$("${DOCKER[@]}" image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)"
    if [[ -n "$actual_id" && "$actual_id" == "$expected_id" ]]; then
      printf '%-28s %-8s %s\n' "$key" OK "$ref"
    else
      printf '%-28s %-8s %s\n' "$key" MISMATCH "$ref"
      rc=1
    fi
  done < "$tmp"
  rm -f -- "$tmp"
  return "$rc"
}

backup_list() {
  local destination="$BACKUP_DIR_DEFAULT" mode=text
  while (($#)); do
    case "$1" in
      --destination) [[ $# -ge 2 ]] || die "--destination requires a directory"; destination="$2"; shift 2 ;;
      --json) mode=json; shift ;;
      *) die "unknown backup-list option: $1" ;;
    esac
  done
  mkdir -p "$destination"
  if [[ "$mode" == json ]]; then
    find "$destination" -maxdepth 1 -type f \( -name 'hermes-stack-*.tar.gz' -o -name 'hermes-stack-*.tar.gz.age' \) -printf '%T@\t%p\n' 2>/dev/null \
      | sort -nr | python3 -c 'import json,sys; print(json.dumps([{"path":l.rstrip().split("\t",1)[1]} for l in sys.stdin if "\t" in l], indent=2))'
  else
    find "$destination" -maxdepth 1 -type f \( -name 'hermes-stack-*.tar.gz' -o -name 'hermes-stack-*.tar.gz.age' \) -printf '%TY-%Tm-%Td %TH:%TM  %s bytes  %p\n' 2>/dev/null | sort -r
  fi
}

cmd_version() {
  local v rev
  v="$(stack_version)"; rev="$(source_revision)"
  printf 'Hermes Linux Stack %s\n' "$v"
  [[ -n "$rev" ]] && printf 'Revision: %s\n' "$rev"
  return 0
}

main() {
  local command="${1:-}"
  case "$command" in
    -h|--help|help|"") usage ;;
    version) cmd_version ;;
    status)
      shift
      [[ "${1:-}" == "--json" && $# -eq 1 ]] || die "stack-ops status currently supports only --json"
      status_json
      ;;
    health)
      shift
      ensure_configured
      local mode=text wait=0
      while (($#)); do
        case "$1" in
          --json) mode=json; shift ;;
          --wait) [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || die "--wait requires seconds"; wait="$2"; shift 2 ;;
          *) die "unknown health option: $1" ;;
        esac
      done
      if (( wait > 0 )); then health_wait "$wait" "$mode"; else health_snapshot "$mode"; fi
      ;;
    backup) shift; acquire_lock; backup_create "$@" ;;
    backup-list) shift; backup_list "$@" ;;
    restore) shift; restore_archive "$@" ;;
    update) shift; safe_update "$@" ;;
    rollback)
      shift
      acquire_lock
      local id="" wait=180
      if (($#)) && [[ "$1" != --* ]]; then id="$1"; shift; fi
      while (($#)); do
        case "$1" in
          --wait) [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || die "--wait requires seconds"; wait="$2"; shift 2 ;;
          *) die "unknown rollback option: $1" ;;
        esac
      done
      rollback_state "$id" "$wait"
      ;;
    lock-images) shift; acquire_lock; lock_images "$@" ;;
    verify-images) shift; verify_images "$@" ;;
    *) die "unknown stack-ops command: $command" ;;
  esac
}

if [[ "${STACK_OPS_LIB_ONLY:-0}" != 1 ]]; then
  main "$@"
fi
