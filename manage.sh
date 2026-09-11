#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT_DIR/.env"
HERMES_ENV="$ROOT_DIR/data/hermes/.env"
STACK_SECRETS_DIR="$ROOT_DIR/data/stack-secrets"
N8N_BOOTSTRAP_ENV="$STACK_SECRETS_DIR/n8n-bootstrap.env"
N8N_BOOTSTRAP_STATE="$STACK_SECRETS_DIR/n8n-bootstrap-state.json"
OMNIROUTE_N8N_KEY_ENV="$STACK_SECRETS_DIR/omniroute-n8n-router.env"
HERMES_DASHBOARD_ACCESS_FILE="$STACK_SECRETS_DIR/hermes-dashboard-access.env"
TEMP_SECRET_FILES=()

# The operator panel mounts the repository at the identical host path so the
# Docker CLI inside the container resolves compose bind mounts exactly like a
# host invocation, and it writes .env/config files as the invoking user.
export PANEL_STACK_PATH="${PANEL_STACK_PATH:-$ROOT_DIR}"
export PANEL_RUN_AS="${PANEL_RUN_AS:-$(id -u):$(id -g)}"
if [[ -z "${PANEL_DOCKER_GID:-}" && -S /var/run/docker.sock ]]; then
  PANEL_DOCKER_GID="$(stat -c '%g' /var/run/docker.sock 2>/dev/null || printf '984')"
  export PANEL_DOCKER_GID
fi

cleanup_temp_secrets() {
  local file
  for file in "${TEMP_SECRET_FILES[@]}"; do
    rm -rf -- "$file"
  done
}
trap cleanup_temp_secrets EXIT

usage() {
  cat <<'EOF'
Hermes Linux Stack Manager v0.5.9

Usage:
  ./manage.sh                 Open the interactive manager
  ./manage.sh menu            Open the interactive manager
  ./manage.sh help            Show this grouped command reference

Interactive groups:
  services                    Containers, status, start/stop/restart, logs
  router                      Smart Router dashboard, routing, health, policy
  hermes                      Hermes Agent, Telegram, API and agent settings
  n8n                         n8n provisioning and MCP integration
  content                     Content Bot status, publishing docs and platform setup
  media                       Media Studio status, jobs, logs and reconfiguration
  execution                   Sandbox, Docker execution, SSH and approvals
  maintenance                 Update, backups, restore and rollback
  panel                       Operator console: status, config, logs, actions
  security                    Diagnostics, image integrity and access info

Common direct commands:
  status                      Show container status
  health [--json]             Show per-service health
  logs [SERVICE]              Follow logs (hermes/9router/omniroute/smart-router/webui/n8n/content/media/caddy/rustfs)
  doctor                      Run diagnostics and hardening checks
  migrate-hermes-permissions [--dry-run]
                              Repair Hermes log ownership/mode under data/hermes/logs
  dashboard-access [--show-password]
                              Hermes dashboard URL, username and optional password
  configure                   Re-run the interactive installer
  uninstall [--purge]         Remove containers; --purge also removes local runtime data

Smart Router automation:
  set-router-mode MODE        observe | route
  router-policy POLICY        heuristic | calibrated | learned
  router-status               Mode, policy, active features and URLs
  router-access [--show-secrets]
                              Dashboard/control URLs and local credentials
  router-summary [HOURS]      Authenticated telemetry summary
  router-routes               Route profiles
  router-provider-health      Provider/model health and circuit state
  router-system               Operations Center system/feature state
  router-info                 Runtime router information
  router-calibrate FILE       Build calibrated policy from labeled JSONL
  router-report FILE          Evaluate policy against labeled JSONL
  router-replay FILE [OUT]    Replay requests offline

n8n automation:
  n8n-status                  Provisioning/MCP status without secrets
  set-n8n-api-key             Store owner API key
  set-n8n-instance-mcp-token  Store/validate Instance MCP token
  set-n8n-mcp-mode MODE       instance | trigger | off
  bootstrap-n8n               Bootstrap/reconcile managed n8n objects
  reconcile-n8n               Reconcile managed n8n objects
  verify-n8n                  Verify hosted chat and MCP integration
  rotate-n8n-trigger-token    Rotate Trigger-mode bearer token

Content Bot automation:
  content-status              Content Bot configuration summary (no secrets)
  content-connect-instagram   Print the pending Instagram/Meta setup checklist
  content-configure           Reconfigure Content Bot settings (installer wizard)

Operator panel:
  panel-status                Panel URL, profile and token state (no secrets)
  panel-enable                Enable the panel profile and start the console
  panel-disable               Stop the panel and disable its profile
  panel-token                 Print the operator token (creates one if missing)
  panel-rotate-token          Replace the operator token and restart the panel
  panel-build                 Build the panel image locally from panel/Dockerfile

Shared object storage (S3):
  s3-status                   Backend, endpoints, bucket and per-service state (no secrets)
  s3-enable [--rustfs|--external] [--bind-ip IP]
                              Enable the shared S3 block and start the bundled RustFS server
  s3-disable                  Stop the bundled server and switch services back to local storage
  s3-verify [--create-bucket] Prove the endpoint, credentials and bucket with signed requests
  s3-keys [--show-secrets|--rotate]
                              Show or rotate the RustFS credentials shared with the stack
  s3-guide                    Public-domain route and external-provider checklist

Instagram media host (public address the Meta Graph API downloads media from):
  instagram-media-status      Show the public media URL and profile state
  instagram-media-enable [--nginx-only|--named|--quick]
                              Serve data/content-bot/media publicly (profile "ig-media")
  instagram-media-disable     Stop the public media host and disable its profile
  instagram-media-tunnel-off  Stop only the public tunnel (keep nginx for a proxy)
  instagram-media-verify      Check that the media URL is downloadable, including by Meta

Media Studio automation:
  media-status                Media Studio configuration summary (no secrets)
  media-guide                 Print the Media Studio setup and API guide pointer
  media-configure             Reconfigure Media Studio settings (installer wizard)

Backup and restore automation:
  backup [--only SECTION[,...]] [--destination DIR] [--label NAME]
                              Full-stack or per-section backup archive
  backup-sections             List the section names accepted by --only
  backup-list                 List archives with the sections they contain
  restore ARCHIVE [--no-start] [--no-relocate]
                              Restore a full or partial archive; host paths in
                              a restored .env are rewritten to this checkout

Content pipeline automation (Content Bot + Media Studio on one server):
  pipeline-status             Combined Content Bot, Media Studio, and API link status

Advanced commands remain backward compatible. Use the interactive groups for
normal administration; use direct commands for automation and scripts.
EOF
}

case "${1:-}" in
  -h|--help|help) usage; exit 0 ;;
  "") set -- menu ;;
esac

[[ -f "$ENV_FILE" ]] || {
  printf 'Not configured. Run ./install.sh first.\n' >&2
  exit 1
}

if [[ "${1:-}" == migrate-hermes-permissions ]]; then
  # This host-side recovery must remain usable while Docker or Hermes is down.
  DOCKER=(docker)
elif docker info >/dev/null 2>&1; then
  DOCKER=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
  DOCKER=(sudo docker)
else
  printf 'Cannot access the Docker daemon.\n' >&2
  exit 1
fi

compose() {
  "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" "$@"
}
ops() { "$ROOT_DIR/scripts/stack-ops.sh" "$@"; }

uninstall_stack() {
  local purge=false answer dir
  case "${1:-}" in
    "") ;;
    --purge) purge=true ;;
    *)
      printf 'Usage: ./manage.sh uninstall [--purge]\n' >&2
      return 2
      ;;
  esac

  if [[ "$purge" == true ]]; then
    printf '%s\n' 'WARNING: --purge permanently deletes local stack configuration, secrets, and runtime data.'
    printf '%s\n' 'The Git/source directory and backups stored outside this repository are kept.'
    read -r -p 'Type PURGE to continue: ' answer
    [[ "$answer" == PURGE ]] || { printf 'Uninstall cancelled.\n'; return 0; }
  else
    printf '%s\n' 'This removes stack containers and the Compose network.'
    printf '%s\n' 'Your .env, Hermes/n8n/OpenWebUI data, secrets, and source files will be kept.'
    read -r -p 'Continue? [y/N]: ' answer
    [[ "$answer" =~ ^[Yy]$ ]] || { printf 'Uninstall cancelled.\n'; return 0; }
  fi

  # Enable every Compose profile so services started under optional profiles are
  # included even if COMPOSE_PROFILES has changed since installation.
  compose --profile '*' down --remove-orphans
  printf 'Stack containers and network removed.\n'

  if [[ "$purge" == true ]]; then
    rm -f -- "$ENV_FILE"

    for dir in 9router omniroute caddy hermes n8n open-webui rustfs stack-secrets; do
      if [[ -d "$ROOT_DIR/data/$dir" ]]; then
        find "$ROOT_DIR/data/$dir" -mindepth 1 -maxdepth 1 ! -name '.gitkeep' -exec rm -rf -- {} +
      fi
    done

    rm -rf -- "$ROOT_DIR/data/smart-router"

    if [[ -d "$ROOT_DIR/data/stack-state" ]]; then
      find "$ROOT_DIR/data/stack-state" -mindepth 1 -maxdepth 1 ! -name 'ops.lock' -exec rm -rf -- {} +
      : > "$ROOT_DIR/data/stack-state/ops.lock"
    fi

    printf '%s\n' 'Local stack configuration, secrets, and runtime data purged.'
    printf '%s\n' 'Source files were kept. Run ./install.sh to install again.'
  else
    printf '%s\n' 'Local data was preserved. Run ./manage.sh start to bring the stack back.'
  fi
}

valid_ids() { [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]]; }

router_local_base_url() {
  local bind port
  bind="$(env_value "$ENV_FILE" SMART_ROUTER_BIND_IP)"; bind="${bind:-127.0.0.1}"
  port="$(env_value "$ENV_FILE" SMART_ROUTER_PORT)"; port="${port:-8787}"
  case "$bind" in
    0.0.0.0|'') bind=127.0.0.1 ;;
  esac
  printf 'http://%s:%s' "$bind" "$port"
}

router_api_get() {
  local path="$1" key="${2:-}" base
  base="$(router_local_base_url)"
  if command -v curl >/dev/null 2>&1; then
    if [[ -n "$key" ]]; then
      curl -fsS --connect-timeout 3 --max-time 10 -H "Authorization: Bearer $key" "$base$path"
    else
      curl -fsS --connect-timeout 3 --max-time 10 "$base$path"
    fi
  else
    # urllib fallback keeps management usable on minimal hosts.
    ROUTER_URL="$base$path" ROUTER_KEY="$key" python3 - <<'PYROUTER'
import os, urllib.request
req=urllib.request.Request(os.environ['ROUTER_URL'])
if os.environ.get('ROUTER_KEY'):
    req.add_header('Authorization','Bearer '+os.environ['ROUTER_KEY'])
with urllib.request.urlopen(req, timeout=10) as r:
    print(r.read().decode(), end='')
PYROUTER
  fi
}

router_api_request() {
  local method="$1" path="$2" key="${3:-}" data="${4:-}" base
  base="$(router_local_base_url)"
  if command -v curl >/dev/null 2>&1; then
    local args=(-fsS --connect-timeout 3 --max-time 10 -X "$method")
    [[ -n "$key" ]] && args+=(-H "Authorization: Bearer $key")
    if [[ -n "$data" ]]; then
      args+=(-H 'Content-Type: application/json' --data "$data")
    fi
    curl "${args[@]}" "$base$path"
  else
    ROUTER_URL="$base$path" ROUTER_KEY="$key" ROUTER_METHOD="$method" ROUTER_DATA="$data" python3 - <<'PYROUTER_MUTATE'
import os, urllib.request
data=os.environ.get('ROUTER_DATA','').encode() or None
req=urllib.request.Request(os.environ['ROUTER_URL'], data=data, method=os.environ['ROUTER_METHOD'])
if os.environ.get('ROUTER_KEY'):
    req.add_header('Authorization','Bearer '+os.environ['ROUTER_KEY'])
if data is not None:
    req.add_header('Content-Type','application/json')
with urllib.request.urlopen(req, timeout=10) as r:
    print(r.read().decode(), end='')
PYROUTER_MUTATE
  fi
}

router_runtime_set() {
  local json="$1" key response
  key="$(env_value "$ENV_FILE" SMART_ROUTER_ADMIN_API_KEY)"
  for _ in {1..30}; do
    if response="$(router_api_request PUT /control/api/system "$key" "$json" 2>/dev/null)"; then
      printf '%s' "$response"
      return 0
    fi
    sleep 1
  done
  return 1
}

pretty_json() {
  python3 -m json.tool 2>/dev/null || cat
}

menu_title() {
  printf '\n\033[1;36m%s\033[0m\n' "$1"
  printf '%s\n' '============================================================'
}

menu_pause() {
  [[ -r /dev/tty ]] || return 0
  read -r -p 'Press Enter to continue...' _ </dev/tty || true
}

services_menu() {
  local choice service
  while true; do
    menu_title 'Services & Logs'
    printf '%s\n' '1) Status                 Show all containers and ports'
    printf '%s\n' '2) Health                 Run v0.5.9 service health checks'
    printf '%s\n' '3) Start                  Start selected stack services'
    printf '%s\n' '4) Stop                   Stop running stack services'
    printf '%s\n' '5) Restart                Restart running stack services'
    printf '%s\n' '6) Follow logs            Choose one service or all'
    printf '%s\n' '0) Back'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1) "$ROOT_DIR/manage.sh" status; menu_pause ;;
      2) "$ROOT_DIR/manage.sh" health; menu_pause ;;
      3) "$ROOT_DIR/manage.sh" start; menu_pause ;;
      4) "$ROOT_DIR/manage.sh" stop; menu_pause ;;
      5) "$ROOT_DIR/manage.sh" restart; menu_pause ;;
      6)
        printf '%s\n' 'Log choices:'
        printf '%s\n' '  1) all          2) Hermes       3) backend gateway'
        printf '%s\n' '  4) Smart Router 5) Open WebUI   6) n8n          7) Caddy'
        printf '%s\n' '  8) Content Bot  9) Media Studio  10) RustFS'
        read -r -p 'Choose [1]: ' service
        case "${service:-1}" in
          1) "$ROOT_DIR/manage.sh" logs || true ;;
          2) "$ROOT_DIR/manage.sh" logs hermes || true ;;
          3)
            if [[ ",$(env_value "$ENV_FILE" COMPOSE_PROFILES)," == *,omniroute,* ]]; then
              "$ROOT_DIR/manage.sh" logs omniroute || true
            else
              "$ROOT_DIR/manage.sh" logs 9router || true
            fi
            ;;
          4) "$ROOT_DIR/manage.sh" logs smart-router || true ;;
          5) "$ROOT_DIR/manage.sh" logs webui || true ;;
          6) "$ROOT_DIR/manage.sh" logs n8n || true ;;
          7) "$ROOT_DIR/manage.sh" logs caddy || true ;;
          8) "$ROOT_DIR/manage.sh" logs content || true ;;
          9) "$ROOT_DIR/manage.sh" logs media || true ;;
          10) "$ROOT_DIR/manage.sh" logs rustfs || true ;;
          *) printf 'Unknown log choice.\n' >&2 ;;
        esac
        ;;
      0) return 0 ;;
      *) printf 'Unknown choice.\n' >&2 ;;
    esac
  done
}

router_menu() {
  local choice value file
  while true; do
    menu_title 'Hermes Smart Router v0.5.9'
    printf '%s\n' 'Observe & access'
    printf '%s\n' '  1) Status & URLs            Mode, policy, features and endpoints'
    printf '%s\n' '  2) Dashboard / Control      URLs and credential guidance'
    printf '%s\n' '  3) Telemetry summary         Choose 1h / 24h / 7d / 30d'
    printf '%s\n' '  4) Provider health           Health scores and circuit state'
    printf '%s\n' 'Routing'
    printf '%s\n' '  5) Route profiles            fast / standard / strong / coding / vision'
    printf '%s\n' '  6) Change mode               observe | route'
    printf '%s\n' '  7) Change policy             heuristic | calibrated | learned'
    printf '%s\n' '  8) Operations Center system  DB, HA, auth, OIDC and upstream state'
    printf '%s\n' 'Evaluate / tune'
    printf '%s\n' '  9) Router runtime info'
    printf '%s\n' ' 10) Calibrate from JSONL'
    printf '%s\n' ' 11) Evaluate/report JSONL'
    printf '%s\n' ' 12) Replay requests JSONL'
    printf '%s\n' '  0) Back'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1) "$ROOT_DIR/manage.sh" router-status; menu_pause ;;
      2) "$ROOT_DIR/manage.sh" router-access; menu_pause ;;
      3)
        printf '%s\n' 'Telemetry window: 1) 1 hour  2) 24 hours  3) 7 days  4) 30 days'
        read -r -p 'Choose [2]: ' value
        case "${value:-2}" in 1) value=1;; 2) value=24;; 3) value=168;; 4) value=720;; *) printf 'Unknown window.\n' >&2; continue;; esac
        "$ROOT_DIR/manage.sh" router-summary "$value"; menu_pause
        ;;
      4) "$ROOT_DIR/manage.sh" router-provider-health; menu_pause ;;
      5) "$ROOT_DIR/manage.sh" router-routes; menu_pause ;;
      6)
        printf '%s\n' 'Mode choices:'
        printf '%s\n' '  1) observe  Evaluate model=auto decisions but dispatch through the observe model'
        printf '%s\n' '  2) route    Apply route profiles to model=auto requests'
        printf '%s\n' 'Explicit upstream model names pass through in both modes.'
        read -r -p 'Choose [1]: ' value
        case "${value:-1}" in 1) value=observe;; 2) value=route;; *) printf 'Unknown mode.\n' >&2; continue;; esac
        "$ROOT_DIR/manage.sh" set-router-mode "$value"; menu_pause
        ;;
      7)
        printf '%s\n' 'Policy choices:'
        printf '%s\n' '  1) heuristic   Built-in deterministic policy; safest default'
        printf '%s\n' '  2) calibrated  Uses policy/calibrated.json from your labeled data'
        printf '%s\n' '  3) learned     Uses trained learned-routing artifacts with fallback'
        read -r -p 'Choose [1]: ' value
        case "${value:-1}" in 1) value=heuristic;; 2) value=calibrated;; 3) value=learned;; *) printf 'Unknown policy.\n' >&2; continue;; esac
        "$ROOT_DIR/manage.sh" router-policy "$value"; menu_pause
        ;;
      8) "$ROOT_DIR/manage.sh" router-system; menu_pause ;;
      9) "$ROOT_DIR/manage.sh" router-info; menu_pause ;;
      10)
        read -r -p 'Labeled calibration JSONL path: ' file
        [[ -n "$file" ]] && "$ROOT_DIR/manage.sh" router-calibrate "$file"
        menu_pause
        ;;
      11)
        read -r -p 'Labeled evaluation JSONL path: ' file
        [[ -n "$file" ]] && "$ROOT_DIR/manage.sh" router-report "$file"
        menu_pause
        ;;
      12)
        read -r -p 'Requests JSONL path: ' file
        [[ -n "$file" ]] && "$ROOT_DIR/manage.sh" router-replay "$file"
        menu_pause
        ;;
      0) return 0 ;;
      *) printf 'Unknown Smart Router choice.\n' >&2 ;;
    esac
  done
}

hermes_menu() {
  local choice value
  while true; do
    menu_title 'Hermes Agent & Telegram'
    printf '%s\n' 'Agent'
    printf '%s\n' '  1) Restart Hermes Agent'
    printf '%s\n' '  2) Set max agent turns        10-500; 90 recommended'
    printf '%s\n' '  3) Upstream terminal tools    enable | disable'
    printf '%s\n' '  4) Update backend API key'
    printf '%s\n' 'Telegram'
    printf '%s\n' '  5) Show allowed users'
    printf '%s\n' '  6) Add allowed user'
    printf '%s\n' '  7) Replace allowed users'
    printf '%s\n' 'Dashboard'
    printf '%s\n' '  8) Show dashboard access      URL and username'
    printf '%s\n' '  9) Reveal dashboard password Trusted terminal confirmation required'
    printf '%s\n' '0) Back'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1) "$ROOT_DIR/manage.sh" restart-hermes; menu_pause ;;
      2) read -r -p 'Max turns [90]: ' value; "$ROOT_DIR/manage.sh" set-agent-max-turns "${value:-90}"; menu_pause ;;
      3)
        printf '%s\n' '1) enable terminal/code_execution  2) disable terminal/code_execution'
        read -r -p 'Choose [2]: ' value
        case "${value:-2}" in 1) value=enable;; 2) value=disable;; *) printf 'Unknown choice.\n' >&2; continue;; esac
        "$ROOT_DIR/manage.sh" set-upstream-terminal "$value"; menu_pause
        ;;
      4)
        read -r -s -p 'New backend API key: ' value; printf '\n'
        [[ -n "$value" ]] && "$ROOT_DIR/manage.sh" set-backend-api-key "$value"
        menu_pause
        ;;
      5) "$ROOT_DIR/manage.sh" show-telegram-users; menu_pause ;;
      6) read -r -p 'Numeric Telegram user ID: ' value; "$ROOT_DIR/manage.sh" add-telegram-user "$value"; menu_pause ;;
      7) read -r -p 'Complete comma-separated ID list: ' value; "$ROOT_DIR/manage.sh" set-telegram-users "$value"; menu_pause ;;
      8) "$ROOT_DIR/manage.sh" dashboard-access; menu_pause ;;
      9) "$ROOT_DIR/manage.sh" dashboard-access --show-password; menu_pause ;;
      0) return 0 ;;
      *) printf 'Unknown Hermes choice.\n' >&2 ;;
    esac
  done
}

execution_menu() {
  local choice value feature name
  while true; do
    menu_title 'Execution, Sandbox & SSH'
    printf '%s\n' '1) Execution status'
    printf '%s\n' '2) Enable feature             sandbox | ssh | docker | all'
    printf '%s\n' '3) Disable feature            sandbox | ssh | docker | all'
    printf '%s\n' '4) Set execution users'
    printf '%s\n' '5) Add execution user'
    printf '%s\n' '6) Remove execution user'
    printf '%s\n' '7) Add SSH profile'
    printf '%s\n' '8) Verify SSH profile'
    printf '%s\n' '9) Change SSH profile password'
    printf '%s\n' '10) Remove SSH profile'
    printf '%s\n' '11) Configure approval bot token'
    printf '%s\n' '12) Rotate execution broker secret'
    printf '%s\n' '13) Enable Execution Admin UI'
    printf '%s\n' '14) Execution Admin status'
    printf '%s\n' '15) Show Execution Admin key (trusted terminal)'
    printf '%s\n' '16) Rotate Execution Admin key'
    printf '%s\n' '17) Disable Execution Admin UI'
    printf '%s\n' '18) PURGE execution state / SSH keys'
    printf '%s\n' '0) Back'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1) "$ROOT_DIR/manage.sh" execution-status; menu_pause ;;
      2|3)
        printf '%s\n' 'Feature: 1) sandbox  2) ssh  3) docker  4) all'
        read -r -p 'Choose [1]: ' value
        case "${value:-1}" in 1) feature=sandbox;; 2) feature=ssh;; 3) feature=docker;; 4) feature=all;; *) printf 'Unknown feature.\n' >&2; continue;; esac
        if [[ "$choice" == 2 ]]; then "$ROOT_DIR/manage.sh" enable-execution "$feature"; else "$ROOT_DIR/manage.sh" disable-execution "$feature"; fi
        menu_pause
        ;;
      4) read -r -p 'Comma-separated Telegram IDs: ' value; "$ROOT_DIR/manage.sh" set-execution-users "$value"; menu_pause ;;
      5) read -r -p 'Telegram ID: ' value; "$ROOT_DIR/manage.sh" add-execution-user "$value"; menu_pause ;;
      6) read -r -p 'Telegram ID: ' value; "$ROOT_DIR/manage.sh" remove-execution-user "$value"; menu_pause ;;
      7) read -r -p 'SSH profile name: ' name; "$ROOT_DIR/manage.sh" add-ssh-profile "$name"; menu_pause ;;
      8) read -r -p 'SSH profile name: ' name; "$ROOT_DIR/manage.sh" verify-ssh-profile "$name"; menu_pause ;;
      9) read -r -p 'SSH profile name: ' name; "$ROOT_DIR/manage.sh" set-ssh-profile-password "$name"; menu_pause ;;
      10) read -r -p 'SSH profile name: ' name; "$ROOT_DIR/manage.sh" remove-ssh-profile "$name"; menu_pause ;;
      11) "$ROOT_DIR/manage.sh" set-execution-approval-bot-token; menu_pause ;;
      12) "$ROOT_DIR/manage.sh" rotate-execution-broker-secret; menu_pause ;;
      13) "$ROOT_DIR/manage.sh" enable-execution-admin; menu_pause ;;
      14) "$ROOT_DIR/manage.sh" execution-admin-status; menu_pause ;;
      15) "$ROOT_DIR/manage.sh" show-execution-admin-key; menu_pause ;;
      16) "$ROOT_DIR/manage.sh" rotate-execution-admin-key; menu_pause ;;
      17) "$ROOT_DIR/manage.sh" disable-execution-admin; menu_pause ;;
      18) "$ROOT_DIR/manage.sh" purge-execution; menu_pause ;;
      0) return 0 ;;
      *) printf 'Unknown execution choice.\n' >&2 ;;
    esac
  done
}

maintenance_menu() {
  local choice value
  while true; do
    menu_title 'Maintenance, Backup & Recovery'
    printf '%s\n' '1) Update official images and recreate services'
    printf '%s\n' '2) Create backup'
    printf '%s\n' '3) Create a section backup (env, content, panel, ...)'
    printf '%s\n' '4) List backups'
    printf '%s\n' '5) Restore backup archive'
    printf '%s\n' '6) Roll back last update/state'
    printf '%s\n' '7) Version information'
    printf '%s\n' '0) Back'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1) read -r -p 'Pull and recreate selected services? [y/N]: ' value; [[ "$value" =~ ^[Yy]$ ]] && "$ROOT_DIR/manage.sh" update; menu_pause ;;
      2) "$ROOT_DIR/manage.sh" backup; menu_pause ;;
      3) "$ROOT_DIR/manage.sh" backup-sections; read -r -p 'Sections (comma separated): ' value; [[ -n "$value" ]] && "$ROOT_DIR/manage.sh" backup --only "$value"; menu_pause ;;
      4) "$ROOT_DIR/manage.sh" backup-list; menu_pause ;;
      5) read -r -p 'Backup archive path: ' value; [[ -n "$value" ]] && "$ROOT_DIR/manage.sh" restore "$value"; menu_pause ;;
      6) read -r -p 'State ID (Enter = latest): ' value; if [[ -n "$value" ]]; then "$ROOT_DIR/manage.sh" rollback "$value"; else "$ROOT_DIR/manage.sh" rollback; fi; menu_pause ;;
      7) "$ROOT_DIR/manage.sh" version; menu_pause ;;
      0) return 0 ;;
      *) printf 'Unknown maintenance choice.\n' >&2 ;;
    esac
  done
}

security_menu() {
  local choice
  while true; do
    menu_title 'Security & Integrity'
    printf '%s\n' '1) Doctor / diagnostics'
    printf '%s\n' '2) Lock current image digests'
    printf '%s\n' '3) Verify pinned image policy'
    printf '%s\n' '4) Smart Router access guidance'
    printf '%s\n' '5) Reveal Smart Router local secrets (confirmation required)'
    printf '%s\n' '0) Back'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1) "$ROOT_DIR/manage.sh" doctor; menu_pause ;;
      2) "$ROOT_DIR/manage.sh" lock-images; menu_pause ;;
      3) "$ROOT_DIR/manage.sh" verify-images; menu_pause ;;
      4) "$ROOT_DIR/manage.sh" router-access; menu_pause ;;
      5) "$ROOT_DIR/manage.sh" router-access --show-secrets; menu_pause ;;
      0) return 0 ;;
      *) printf 'Unknown security choice.\n' >&2 ;;
    esac
  done
}

uninstall_menu() {
  local value
  menu_title 'Uninstall'
  printf '%s\n' '1) Remove containers/network only'
  printf '%s\n' '   KEEP .env, secrets, Hermes/n8n/OpenWebUI data and source files.'
  printf '%s\n' '2) PURGE local runtime data and secrets'
  printf '%s\n' '   Source files and external backups are kept; PURGE confirmation is required.'
  printf '%s\n' '0) Cancel'
  read -r -p 'Choose [0]: ' value
  case "${value:-0}" in
    1) "$ROOT_DIR/manage.sh" uninstall ;;
    2) "$ROOT_DIR/manage.sh" uninstall --purge ;;
    0) ;;
    *) printf 'Unknown uninstall choice.\n' >&2 ;;
  esac
}

interactive_menu() {
  local choice
  while true; do
    menu_title 'Hermes Linux Stack Manager v0.5.9'
    printf '%s\n' 'Quick administration — choose a group; direct CLI commands still work.'
    printf '\n%s\n' '1) Overview & health          Status, health, version, diagnostics'
    printf '%s\n'   '2) Services & logs            Start/stop/restart and service logs'
    printf '%s\n'   '3) Smart Router               Routing, dashboard, profiles, policy, health'
    printf '%s\n'   '4) Hermes Agent & Telegram    Agent and messaging settings'
    printf '%s\n'   '5) n8n & MCP                  Provisioning, Instance MCP, Trigger MCP'
    printf '%s\n'   '6) Execution & SSH            Sandbox, Docker, SSH profiles, approvals'
    printf '%s\n'   '7) Content Bot                Status, publishing docs, platform setup'
    printf '%s\n'   '8) Media Studio               Media jobs, sessions and Google setup'
    printf '%s\n'   '9) Maintenance & recovery     Updates, backup, restore, rollback'
    printf '%s\n'   '10) Security & integrity      Doctor, image pins, access credentials'
    printf '%s\n'   '11) Reconfigure installation  Run the v0.5.9 wizard again'
    printf '%s\n'   '12) Operator panel            Web console for status, config, logs, actions'
    printf '%s\n'   '13) Object storage (S3)       RustFS or an external S3 endpoint'
    printf '%s\n'   '14) Uninstall                 Safe remove or explicit purge'
    printf '%s\n'   '0) Exit'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1)
        "$ROOT_DIR/manage.sh" status
        printf '\n'
        "$ROOT_DIR/manage.sh" health || true
        printf '\n'
        "$ROOT_DIR/manage.sh" version || true
        menu_pause
        ;;
      2) services_menu ;;
      3) router_menu ;;
      4) hermes_menu ;;
      5) n8n_menu ;;
      6) execution_menu ;;
      7) content_menu ;;
      8) media_menu ;;
      9) maintenance_menu ;;
      10) security_menu ;;
      11) exec "$ROOT_DIR/install.sh" ;;
      12) panel_menu ;;
      13) storage_menu ;;
      14) uninstall_menu ;;
      0) return 0 ;;
      *) printf 'Unknown choice.\n' >&2 ;;
    esac
  done
}

replace_env_value() {
  local file="$1" key="$2" value="$3" tmp
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || {
    printf 'Unsafe environment key: %s\n' "$key" >&2
    return 1
  }
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  if ! printf '%s' "$value" | python3 /dev/fd/3 "$file" "$key" 3<<'PY' > "$tmp"
import sys

path, key = sys.argv[1:]
value = sys.stdin.read()
lines = open(path, encoding="utf-8").read().splitlines()
indexes = [index for index, line in enumerate(lines) if line.startswith(f"{key}=")]
if len(indexes) > 1:
    raise SystemExit(f"Duplicate {key} entries are unsafe")
replacement = f"{key}={value}"
if indexes:
    lines[indexes[0]] = replacement
else:
    lines.append(replacement)
print("\n".join(lines) + "\n", end="")
PY
  then
    rm -f -- "$tmp"
    return 1
  fi
  chmod --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  chown --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  mv "$tmp" "$file"
}

remove_env_values() {
  local file="$1" tmp key pattern=""
  shift
  for key in "$@"; do
    [[ -z "$pattern" ]] || pattern+="|"
    pattern+="${key}="
  done
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  grep -v -E "^(${pattern})" "$file" > "$tmp" || true
  chmod --reference="$file" "$tmp"
  mv "$tmp" "$file"
}

n8n_mcp_mode() {
  local mode legacy
  mode="$(env_value "$ENV_FILE" N8N_MCP_MODE)" || return 1
  if [[ -z "$mode" ]]; then
    legacy="$(env_value "$HERMES_ENV" N8N_MCP_TOKEN)" || return 1
    if [[ -n "$legacy" ]]; then mode=trigger; else mode=off; fi
  fi
  [[ "$mode" == instance || "$mode" == trigger || "$mode" == off ]] || {
    printf 'N8N_MCP_MODE must be instance, trigger, or off.\n' >&2
    return 1
  }
  printf '%s' "$mode"
}

migrate_legacy_trigger_env() {
  local legacy
  legacy="$(env_value "$HERMES_ENV" N8N_MCP_TOKEN)" || return 1
  if [[ -n "$legacy" && -z "$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN)" ]]; then
    replace_env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN "$legacy"
  fi
  replace_env_value "$HERMES_ENV" N8N_TRIGGER_MCP_URL '"http://n8n:5678/mcp/hermes"'
  replace_env_value "$HERMES_ENV" N8N_INSTANCE_MCP_URL '"http://n8n:5678/mcp-server/http"'
}

finish_legacy_trigger_env_migration() {
  remove_env_values "$HERMES_ENV" N8N_MCP_URL N8N_MCP_PATH N8N_MCP_TOKEN
}

render_managed_n8n_mcp_entry() {
  local mode="$1"
  case "$mode" in
    instance) url_var=N8N_INSTANCE_MCP_URL; token_var=N8N_INSTANCE_MCP_TOKEN ;;
    trigger) url_var=N8N_TRIGGER_MCP_URL; token_var=N8N_TRIGGER_MCP_TOKEN ;;
    off) return 0 ;;
  esac
  printf '%s\n' \
    '  # >>> hermes-stack n8n mcp (managed) >>>' \
    '  n8n:' \
    "    url: \"\${$url_var}\"" \
    '    headers:' \
    "      Authorization: \"Bearer \${$token_var}\"" \
    '  # <<< hermes-stack n8n mcp (managed) <<<'
}

set_hermes_n8n_mcp_entry() {
  local mode="$1" file="$ROOT_DIR/data/hermes/config.yaml" tmp entry
  [[ -f "$file" ]] || { printf 'Hermes config is missing.\n' >&2; return 1; }
  entry="$(render_managed_n8n_mcp_entry "$mode")"
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  if ! python3 - "$file" "$mode" "$entry" > "$tmp" <<'PY'
import re
import sys

path, mode, entry = sys.argv[1:]
lines = open(path, encoding="utf-8").read().splitlines()
opens = [i for i, line in enumerate(lines) if line == "  # >>> hermes-stack n8n mcp (managed) >>>"]
closes = [i for i, line in enumerate(lines) if line == "  # <<< hermes-stack n8n mcp (managed) <<<"]
top_opens = [i for i, line in enumerate(lines) if line == "# >>> hermes-stack n8n mcp (managed) >>>"]
top_closes = [i for i, line in enumerate(lines) if line == "# <<< hermes-stack n8n mcp (managed) <<<"]
if len(opens) != len(closes) or len(top_opens) != len(top_closes) or len(opens) + len(top_opens) > 1:
    raise SystemExit("Hermes config has incomplete or duplicate managed n8n MCP markers")
replacement = entry.splitlines() if entry else []
if top_opens:
    start, end = top_opens[0], top_closes[0]
    block = ["mcp_servers:", *replacement] if replacement else []
    lines[start:end + 1] = block
elif opens:
    start, end = opens[0], closes[0]
    lines[start:end + 1] = replacement
elif replacement:
    roots = [i for i, line in enumerate(lines) if re.fullmatch(r"mcp_servers:\s*", line)]
    if len(roots) > 1:
        raise SystemExit("Duplicate top-level mcp_servers sections are unsafe")
    if roots:
        lines[roots[0] + 1:roots[0] + 1] = replacement
    else:
        if lines and lines[-1]: lines.append("")
        lines.extend(["mcp_servers:", *replacement])
print("\n".join(lines) + "\n", end="")
PY
  then
    rm -f -- "$tmp"
    return 1
  fi
  chmod --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  chown --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  mv "$tmp" "$file"
}

enable_hermes_execution_plugin() {
  local file="$ROOT_DIR/data/hermes/config.yaml" tmp
  [[ -f "$file" ]] || { printf 'Hermes config is missing.\n' >&2; return 1; }
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  if ! python3 - "$file" > "$tmp" <<'PY'
import re
import sys

lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
starts = [i for i, line in enumerate(lines) if re.fullmatch(r"plugins:\s*", line)]
if len(starts) > 1:
    raise SystemExit("Duplicate top-level plugins sections are unsafe")
if not starts:
    if lines and lines[-1]:
        lines.append("")
    lines.extend(["plugins:", "  enabled:", "    - stack-execution-policy"])
else:
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[^\s#]", lines[i])), len(lines))
    if not any(re.fullmatch(r"\s*-\s*stack-execution-policy\s*", line) for line in lines[start + 1:end]):
        enabled = next((i for i in range(start + 1, end) if re.fullmatch(r"  enabled:\s*", lines[i])), None)
        if enabled is None:
            lines[end:end] = ["  enabled:", "    - stack-execution-policy"]
        else:
            lines.insert(enabled + 1, "    - stack-execution-policy")
print("\n".join(lines) + "\n", end="")
PY
  then
    rm -f -- "$tmp"
    return 1
  fi
  chmod --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  chown --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  mv "$tmp" "$file"
}

restart_hermes() {
  compose up -d --force-recreate hermes
}

hermes_dashboard_url() {
  local bind port
  bind="$(env_value "$ENV_FILE" HERMES_BIND_IP)"; bind="${bind:-127.0.0.1}"
  port="$(env_value "$ENV_FILE" HERMES_DASHBOARD_PORT)"; port="${port:-9119}"
  [[ "$bind" != 0.0.0.0 ]] || bind=127.0.0.1
  printf 'http://%s:%s' "$bind" "$port"
}

hermes_dashboard_access() {
  local show_password="${1:-}" enabled username password answer mode
  [[ -z "$show_password" || "$show_password" == --show-password ]] || {
    printf 'Usage: ./manage.sh dashboard-access [--show-password]\n' >&2
    return 2
  }
  enabled="$(env_value "$ENV_FILE" HERMES_DASHBOARD)"
  [[ "$enabled" == 1 ]] || {
    printf 'Hermes dashboard is disabled. Run ./manage.sh configure to enable it.\n' >&2
    return 1
  }
  username="$(env_value "$ENV_FILE" HERMES_DASHBOARD_BASIC_AUTH_USERNAME)"
  printf 'Dashboard: %s\n' "$(hermes_dashboard_url)"
  printf 'Username:  %s\n' "${username:-NOT CONFIGURED}"
  if [[ -z "$show_password" ]]; then
    printf 'Use ./manage.sh dashboard-access --show-password only on a trusted terminal.\n'
    return 0
  fi
  [[ -r /dev/tty && -w /dev/tty ]] || {
    printf 'A controlling terminal is required to reveal the dashboard password.\n' >&2
    return 1
  }
  [[ -f "$HERMES_DASHBOARD_ACCESS_FILE" && ! -L "$HERMES_DASHBOARD_ACCESS_FILE" ]] || {
    printf 'Dashboard credential file is missing or unsafe; reconfigure Hermes to rotate credentials.\n' >&2
    return 1
  }
  mode="$(stat -c '%a' "$HERMES_DASHBOARD_ACCESS_FILE")"
  [[ "$mode" == 600 ]] || {
    printf 'Refusing to read dashboard credentials with unsafe mode %s; expected 600.\n' "$mode" >&2
    return 1
  }
  read -r -p 'Reveal the Hermes dashboard password on this terminal? [y/N]: ' answer </dev/tty
  [[ "$answer" =~ ^[Yy]$ ]] || { printf 'Password not shown.\n'; return 0; }
  password="$(env_value "$HERMES_DASHBOARD_ACCESS_FILE" HERMES_DASHBOARD_PASSWORD)"
  [[ -n "$password" ]] || { printf 'Dashboard password is missing; reconfigure Hermes to rotate credentials.\n' >&2; return 1; }
  printf 'Password:  %s\n' "$password"
}

hermes_uid_gid() {
  local uid gid
  uid="$(env_value "$ENV_FILE" HERMES_UID)"
  gid="$(env_value "$ENV_FILE" HERMES_GID)"
  uid="${uid:-10000}"
  gid="${gid:-10000}"
  [[ "$uid" =~ ^[1-9][0-9]*$ ]] || {
    printf 'HERMES_UID must be a non-root numeric uid in %s.\n' "$ENV_FILE" >&2
    return 1
  }
  [[ "$gid" =~ ^[1-9][0-9]*$ ]] || {
    printf 'HERMES_GID must be a non-root numeric gid in %s.\n' "$ENV_FILE" >&2
    return 1
  }
  printf '%s:%s' "$uid" "$gid"
}

hermes_permission_error() {
  printf 'Could not repair data/hermes/logs for the Hermes gateway uid.\n' >&2
  if [[ "$(id -u)" != 0 ]]; then
    printf 'Rerun with: sudo ./manage.sh migrate-hermes-permissions\n' >&2
  fi
  return 1
}

migrate_hermes_permissions() {
  local dry_run="$1" logs_dir desired uid gid changed=false path owner mode target_mode label
  logs_dir="$ROOT_DIR/data/hermes/logs"
  desired="$(hermes_uid_gid)" || return 1
  uid="${desired%%:*}"
  gid="${desired##*:}"

  if [[ -L "$logs_dir" ]]; then
    printf 'Refusing unsafe Hermes logs symlink: %s\n' "$logs_dir" >&2
    return 1
  fi

  if [[ ! -d "$logs_dir" ]]; then
    if [[ "$dry_run" == true ]]; then
      printf '[dry-run] would create %s with owner %s and mode 0700\n' "$logs_dir" "$desired"
      return 0
    fi
    install -d -m 0700 "$logs_dir" || hermes_permission_error
    chown "$uid:$gid" "$logs_dir" || hermes_permission_error
    changed=true
  fi

  while IFS= read -r -d '' path; do
    if [[ -d "$path" ]]; then
      target_mode=700
      label=directory
    else
      target_mode=600
      label=file
    fi
    owner="$(stat -c '%u:%g' "$path")"
    mode="$(stat -c '%a' "$path")"
    if [[ "$owner" != "$desired" ]]; then
      if [[ "$dry_run" == true ]]; then
        printf '[dry-run] would chown %s to %s (current %s)\n' "${path#$ROOT_DIR/}" "$desired" "$owner"
      else
        chown "$uid:$gid" "$path" || hermes_permission_error
        changed=true
      fi
    fi
    if [[ "$mode" != "$target_mode" ]]; then
      if [[ "$dry_run" == true ]]; then
        printf '[dry-run] would chmod %s %s to 0%s (current %s)\n' \
          "$label" "${path#$ROOT_DIR/}" "$target_mode" "$mode"
      else
        chmod "$target_mode" "$path" || hermes_permission_error
        changed=true
      fi
    fi
  done < <(find "$logs_dir" \( -type d -o -type f \) -print0)

  if [[ "$dry_run" == true ]]; then
    printf '[dry-run] checked %s for owner %s and restrictive modes\n' "$logs_dir" "$desired"
  elif [[ "$changed" == true ]]; then
    printf 'Hermes log permissions repaired under %s (owner %s, directories 0700, files 0600).\n' \
      "$logs_dir" "$desired"
  else
    printf 'Hermes log permissions already correct under %s.\n' "$logs_dir"
  fi
}

check_hermes_log_permissions() {
  local logs_dir="$ROOT_DIR/data/hermes/logs" desired issue_count=0 path owner mode expected_mode
  desired="$(hermes_uid_gid)" || return 1

  if [[ -L "$logs_dir" ]]; then
    printf 'WARNING: data/hermes/logs is an unsafe symlink. Replace it with a directory.\n'
    return 0
  fi
  if [[ ! -e "$logs_dir" ]]; then
    printf 'Hermes logs: not created yet (hermes-init will prepare it on startup)\n'
    return 0
  fi
  if [[ ! -d "$logs_dir" ]]; then
    printf 'WARNING: data/hermes/logs is not a directory.\n'
    printf '         Remove or relocate it, then run ./manage.sh migrate-hermes-permissions.\n'
    return 0
  fi

  while IFS= read -r -d '' path; do
    if [[ -d "$path" ]]; then expected_mode=700; else expected_mode=600; fi
    owner="$(stat -c '%u:%g' "$path")"
    mode="$(stat -c '%a' "$path")"
    if [[ "$owner" != "$desired" || "$mode" != "$expected_mode" ]]; then
      printf 'WARNING: %s has owner/mode %s/%s; expected %s/%s.\n' \
        "${path#$ROOT_DIR/}" "$owner" "$mode" "$desired" "$expected_mode"
      issue_count=$((issue_count + 1))
    fi
  done < <(find "$logs_dir" \( -type d -o -type f \) -print0)

  if (( issue_count > 0 )); then
    printf '         Run ./manage.sh migrate-hermes-permissions to repair data/hermes/logs.\n'
  else
    printf 'Hermes logs: owner/mode is compatible with Hermes gateway uid %s\n' "$desired"
  fi
}

# agent.max_turns in config.yaml is authoritative: the gateway bridges it into
# HERMES_MAX_ITERATIONS, which produces the "Iteration budget exhausted" notice.
AGENT_MAX_TURNS_MIN=10
AGENT_MAX_TURNS_MAX=500

hermes_agent_max_turns() {
  local file="$ROOT_DIR/data/hermes/config.yaml"
  [[ -f "$file" ]] || return 0
  python3 - "$file" <<'PY'
import re, sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
in_agent = False
for line in lines:
    if re.fullmatch(r"agent:\s*", line):
        in_agent = True
        continue
    if in_agent:
        if line and not line[0].isspace():
            in_agent = False
            continue
        match = re.fullmatch(r"\s+max_turns:\s*(\d+)\s*", line)
        if match:
            print(match.group(1))
            break
PY
}

set_hermes_agent_max_turns() {
  local value="$1" file="$ROOT_DIR/data/hermes/config.yaml" tmp
  [[ -f "$file" && ! -L "$file" ]] || {
    printf 'Hermes config is missing or unsafe.\n' >&2
    return 1
  }
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  if ! python3 - "$file" "$value" > "$tmp" <<'PY'
import re, sys
path, value = sys.argv[1:]
lines = open(path, encoding="utf-8").read().splitlines()
roots = [i for i, line in enumerate(lines) if re.fullmatch(r"agent:\s*", line)]
if len(roots) > 1:
    raise SystemExit("Duplicate top-level agent sections are unsafe")
if roots:
    start = roots[0]
    end = start + 1
    while end < len(lines) and (not lines[end] or lines[end][0].isspace()):
        end += 1
    body = lines[start + 1:end]
    replaced = False
    for i, line in enumerate(body):
        if re.fullmatch(r"(\s+)max_turns:\s*\d+\s*", line):
            indent = re.match(r"\s+", line).group(0)
            body[i] = f"{indent}max_turns: {value}"
            replaced = True
            break
    if not replaced:
        body.insert(0, f"  max_turns: {value}")
    lines[start + 1:end] = body
else:
    if lines and lines[-1]:
        lines.append("")
    lines.extend(["agent:", f"  max_turns: {value}"])
print("\n".join(lines) + "\n", end="")
PY
  then
    rm -f -- "$tmp"
    return 1
  fi
  chmod --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  chown --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  mv "$tmp" "$file"
}

# Upstream terminal/code_execution run as the gateway uid inside hermes-agent,
# which owns /opt/data/.env. Enabling them is a deliberate local trade of
# isolation for capability, so it lives behind an explicit command.
set_upstream_terminal() {
  local state="$1" file="$ROOT_DIR/data/hermes/config.yaml" tmp
  [[ -f "$file" && ! -L "$file" ]] || {
    printf 'Hermes config is missing or unsafe.\n' >&2
    return 1
  }
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  if ! python3 - "$file" "$state" > "$tmp" <<'PY'
import re, sys
path, state = sys.argv[1:]
lines = open(path, encoding="utf-8").read().splitlines()
roots = [i for i, line in enumerate(lines) if re.fullmatch(r"agent:\s*", line)]
if len(roots) > 1:
    raise SystemExit("Duplicate top-level agent sections are unsafe")
names = ("terminal", "code_execution")
if roots:
    start = roots[0]
    end = start + 1
    while end < len(lines) and (not lines[end] or lines[end][0].isspace()):
        end += 1
else:
    if lines and lines[-1]:
        lines.append("")
    lines.append("agent:")
    start, end = len(lines) - 1, len(lines)
# Drop any existing disabled_toolsets block, preserving every other agent key.
key = next((i for i in range(start + 1, end)
            if re.fullmatch(r"\s+disabled_toolsets:.*", lines[i])), None)
if key is not None:
    stop = key + 1
    while stop < end and re.fullmatch(r"\s+-\s*[A-Za-z0-9_-]+\s*", lines[stop]):
        stop += 1
    del lines[key:stop]
    end -= stop - key
block = ["  disabled_toolsets: []"] if state == "enabled" else \
        ["  disabled_toolsets:", *(f"    - {name}" for name in names)]
# Append after the section's last real key, not after the blank lines that
# separate it from the next section, so the file stays readable.
insert_at = end
while insert_at > start + 1 and not lines[insert_at - 1].strip():
    insert_at -= 1
lines[insert_at:insert_at] = block
print("\n".join(lines) + "\n", end="")
PY
  then
    rm -f -- "$tmp"
    return 1
  fi
  chmod --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  chown --reference="$file" "$tmp" || { rm -f -- "$tmp"; return 1; }
  mv "$tmp" "$file"
}

content_status() {
  local profiles token channel users writer model scheduler daily_time ig_id ig_url
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," != *,content,* ]]; then
    printf 'Content Bot is not enabled in COMPOSE_PROFILES. Run ./manage.sh content-configure to enable it.\n'
    return 1
  fi
  token="$(env_value "$ENV_FILE" CONTENT_BOT_TOKEN)"
  channel="$(env_value "$ENV_FILE" CONTENT_TELEGRAM_CHANNEL)"
  users="$(env_value "$ENV_FILE" CONTENT_TELEGRAM_USERS)"
  writer="$(env_value "$ENV_FILE" CONTENT_WRITER_BASE_URL)"
  model="$(env_value "$ENV_FILE" CONTENT_WRITER_MODEL)"
  scheduler="$(env_value "$ENV_FILE" CONTENT_SCHEDULER_ENABLED)"
  ig_id="$(env_value "$ENV_FILE" INSTAGRAM_BUSINESS_ID)"
  ig_url="$(ig_media_public_url)"
  daily_time="$(sed -n 's/^  daily_proposal_time: //p' \
    "$ROOT_DIR/data/content-manager/config/editorial-policy.yaml" 2>/dev/null | head -n1 | tr -d '"')"
  printf 'Content Bot status\n'
  if [[ -n "$token" ]]; then
    printf '  Telegram bot token: stored (secret not shown)\n'
  else
    printf '  Telegram bot token: NOT stored; reconfigure with ./manage.sh content-configure\n'
  fi
  printf '  Publish channel: %s\n' "${channel:-not configured}"
  printf '  Operator users: %s\n' "${users:-none}"
  printf '  Writer endpoint: %s\n' "${writer:-not configured}"
  printf '  Writer model: %s\n' "${model:-auto}"
  printf '  Daily scheduler: %s at %s\n' "${scheduler:-true}" "${daily_time:-08:00}"
  if [[ -n "$ig_id" && -n "$(env_value "$ENV_FILE" INSTAGRAM_ACCESS_TOKEN)" ]]; then
    printf '  Instagram: enabled (business %s, public media URL %s)\n' "$ig_id" "${ig_url:-not set}"
  else
    printf '  Instagram: not configured (set INSTAGRAM_BUSINESS_ID / INSTAGRAM_ACCESS_TOKEN)\n'
  fi
  printf '  Editorial policy: data/content-manager/config/editorial-policy.yaml\n'
  printf '  Discovery sources: data/content-manager/config/sources.yaml\n'
  printf '  Guide: docs/CONTENT-PRODUCTION-GUIDE.md\n'
}

content_connect_instagram() {
  printf '%s\n' 'Instagram/Meta setup checklist (official Graph API):'
  printf '%s\n' '  1. Convert the Instagram account to Business/Creator and link it to a Facebook Page.'
  printf '%s\n' '  2. Create a Meta Business app: https://developers.facebook.com/apps/creation/'
  printf '%s\n' '  3. Add the Instagram Graph API product and connect the Instagram account.'
  printf '%s\n' '  4. Grant instagram_basic and instagram_content_publish and generate a long-lived token.'
  printf '%s\n' 'Step-by-step guide: docs/INSTAGRAM-SETUP.md'
  printf '%s\n' 'Then set INSTAGRAM_BUSINESS_ID and INSTAGRAM_ACCESS_TOKEN in .env, give the media'
  printf '%s\n' 'files a public address with ./manage.sh instagram-media-enable (or pin a stable'
  printf '%s\n' 'hostname in data/content-bot/media-base-url.txt), and restart the bot:'
  printf '%s\n' '  ./manage.sh restart content'
}

content_configure() {
  printf 'Reconfiguring Content Bot settings. Existing components, data, and bind IPs are preserved.\n'
  exec "$ROOT_DIR/install.sh" --content-reconfigure
}

content_menu() {
  local choice
  while true; do
    printf '\nContent Bot Manager\n'
    printf '%s\n' '==================='
    printf '%s\n' '1) Show Content Bot status'
    printf '%s\n' '2) Instagram/Meta setup checklist'
    printf '%s\n' '3) Follow Content Bot logs'
    printf '%s\n' '4) Reconfigure Content Bot settings'
    printf '%s\n' '5) Instagram media host status'
    printf '%s\n' '6) Enable the Instagram media host'
    printf '%s\n' '0) Back'
    read -r -p 'Choose: ' choice
    case "$choice" in
      1) content_status || true ;;
      2) content_connect_instagram ;;
      3) compose logs -f --tail=100 content-bot ;;
      4) content_configure ;;
      5) ig_media_status || true ;;
      6) ig_media_enable || true ;;
      0) return 0 ;;
      *) printf 'Unknown choice.\n' >&2 ;;
    esac
  done
}

media_status() {
  local profiles drivers mode writer model token set freeze
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," != *,media,* ]]; then
    printf 'Media Studio is not enabled in COMPOSE_PROFILES. Run ./manage.sh media-configure to enable it.\n'
    return 1
  fi
  drivers="$(env_value "$ENV_FILE" MEDIA_STUDIO_DRIVERS)"
  mode="$(env_value "$ENV_FILE" MEDIA_STUDIO_SESSION_MODE)"
  writer="$(env_value "$ENV_FILE" MEDIA_STUDIO_WRITER_BASE_URL)"
  [[ -n "$writer" ]] || writer="$(env_value "$ENV_FILE" CONTENT_WRITER_BASE_URL)"
  token="$(env_value "$ENV_FILE" MEDIA_STUDIO_API_TOKEN)"
  freeze="$(env_value "$ENV_FILE" MEDIA_STUDIO_FREEZE_ON_READY)"
  printf 'Media Studio status\n'
  printf '  Enabled drivers: %s\n' "${drivers:-api-image,flow-video,video-edit}"
  printf '  Session mode: %s\n' "${mode:-cdp}"
  printf '  Writer endpoint: %s\n' "${writer:-not configured}"
  printf '  API token: %s\n' "$([[ -n "$token" ]] && printf 'stored (secret not shown)' || printf 'not set (localhost only)')"
  printf '  Flow freeze on ready: %s\n' "${freeze:-true}"
  printf '  Guide: docs/MEDIA-STUDIO.md\n'
  printf '  API base: http://127.0.0.1:%s (when enabled)\n' "$(env_value "$ENV_FILE" MEDIA_STUDIO_PORT | sed 's/^$/8850/')"
}

pipeline_status() {
  local profiles content_enabled media_enabled media_url media_token bot_token
  local image_driver video_driver media_drivers
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  content_enabled=false
  media_enabled=false
  [[ ",$profiles," == *,content,* ]] && content_enabled=true
  [[ ",$profiles," == *,media,* ]] && media_enabled=true
  if [[ "$content_enabled" != true && "$media_enabled" != true ]]; then
    printf 'The content pipeline is not installed here (neither content nor media is enabled).\n'
    printf 'Install both on one server with ./install.sh option 7, or enable them separately with\n'
    printf './manage.sh content-configure and ./manage.sh media-configure.\n'
    return 1
  fi
  printf '\nContent production pipeline\n'
  if [[ "$content_enabled" == true ]]; then
    content_status || true
  else
    printf '  Content Bot: not installed on this host (media-only server).\n'
  fi
  if [[ "$media_enabled" == true ]]; then
    media_status || true
  else
    printf '  Media Studio: not installed on this host (content-only server).\n'
  fi
  if [[ "$content_enabled" == true && "$media_enabled" == true ]]; then
    media_url="$(env_value "$ENV_FILE" CONTENT_MEDIA_STUDIO_URL)"
    [[ -n "$media_url" ]] || media_url="http://media-studio:8850"
    media_token="$(env_value "$ENV_FILE" MEDIA_STUDIO_API_TOKEN)"
    bot_token="$(env_value "$ENV_FILE" CONTENT_MEDIA_STUDIO_TOKEN)"
    image_driver="$(env_value "$ENV_FILE" CONTENT_MEDIA_IMAGE_DRIVER)"
    [[ -n "$image_driver" ]] || image_driver="api-image"
    video_driver="$(env_value "$ENV_FILE" CONTENT_MEDIA_VIDEO_DRIVER)"
    [[ -n "$video_driver" ]] || video_driver="flow-video"
    media_drivers="$(env_value "$ENV_FILE" MEDIA_STUDIO_DRIVERS)"
    media_drivers="${media_drivers//[[:space:]]/}"
    printf '\nContent Bot -> Media Studio link\n'
    printf '  Media Studio URL: %s\n' "$media_url"
    if [[ "$bot_token" == "$media_token" ]]; then
      if [[ -n "$bot_token" ]]; then
        printf '  API token: synced (secret not shown)\n'
      else
        printf '  API token: not set (localhost only)\n'
      fi
    else
      printf '  API token: NOT synced; run ./manage.sh media-configure after rotating the Media Studio token\n'
    fi
    printf '  Bot image driver: %s\n' "$image_driver"
    printf '  Bot video driver: %s\n' "$video_driver"
    if [[ -n "$media_drivers" ]]; then
      if [[ ",$media_drivers," != *",$image_driver,"* ]]; then
        printf '  WARNING: Media Studio drivers (%s) do not enable the bot image driver %s.\n' \
          "$media_drivers" "$image_driver"
      fi
      if [[ -n "$video_driver" && ",$media_drivers," != *",$video_driver,"* ]]; then
        printf '  WARNING: Media Studio drivers (%s) do not enable the bot video driver %s.\n' \
          "$media_drivers" "$video_driver"
      fi
    fi
  fi
}

media_guide() {
  printf '%s\n' 'Media Studio guide: docs/MEDIA-STUDIO.md'
  printf '%s\n' 'Flow unlock on a laptop only: docs/FLOW-UNLOCK-STANDALONE.md'
  printf '%s\n' 'Standalone extension folder: extensions/locallab-flow-unlock/ (load as an unpacked Chrome extension)'
  printf '%s\n' 'Reconfigure: ./manage.sh media-configure'
}

media_configure() {
  printf 'Reconfiguring Media Studio settings. Existing components, data, and bind IPs are preserved.\n'
  exec "$ROOT_DIR/install.sh" --media-reconfigure
}

media_menu() {
  local choice
  while true; do
    printf '\nMedia Studio Manager\n'
    printf '%s\n' '====================='
    printf '%s\n' '1) Show Media Studio status'
    printf '%s\n' '2) Print setup and API guide'
    printf '%s\n' '3) Follow Media Studio logs'
    printf '%s\n' '4) Check API health'
    printf '%s\n' '5) Reconfigure Media Studio settings'
    printf '%s\n' '0) Back'
    read -r -p 'Choose: ' choice
    case "$choice" in
      1) media_status || true ;;
      2) media_guide ;;
      3) compose logs -f --tail=100 media-studio ;;
      4)
        if [[ -z "$(compose ps -q media-studio)" ]]; then
          printf 'Media Studio container is not running. Start it with ./manage.sh start.\n'
        else
          compose exec -T media-studio python -c             'import json, urllib.request; print(json.load(urllib.request.urlopen("http://127.0.0.1:8850/healthz", timeout=5)))'             || printf 'Media Studio API did not answer on :8850.\n'
        fi
        ;;
      5) media_configure ;;
      0) return 0 ;;
      *) printf 'Unknown choice.\n' >&2 ;;
    esac
  done
}

# ------------------------------------------------------- Instagram media host

IG_MEDIA_LOG="$ROOT_DIR/data/content-bot/tunnel/trycloudflared.log"
IG_MEDIA_PIN="$ROOT_DIR/data/content-bot/media-base-url.txt"

ig_media_enabled() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  [[ ",$profiles," == *,ig-media,* ]]
}

ig_media_tunnel_url() {
  [[ -s "$IG_MEDIA_LOG" ]] || return 0
  grep -o 'https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com' "$IG_MEDIA_LOG" 2>/dev/null | tail -n1
}

ig_media_public_url() {
  local pinned stable tunnel
  pinned="$(grep -m1 -o 'https://[^[:space:]]*' "$IG_MEDIA_PIN" 2>/dev/null || true)"
  stable="$(env_value "$ENV_FILE" INSTAGRAM_MEDIA_PUBLIC_BASE_URL)"
  case "$stable" in
    *trycloudflare.com*) stable="" ;;
  esac
  [[ "$stable" == https://* ]] || stable=""
  tunnel="$(ig_media_tunnel_url || true)"
  printf '%s\n' "${pinned:-${stable:-$tunnel}}"
}

ig_media_add_profile() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  [[ ",$profiles," == *,ig-media,* ]] && return 0
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "${profiles:+$profiles,}ig-media"
  printf 'Enabled the "ig-media" compose profile.\n'
}

ig_media_add_quick_profile() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  [[ ",$profiles," == *,ig-media-quick,* ]] && return 0
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "${profiles:+$profiles,}ig-media-quick"
  printf 'Enabled the "ig-media-quick" compose profile.\n'
}

ig_media_add_named_profile() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  [[ ",$profiles," == *,ig-media-named,* ]] && return 0
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "${profiles:+$profiles,}ig-media-named"
  printf 'Enabled the "ig-media-named" compose profile.\n'
}

ig_media_remove_profile() {
  local profiles entry filtered=""
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  local entries=()
  IFS=',' read -r -a entries <<< "$profiles"
  for entry in "${entries[@]}"; do
    [[ -n "$entry" && "$entry" != ig-media && "$entry" != ig-media-named \
      && "$entry" != ig-media-quick ]] || continue
    filtered="${filtered:+$filtered,}$entry"
  done
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "$filtered"
  printf 'Disabled the "ig-media", "ig-media-quick", and "ig-media-named" compose profiles.\n'
}

ig_media_remove_tunnel_profiles() {
  local profiles entry filtered=""
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  local entries=()
  IFS=',' read -r -a entries <<< "$profiles"
  for entry in "${entries[@]}"; do
    [[ -n "$entry" && "$entry" != ig-media-named && "$entry" != ig-media-quick ]] || continue
    filtered="${filtered:+$filtered,}$entry"
  done
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "$filtered"
}

ig_media_named() {
  [[ -n "$(env_value "$ENV_FILE" IG_MEDIA_TUNNEL_TOKEN)" ]]
}

ig_media_status() {
  local bind port url pinned legacy
  bind="$(env_value "$ENV_FILE" IG_MEDIA_BIND_IP)"; bind="${bind:-127.0.0.1}"
  port="$(env_value "$ENV_FILE" IG_MEDIA_PORT)"; port="${port:-8099}"
  url="$(ig_media_public_url)"
  legacy="$(pgrep -f "http\.server.*--directory .*ig-medi[a]" 2>/dev/null | head -n1 || true)"
  printf 'Instagram media host\n'
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if ig_media_enabled; then
    if [[ ",$profiles," == *,ig-media-named,* ]]; then
      printf '  Profile "ig-media": enabled (named tunnel)\n'
    elif [[ ",$profiles," == *,ig-media-quick,* ]]; then
      printf '  Profile "ig-media": enabled (quick tunnel)\n'
    else
      printf '  Profile "ig-media": enabled (nginx only; reached through the proxy that fronts %s:%s)\n' \
        "$bind" "$port"
    fi
  else
    printf '  Profile "ig-media": not enabled; run ./manage.sh instagram-media-enable\n'
  fi
  printf '  Local address: %s:%s (serves data/content-bot/media read-only)\n' "$bind" "$port"
  printf '  Pinned URL file: %s\n' \
    "$([[ -s "$IG_MEDIA_PIN" ]] && printf 'data/content-bot/media-base-url.txt' || printf 'not set')"
  printf '  Public base URL: %s\n' "${url:-not set (Instagram publishing needs one)}"
  if [[ -n "$(env_value "$ENV_FILE" INSTAGRAM_MEDIA_PUBLIC_BASE_URL)" ]]; then
    printf '  Note: INSTAGRAM_MEDIA_PUBLIC_BASE_URL is set in .env; a quick tunnel value there is ignored\n'
  fi
  if [[ -n "$legacy" ]]; then
    printf '  WARNING: a manually started media host is still running (pid %s); stop it before enabling the profile\n' "$legacy"
  fi
  if ig_media_enabled; then
    compose --profile ig-media --profile ig-media-quick --profile ig-media-named ps \
      ig-media ig-media-tunnel ig-media-named-tunnel 2>/dev/null || true
  fi
  printf '  Guide: docs/INSTAGRAM-SETUP.md\n'
}

ig_media_enable() {
  local mode="auto" port legacy url waited=0 bind
  case "${1:-}" in
    --nginx-only) mode="nginx" ;;
    --named) mode="named" ;;
    --quick) mode="quick" ;;
    "") ;;
    *) printf 'Usage: ./manage.sh instagram-media-enable [--nginx-only|--named|--quick]\n' >&2; return 2 ;;
  esac
  port="$(env_value "$ENV_FILE" IG_MEDIA_PORT)"; port="${port:-8099}"
  bind="$(env_value "$ENV_FILE" IG_MEDIA_BIND_IP)"; bind="${bind:-127.0.0.1}"
  legacy="$(pgrep -f "http\.server.*--directory .*ig-medi[a]" 2>/dev/null | head -n1 || true)"
  if [[ -n "$legacy" ]]; then
    printf 'A manually started media host is running (pid %s).\n' "$legacy"
    printf 'Stop it first, for example: pkill -f "http.server.*--directory .*ig-media"\n'
    printf 'and stop the cloudflared process that points at 127.0.0.1:%s.\n' "$port"
    return 1
  fi
  install -d -m 0755 "$ROOT_DIR/data/content-bot/tunnel"
  if [[ "$mode" == nginx ]]; then
    ig_media_add_profile
    compose --profile ig-media up -d ig-media
    printf '\nMedia host started without a tunnel; it serves data/content-bot/media on %s:%s.\n' "$bind" "$port"
    printf 'Point the reverse proxy at http://%s:%s and set INSTAGRAM_MEDIA_PUBLIC_BASE_URL\n' "$bind" "$port"
    printf '(or data/content-bot/media-base-url.txt) to the public base it answers on.\n'
    return 0
  fi
  if [[ "$mode" == named ]] && ! ig_media_named; then
    printf 'IG_MEDIA_TUNNEL_TOKEN is empty; add the named tunnel token first or use --quick.\n' >&2
    return 1
  fi
  if ig_media_named && [[ "$mode" != quick ]]; then
    ig_media_add_profile
    ig_media_add_named_profile
    compose --profile ig-media --profile ig-media-named up -d ig-media ig-media-named-tunnel
    printf '\nNamed tunnel started; it keeps the hostname configured in Cloudflare.\n'
    printf 'Point INSTAGRAM_MEDIA_PUBLIC_BASE_URL or data/content-bot/media-base-url.txt at it\n'
    printf 'if that is not done yet, then check ./manage.sh instagram-media-status.\n'
    return 0
  fi
  ig_media_add_profile
  ig_media_add_quick_profile
  compose --profile ig-media up -d ig-media
  # A quick tunnel hostname is only trustworthy when it was written after this
  # start, so the log is rotated AND the tunnel is recreated: a running
  # cloudflared keeps appending to the file it already has open.
  if [[ -s "$IG_MEDIA_LOG" ]]; then
    mv "$IG_MEDIA_LOG" "$IG_MEDIA_LOG.previous"
  fi
  compose --profile ig-media --profile ig-media-quick up -d --force-recreate ig-media-tunnel
  printf '\nWaiting for the quick tunnel hostname'
  while (( waited < 60 )); do
    url="$(ig_media_tunnel_url)"
    if [[ -n "$url" ]]; then
      printf '\nInstagram media host is live.\n'
      printf '  Public base URL: %s/media\n' "$url"
      printf '  The Content Bot picks this up automatically; verify with ./manage.sh instagram-media-status\n'
      return 0
    fi
    printf '.'
    sleep 3
    waited=$(( waited + 3 ))
  done
  printf '\nThe tunnel did not report a hostname yet; check ./manage.sh logs ig-media-tunnel\n' >&2
  return 1
}

ig_media_verify() {
  local url file code
  url="$(ig_media_public_url)"
  [[ -n "$url" ]] || { printf 'No public media base URL is configured yet.\n' >&2; return 1; }
  file="$(find "$ROOT_DIR/data/content-bot/media" -maxdepth 1 -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) \
    -printf '%f\n' 2>/dev/null | head -n1)"
  [[ -n "$file" ]] || { printf 'No image under data/content-bot/media to test with.\n' >&2; return 1; }
  printf 'Public media URL under test: %s/media/%s\n' "$url" "$file"
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$url/media/$file" || true)"
  printf '  download from this host: %s\n' "${code:-no response}"
  [[ "$code" == 200 ]] || printf '  WARNING: the media URL did not answer 200 from here.\n'
  printf 'Asking the Instagram Graph API to download the same file (no publish):\n'
  python3 - "$ROOT_DIR" "$url/media/$file" <<'VERIFY'
import json, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

root = Path(sys.argv[1])
media_url = sys.argv[2]
env = {}
for line in (root / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
stored = {}
path = root / "data/content-bot/instagram-token.json"
if path.is_file():
    stored = json.loads(path.read_text(encoding="utf-8"))

token = stored.get("access_token") or env.get("INSTAGRAM_ACCESS_TOKEN", "")
account = env.get("INSTAGRAM_BUSINESS_ID", "")
api = env.get("INSTAGRAM_API_BASE", "https://graph.facebook.com").rstrip("/")
version = env.get("INSTAGRAM_API_VERSION", "v26.0")
if not token or not account:
    print("  skipped: INSTAGRAM_BUSINESS_ID and an access token are required")
    raise SystemExit(1)


def call(url, payload=None):
    data = urllib.parse.urlencode(payload).encode() if payload else None
    request = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        try:
            return json.loads(error.read().decode())
        except ValueError:
            return {"error": {"message": f"HTTP {error.code}"}}
    except Exception as error:  # noqa: BLE001 - surfaced to the operator
        return {"error": {"message": str(error)}}


created = call(
    f"{api}/{version}/{account}/media",
    {
        "image_url": media_url,
        "caption": "media connectivity check",
        "access_token": token,
    },
)
container = created.get("id")
if not container:
    message = (created.get("error") or {}).get("message", json.dumps(created)[:200])
    print(f"  the Graph API refused the media URL: {message}")
    raise SystemExit(1)

status = "IN_PROGRESS"
for _ in range(12):
    result = call(
        f"{api}/{version}/{container}"
        f"?fields=status_code&access_token={urllib.parse.quote(token)}"
    )
    status = result.get("status_code", status)
    if status != "IN_PROGRESS":
        break
    time.sleep(5)
print(f"  Instagram downloaded the file: {status}")
print("  the container is discarded and was never published")
raise SystemExit(0 if status == "FINISHED" else 1)
VERIFY
}

ig_media_tunnel_off() {
  # "ig-media" must stay on the profile list: the tunnel services depend on it.
  compose --profile ig-media --profile ig-media-quick --profile ig-media-named stop \
    ig-media-tunnel ig-media-named-tunnel >/dev/null 2>&1 || true
  ig_media_remove_tunnel_profiles
  printf 'Tunnel stopped. nginx keeps serving data/content-bot/media on %s:%s for a\n' \
    "$(env_value "$ENV_FILE" IG_MEDIA_BIND_IP | sed 's/^$/127.0.0.1/')" \
    "$(env_value "$ENV_FILE" IG_MEDIA_PORT | sed 's/^$/8099/')"
  printf 'reverse proxy or a named tunnel; run ./manage.sh instagram-media-enable to bring it back.\n'
}

ig_media_disable() {
  compose --profile ig-media --profile ig-media-quick --profile ig-media-named stop \
    ig-media-tunnel ig-media-named-tunnel ig-media >/dev/null 2>&1 || true
  ig_media_remove_profile
  printf 'Public media host stopped. data/content-bot/media is untouched.\n'
}

# ------------------------------------------------------------- operator panel

PANEL_DIR="$ROOT_DIR/data/panel"
PANEL_TOKEN_PATH="$PANEL_DIR/token"

panel_enabled() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  [[ ",$profiles," == *,panel,* ]]
}

panel_url() {
  local bind port
  bind="$(env_value "$ENV_FILE" PANEL_BIND_IP)"; bind="${bind:-127.0.0.1}"
  port="$(env_value "$ENV_FILE" PANEL_PORT)"; port="${port:-8899}"
  case "$bind" in 0.0.0.0|::|"[::]") bind=127.0.0.1 ;; esac
  printf 'http://%s:%s/\n' "$bind" "$port"
}

panel_token() {
  local token
  install -d -m 0700 "$PANEL_DIR"
  if [[ ! -s "$PANEL_TOKEN_PATH" ]]; then
    ( umask 077; random_hex 32 > "$PANEL_TOKEN_PATH" )
    printf 'Created a new operator token.\n' >&2
  fi
  chmod 600 "$PANEL_TOKEN_PATH" 2>/dev/null || true
  token="$(tr -d '[:space:]' < "$PANEL_TOKEN_PATH")"
  [[ -n "$token" ]] || { printf 'Panel token file is empty: %s\n' "$PANEL_TOKEN_PATH" >&2; return 1; }
  printf '%s\n' "$token"
}

panel_rotate_token() {
  install -d -m 0700 "$PANEL_DIR"
  ( umask 077; random_hex 32 > "$PANEL_TOKEN_PATH" )
  chmod 600 "$PANEL_TOKEN_PATH" 2>/dev/null || true
  compose up -d --no-deps --force-recreate panel >/dev/null 2>&1 || true
  printf 'Operator token rotated. Panel sessions are invalid; sign in again.\n'
}

panel_add_profile() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," == *,panel,* ]]; then
    return 0
  fi
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "${profiles:+$profiles,}panel"
  printf 'Enabled the "panel" compose profile.\n'
}

panel_remove_profile() {
  local profiles entry filtered=""
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  local entries=()
  IFS=',' read -r -a entries <<< "$profiles"
  for entry in "${entries[@]}"; do
    [[ -n "$entry" && "$entry" != panel ]] || continue
    filtered="${filtered:+$filtered,}$entry"
  done
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "$filtered"
  printf 'Disabled the "panel" compose profile.\n'
}

panel_status() {
  local actions secure
  if panel_enabled; then
    printf 'Operator panel: enabled (profile "panel")\n'
  else
    printf 'Operator panel: not enabled; run ./manage.sh panel-enable to start it\n'
  fi
  printf '  URL: %s\n' "$(panel_url)"
  printf '  Token: %s\n' "$([[ -s "$PANEL_TOKEN_PATH" ]] && printf 'stored at data/panel/token (secret not shown)' || printf 'not created yet; run ./manage.sh panel-token')"
  actions="$(env_value "$ENV_FILE" PANEL_ACTIONS_ENABLED)"
  printf '  Actions: %s\n' "${actions:-true}"
  secure="$(env_value "$ENV_FILE" PANEL_COOKIE_SECURE)"
  printf '  Secure cookie: %s\n' "${secure:-false}"
  if panel_enabled; then
    compose ps panel 2>/dev/null || true
  fi
  printf '  Bind: loopback only by default; set PANEL_BIND_IP for a trusted network or use a reverse proxy\n'
}

panel_enable() {
  panel_add_profile
  panel_token >/dev/null
  install -d -m 0700 "$PANEL_DIR/backups"
  # The panel mounts the backup directory to list and create archives.
  [[ -d "$ROOT_DIR-backups" ]] || install -d -m 0700 "$ROOT_DIR-backups"
  compose up -d panel
  printf '\nOperator panel: %s\n' "$(panel_url)"
  printf 'Sign in with the token printed by ./manage.sh panel-token\n'
}

panel_disable() {
  compose stop panel >/dev/null 2>&1 || true
  panel_remove_profile
  printf 'Containers stopped. The operator token stays in data/panel/token.\n'
}

panel_build() {
  local repository tag
  repository="$(env_value "$ENV_FILE" PANEL_IMAGE_REPOSITORY)"; repository="${repository:-afsharidevops/content-panel}"
  tag="$(env_value "$ENV_FILE" PANEL_IMAGE_TAG)"; tag="${tag:-0.3.0}"
  "${DOCKER[@]}" build -t "$repository:$tag" -f "$ROOT_DIR/panel/Dockerfile" "$ROOT_DIR"
  printf 'Built %s:%s from panel/Dockerfile\n' "$repository" "$tag"
}

panel_menu() {
  local choice
  while true; do
    printf '\nOperator Panel\n'
    printf '%s\n' '=============='
    printf '%s\n' '1) Enable and start the panel'
    printf '%s\n' '2) Show panel status and URL'
    printf '%s\n' '3) Show the operator token'
    printf '%s\n' '4) Rotate the operator token'
    printf '%s\n' '5) Follow panel logs'
    printf '%s\n' '6) Build the panel image locally'
    printf '%s\n' '7) Disable and stop the panel'
    printf '%s\n' '0) Back'
    read -r -p 'Choose: ' choice
    case "$choice" in
      1) panel_enable; menu_pause ;;
      2) panel_status; menu_pause ;;
      3) panel_token; menu_pause ;;
      4) panel_rotate_token; menu_pause ;;
      5) compose logs -f --tail=100 panel ;;
      6) panel_build; menu_pause ;;
      7) panel_disable; menu_pause ;;
      0) return 0 ;;
      *) printf 'Unknown choice.\n' >&2 ;;
    esac
  done
}

# --- Shared S3-compatible object storage -------------------------------------
# One endpoint backs every service that understands object storage: the
# bundled RustFS server (profile "rustfs") or any external S3-compatible
# provider. docs/S3-STORAGE.md documents the consumer matrix, the public
# domain route, and the external-provider checklist.

s3_data_dir() { printf '%s/data/rustfs' "$ROOT_DIR"; }

s3_backend() {
  local backend
  backend="$(env_value "$ENV_FILE" S3_STORAGE_BACKEND)"
  printf '%s' "${backend:-off}"
}

s3_rustfs_enabled() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  [[ ",$profiles," == *,rustfs,* ]]
}

s3_rustfs_add_profile() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," == *,rustfs,* ]]; then
    return 0
  fi
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "${profiles:+$profiles,}rustfs"
  printf 'Enabled the "rustfs" compose profile.\n'
}

s3_rustfs_remove_profile() {
  local profiles entry filtered="" entries=()
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  IFS=',' read -r -a entries <<< "$profiles"
  for entry in "${entries[@]}"; do
    [[ -n "$entry" && "$entry" != rustfs ]] || continue
    filtered="${filtered:+$filtered,}$entry"
  done
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "$filtered"
}

s3_bind_port() {
  local bind port
  bind="$(env_value "$ENV_FILE" RUSTFS_BIND_IP)"; bind="${bind:-127.0.0.1}"
  port="$(env_value "$ENV_FILE" RUSTFS_PORT)"; port="${port:-9000}"
  case "$bind" in 0.0.0.0|::|"[::]") bind=127.0.0.1 ;; esac
  printf '%s %s\n' "$bind" "$port"
}

# Address that tools on the Docker host use. Inside the stack network the
# services reach the bundled server as http://rustfs:9000, which never
# resolves from the host, so the operator-facing checks need this value.
s3_host_endpoint() {
  local override endpoint bind port
  # The caller's environment wins so a tool that runs inside the stack network
  # (for example the operator panel) can point the check at the in-network
  # endpoint without editing .env.
  override="${S3_HOST_ENDPOINT_URL:-$(env_value "$ENV_FILE" S3_HOST_ENDPOINT_URL)}"
  if [[ -n "$override" ]]; then
    printf '%s' "${override%/}"
    return 0
  fi
  if [[ "$(s3_backend)" == rustfs ]]; then
    read -r bind port < <(s3_bind_port)
    printf 'http://%s:%s' "$bind" "$port"
    return 0
  fi
  endpoint="$(env_value "$ENV_FILE" S3_ENDPOINT_URL)"
  printf '%s' "${endpoint%/}"
}

s3_container_endpoint() {
  local endpoint
  endpoint="$(env_value "$ENV_FILE" S3_ENDPOINT_URL)"
  if [[ -z "$endpoint" && "$(s3_backend)" == rustfs ]]; then
    endpoint="http://rustfs:9000"
  fi
  printf '%s' "${endpoint%/}"
}

s3_console_url() {
  local bind port prefix
  bind="$(env_value "$ENV_FILE" RUSTFS_CONSOLE_BIND_IP)"; bind="${bind:-127.0.0.1}"
  port="$(env_value "$ENV_FILE" RUSTFS_CONSOLE_PORT)"; port="${port:-9001}"
  prefix="$(env_value "$ENV_FILE" RUSTFS_CONSOLE_PREFIX)"; prefix="${prefix:-/rustfs/console}"
  case "$bind" in 0.0.0.0|::|"[::]") bind=127.0.0.1 ;; esac
  printf 'http://%s:%s%s/' "$bind" "$port" "${prefix%/}"
}

# The RustFS image runs unprivileged, so the bind-mounted directories must be
# writable by RUSTFS_UID/RUSTFS_GID. install.sh maps those to the invoking
# user for unprivileged installs.
s3_prepare_dirs() {
  local uid gid owner dir
  uid="$(env_value "$ENV_FILE" RUSTFS_UID)"; uid="${uid:-10001}"
  gid="$(env_value "$ENV_FILE" RUSTFS_GID)"; gid="${gid:-10001}"
  for dir in "$(s3_data_dir)/data" "$(s3_data_dir)/logs"; do
    install -d -m 0700 "$dir"
    owner="$(stat -c '%u:%g' "$dir")"
    if [[ "$owner" != "$uid:$gid" ]]; then
      if ! chown "$uid:$gid" "$dir" 2>/dev/null; then
        printf 'WARNING: %s is owned by %s but RustFS runs as %s. Run: sudo chown %s:%s %s\n' \
          "${dir#$ROOT_DIR/}" "$owner" "$uid:$gid" "$uid" "$gid" "${dir#$ROOT_DIR/}" >&2
      fi
    fi
  done
}

s3_recreate_consumers() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," == *,open-webui,* ]]; then
    compose up -d --no-deps --force-recreate open-webui >/dev/null
    printf 'Recreated Open WebUI with the updated storage settings.\n'
  fi
}

s3_wait_healthy() {
  local endpoint attempt
  endpoint="$(s3_host_endpoint)"
  for attempt in $(seq 1 30); do
    if curl -fsS --connect-timeout 2 --max-time 4 "$endpoint/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

s3_curl_supported() {
  curl --help all 2>/dev/null | grep -q -- '--aws-sigv4'
}

# Signed S3 request. Prints "HTTP_CODE" on stdout and the response body on
# stderr so callers can decide on retries without leaking the secret.
s3_signed_request() {
  local method="$1" url="$2" key="$3" secret="$4" region="$5" body="${6:-}"
  local args=(-sS -o /dev/stdout -w '%{http_code}' -X "$method"
    --aws-sigv4 "aws:amz:${region}:s3" --user "${key}:${secret}")
  if [[ -n "$body" ]]; then
    printf '%s' "$body" | curl "${args[@]}" --data-binary @- "$url"
  else
    curl "${args[@]}" "$url"
  fi
}

s3_ensure_bucket() {
  local endpoint bucket key secret region response code
  endpoint="$(s3_host_endpoint)"
  bucket="$(env_value "$ENV_FILE" S3_BUCKET)"; bucket="${bucket:-locallab}"
  key="$(env_value "$ENV_FILE" S3_ACCESS_KEY_ID)"
  secret="$(env_value "$ENV_FILE" S3_SECRET_ACCESS_KEY)"
  region="$(env_value "$ENV_FILE" S3_REGION)"; region="${region:-us-east-1}"
  [[ -n "$endpoint" && -n "$key" && -n "$secret" ]] || return 1
  response="$(s3_signed_request GET "$endpoint/" "$key" "$secret" "$region" 2>/dev/null)"
  code="${response: -3}"
  [[ "$code" == 200 ]] || return 1
  if [[ "${response%???}" == *"<Name>${bucket}</Name>"* ]]; then
    printf 'Bucket "%s" already exists.\n' "$bucket"
    return 0
  fi
  response="$(s3_signed_request PUT "$endpoint/${bucket}" "$key" "$secret" "$region" 2>/dev/null)"
  code="${response: -3}"
  if [[ "$code" == 200 ]]; then
    printf 'Created bucket "%s".\n' "$bucket"
    return 0
  fi
  printf 'Could not create bucket "%s" (HTTP %s).\n' "$bucket" "$code" >&2
  return 1
}

s3_status() {
  local backend bucket region key secret profiles consumer public_base
  backend="$(s3_backend)"
  bucket="$(env_value "$ENV_FILE" S3_BUCKET)"; bucket="${bucket:-locallab}"
  region="$(env_value "$ENV_FILE" S3_REGION)"; region="${region:-us-east-1}"
  key="$(env_value "$ENV_FILE" S3_ACCESS_KEY_ID)"
  secret="$(env_value "$ENV_FILE" S3_SECRET_ACCESS_KEY)"
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  printf 'Shared object storage\n'
  printf '  Backend:            %s\n' "$backend"
  printf '  Endpoint (stack):   %s\n' "$(s3_container_endpoint)"
  printf '  Endpoint (host):    %s\n' "$(s3_host_endpoint)"
  printf '  Bucket / region:    %s / %s\n' "$bucket" "$region"
  printf '  Credentials:        %s\n' \
    "$([[ -n "$key" && -n "$secret" ]] && printf 'configured (S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY)' || printf 'not configured; run ./manage.sh s3-enable')"
  public_base="$(env_value "$ENV_FILE" S3_PUBLIC_BASE_URL)"
  printf '  Public API URL:     %s\n' "${public_base:--}"
  if [[ "$backend" == rustfs ]]; then
    printf '  RustFS profile:     %s\n' "$(s3_rustfs_enabled && printf 'enabled' || printf 'missing; run ./manage.sh s3-enable --rustfs')"
    printf '  RustFS bind:        %s\n' "$(s3_bind_port | tr ' ' ':')"
    printf '  Console:            %s\n' "$(s3_console_url)"
    printf '  Data directory:     %s\n' "$(s3_data_dir | sed "s|^$ROOT_DIR/||")"
    if s3_rustfs_enabled; then
      compose ps rustfs 2>/dev/null || true
    fi
  fi
  printf '  Consumers:\n'
  if [[ ",$profiles," == *,open-webui,* ]]; then
    consumer="$(env_value "$ENV_FILE" OPENWEBUI_STORAGE_PROVIDER)"; consumer="${consumer:-local}"
    printf '    open-webui        %s\n' "$([[ "$consumer" == s3 ]] && printf 's3 (uploads and generated files in the bucket)' || printf 'local disk (set storage to s3 with ./manage.sh s3-enable)')"
  else
    printf '    open-webui        not installed\n'
  fi
  printf '    content-bot       local disk (no S3 driver yet; documented in docs/S3-STORAGE.md)\n'
  printf '    media-studio      local disk (no S3 driver yet; documented in docs/S3-STORAGE.md)\n'
  printf '    n8n               external binary storage requires n8n Enterprise; n8n still stores workflows locally\n'
  printf '    hermes-agent      no object-storage integration\n'
  printf '  Verify:             ./manage.sh s3-verify\n'
}

s3_prompt_value() {
  local label="$1" key="$2" secret_input="${3:-false}" value
  if [[ ! -t 0 ]]; then
    printf 'Set %s in .env first, or run this command from an interactive terminal.\n' "$key" >&2
    return 1
  fi
  if [[ "$secret_input" == true ]]; then
    read -r -s -p "$label: " value
    printf '\n'
  else
    read -r -p "$label: " value
  fi
  [[ -n "$value" ]] || { printf '%s must not be empty.\n' "$key" >&2; return 1; }
  replace_env_value "$ENV_FILE" "$key" "$value"
  printf 'Stored %s in .env.\n' "$key" >&2
}

s3_enable() {
  local mode="" bind_ip="" arg endpoint key secret bucket region bind port rustfs_uid rustfs_gid rustfs_data_owner
  while (($#)); do
    arg="$1"; shift
    case "$arg" in
      --rustfs|rustfs) mode=rustfs ;;
      --external|external) mode=external ;;
      --bind-ip)
        (($#)) || { printf 'Usage: ./manage.sh s3-enable [--rustfs|--external] [--bind-ip IP]\n' >&2; return 2; }
        bind_ip="$1"; shift ;;
      *) printf 'Usage: ./manage.sh s3-enable [--rustfs|--external] [--bind-ip IP]\n' >&2; return 2 ;;
    esac
  done
  [[ -n "$mode" ]] || mode="$(s3_backend)"
  [[ "$mode" == rustfs || "$mode" == external ]] || mode=rustfs

  replace_env_value "$ENV_FILE" S3_FORCE_PATH_STYLE true

  if [[ "$mode" == rustfs ]]; then
    if [[ -n "$bind_ip" ]]; then
      replace_env_value "$ENV_FILE" RUSTFS_BIND_IP "$bind_ip"
    fi
    # The container identity must match the owner of data/rustfs. Root-managed
    # stacks keep the image identity (10001:10001); an unprivileged operator
    # cannot chown the bind mounts, so the container is pinned to that operator
    # instead of the image default.
    configured_rustfs_uid="$(env_value "$ENV_FILE" RUSTFS_UID)"
    rustfs_uid="$configured_rustfs_uid"
    rustfs_gid="$(env_value "$ENV_FILE" RUSTFS_GID)"
    if [[ "$(id -u)" != 0 && ( -z "$rustfs_uid" || "$rustfs_uid" == 10001 ) ]]; then
      # Only take over the image default when the tree is missing or already
      # owned by the caller; a root-managed tree keeps its configured identity.
      rustfs_data_owner="$(stat -c '%u' "$(s3_data_dir)/data" 2>/dev/null || true)"
      if [[ -z "$rustfs_data_owner" || "$rustfs_data_owner" == "$(id -u)" ]]; then
        rustfs_uid="$(id -u)"
        rustfs_gid="$(id -g)"
      fi
    fi
    rustfs_uid="${rustfs_uid:-10001}"
    rustfs_gid="${rustfs_gid:-10001}"
    if [[ "$rustfs_uid" != "$configured_rustfs_uid" ]]; then
      replace_env_value "$ENV_FILE" RUSTFS_UID "$rustfs_uid"
      replace_env_value "$ENV_FILE" RUSTFS_GID "$rustfs_gid"
      printf 'Pinned RUSTFS_UID/RUSTFS_GID to %s:%s in .env.\n' "$rustfs_uid" "$rustfs_gid"
    fi
    key="$(env_value "$ENV_FILE" RUSTFS_ACCESS_KEY)"
    case "$key" in
      ""|CHANGE_ME)
        key="locallab-$(random_hex 6)"
        replace_env_value "$ENV_FILE" RUSTFS_ACCESS_KEY "$key"
        ;;
    esac
    secret="$(env_value "$ENV_FILE" RUSTFS_SECRET_KEY)"
    case "$secret" in
      ""|CHANGE_ME)
        secret="$(random_hex 16)"
        replace_env_value "$ENV_FILE" RUSTFS_SECRET_KEY "$secret"
        ;;
    esac
    bucket="$(env_value "$ENV_FILE" S3_BUCKET)"; bucket="${bucket:-locallab}"
    region="$(env_value "$ENV_FILE" RUSTFS_REGION)"; region="${region:-us-east-1}"
    read -r bind port < <(s3_bind_port)
    replace_env_value "$ENV_FILE" S3_STORAGE_BACKEND rustfs
    replace_env_value "$ENV_FILE" S3_ENDPOINT_URL "http://rustfs:9000"
    replace_env_value "$ENV_FILE" S3_ACCESS_KEY_ID "$key"
    replace_env_value "$ENV_FILE" S3_SECRET_ACCESS_KEY "$secret"
    replace_env_value "$ENV_FILE" S3_BUCKET "$bucket"
    replace_env_value "$ENV_FILE" S3_REGION "$region"
    replace_env_value "$ENV_FILE" S3_HOST_ENDPOINT_URL "http://$bind:$port"
    replace_env_value "$ENV_FILE" OPENWEBUI_STORAGE_PROVIDER s3
    s3_rustfs_add_profile
    s3_prepare_dirs
    compose up -d --no-deps rustfs
    if s3_wait_healthy; then
      s3_ensure_bucket || printf 'WARNING: the bucket could not be created yet; rerun ./manage.sh s3-verify --create-bucket.\n' >&2
    else
      printf 'WARNING: the RustFS API did not answer yet; check ./manage.sh logs rustfs.\n' >&2
    fi
    s3_recreate_consumers
    printf '\nObject storage: RustFS (profile "rustfs")\n'
    printf '  S3 API:  %s\n' "$(s3_host_endpoint)"
    printf '  Console: %s\n' "$(s3_console_url)"
    printf '  Bucket:  %s (region %s)\n' "$bucket" "$region"
    printf '  Credentials stay in .env; print them with ./manage.sh s3-keys --show-secrets\n'
    printf '  Publish it with domain: ./manage.sh s3-guide\n'
    return 0
  fi

  # Values that belong to the bundled server never count as external ones.
  endpoint="$(env_value "$ENV_FILE" S3_ENDPOINT_URL)"
  case "$endpoint" in "http://rustfs:9000"|"http://rustfs:9000/") endpoint="" ;; esac
  [[ -n "$endpoint" ]] || s3_prompt_value 'S3 endpoint URL (for example https://s3.eu-central-1.amazonaws.com)' S3_ENDPOINT_URL
  key="$(env_value "$ENV_FILE" S3_ACCESS_KEY_ID)"
  case "$key" in
    ""|CHANGE_ME|"$(env_value "$ENV_FILE" RUSTFS_ACCESS_KEY)") s3_prompt_value 'S3 access key id' S3_ACCESS_KEY_ID ;;
  esac
  secret="$(env_value "$ENV_FILE" S3_SECRET_ACCESS_KEY)"
  case "$secret" in
    ""|CHANGE_ME|"$(env_value "$ENV_FILE" RUSTFS_SECRET_KEY)") s3_prompt_value 'S3 secret access key' S3_SECRET_ACCESS_KEY true ;;
  esac
  bucket="$(env_value "$ENV_FILE" S3_BUCKET)"; [[ -n "$bucket" ]] || s3_prompt_value 'S3 bucket name' S3_BUCKET
  region="$(env_value "$ENV_FILE" S3_REGION)"; [[ -n "$region" ]] || s3_prompt_value 'S3 region' S3_REGION
  replace_env_value "$ENV_FILE" S3_STORAGE_BACKEND external
  replace_env_value "$ENV_FILE" S3_HOST_ENDPOINT_URL ""
  replace_env_value "$ENV_FILE" OPENWEBUI_STORAGE_PROVIDER s3
  s3_rustfs_remove_profile
  compose --profile rustfs rm -sf rustfs >/dev/null 2>&1 || true
  s3_recreate_consumers
  printf '\nObject storage: external S3 endpoint\n'
  printf '  Bucket: %s (region %s)\n' "$(env_value "$ENV_FILE" S3_BUCKET)" "$(env_value "$ENV_FILE" S3_REGION)"
  printf '  Verify the endpoint and credentials with ./manage.sh s3-verify\n'
}

s3_disable() {
  local running
  replace_env_value "$ENV_FILE" S3_STORAGE_BACKEND off
  replace_env_value "$ENV_FILE" OPENWEBUI_STORAGE_PROVIDER local
  running="$(compose --profile rustfs ps -q rustfs 2>/dev/null || true)"
  if [[ -n "$running" ]]; then
    compose --profile rustfs rm -sf rustfs >/dev/null 2>&1 || true
    printf 'Stopped the bundled RustFS container; its data stays in %s.\n' "$(s3_data_dir | sed "s|^$ROOT_DIR/||")"
  fi
  s3_rustfs_remove_profile
  s3_recreate_consumers
  printf 'Object storage disabled; services fall back to their local storage.\n'
}

s3_verify() {
  local create_bucket=false arg endpoint bucket key secret region response code
  while (($#)); do
    arg="$1"; shift
    case "$arg" in
      --create-bucket) create_bucket=true ;;
      *) printf 'Usage: ./manage.sh s3-verify [--create-bucket]\n' >&2; return 2 ;;
    esac
  done
  [[ "$(s3_backend)" != off ]] || {
    printf 'Object storage is off. Run ./manage.sh s3-enable --rustfs (or --external) first.\n' >&2
    return 1
  }
  s3_curl_supported || {
    printf 'This curl lacks --aws-sigv4 support, so signed S3 requests cannot be made here.\n' >&2
    printf 'Run the check from a host with curl 7.75+ or use the provider console.\n' >&2
    return 1
  }
  endpoint="$(s3_host_endpoint)"
  bucket="$(env_value "$ENV_FILE" S3_BUCKET)"; bucket="${bucket:-locallab}"
  key="$(env_value "$ENV_FILE" S3_ACCESS_KEY_ID)"
  secret="$(env_value "$ENV_FILE" S3_SECRET_ACCESS_KEY)"
  region="$(env_value "$ENV_FILE" S3_REGION)"; region="${region:-us-east-1}"
  [[ -n "$endpoint" ]] || { printf 'No S3 endpoint is configured.\n' >&2; return 1; }
  [[ -n "$key" && -n "$secret" ]] || { printf 'S3 credentials are missing; run ./manage.sh s3-enable.\n' >&2; return 1; }

  printf 'Endpoint: %s\n' "$endpoint"
  response="$(s3_signed_request GET "$endpoint/" "$key" "$secret" "$region" 2>/dev/null || true)"
  code="${response: -3}"
  if [[ "$code" != 200 ]]; then
    printf 'FAIL: signed request was rejected (HTTP %s). Check the endpoint, region, and credentials.\n' "$code" >&2
    return 1
  fi
  printf 'OK: signed request accepted; the credentials are valid.\n'
  if [[ "${response%???}" == *"<Name>${bucket}</Name>"* ]]; then
    printf 'OK: bucket "%s" exists.\n' "$bucket"
  elif [[ "$create_bucket" == true ]]; then
    response="$(s3_signed_request PUT "$endpoint/${bucket}" "$key" "$secret" "$region" 2>/dev/null || true)"
    code="${response: -3}"
    if [[ "$code" == 200 ]]; then
      printf 'OK: created bucket "%s".\n' "$bucket"
    else
      printf 'FAIL: could not create bucket "%s" (HTTP %s).\n' "$bucket" "$code" >&2
      return 1
    fi
  else
    printf 'Bucket "%s" is missing; rerun with --create-bucket to create it.\n' "$bucket" >&2
    return 1
  fi
  printf 'Object storage verification passed.\n'
}

s3_keys() {
  local key secret
  [[ "$(s3_backend)" == rustfs ]] || {
    printf 'These credentials belong to the bundled RustFS server; the active backend is "%s".\n' "$(s3_backend)" >&2
    return 1
  }
  key="$(env_value "$ENV_FILE" RUSTFS_ACCESS_KEY)"
  secret="$(env_value "$ENV_FILE" RUSTFS_SECRET_KEY)"
  printf 'RustFS access key id: %s\n' "${key:-<not set; run ./manage.sh s3-enable --rustfs>}"
  case "${1:-}" in
    --show-secrets)
      [[ -r /dev/tty && -w /dev/tty ]] || { printf 'A controlling terminal is required to reveal secrets.\n' >&2; return 1; }
      read -r -p 'Reveal the object-storage secret on this terminal? [y/N]: ' answer </dev/tty
      [[ "$answer" =~ ^[Yy]$ ]] || { printf 'Secret not shown.\n'; return 0; }
      printf 'RUSTFS_SECRET_KEY=%s\n' "$secret"
      printf 'S3_ACCESS_KEY_ID=%s\n' "$key"
      printf 'S3_SECRET_ACCESS_KEY=%s\n' "$secret"
      ;;
    --rotate)
      key="locallab-$(random_hex 6)"
      secret="$(random_hex 16)"
      replace_env_value "$ENV_FILE" RUSTFS_ACCESS_KEY "$key"
      replace_env_value "$ENV_FILE" RUSTFS_SECRET_KEY "$secret"
      replace_env_value "$ENV_FILE" S3_ACCESS_KEY_ID "$key"
      replace_env_value "$ENV_FILE" S3_SECRET_ACCESS_KEY "$secret"
      compose up -d --no-deps --force-recreate rustfs >/dev/null
      if s3_wait_healthy; then
        s3_ensure_bucket || true
      fi
      s3_recreate_consumers
      printf 'Rotated the RustFS credentials; new access key id: %s\n' "$key"
      printf 'Print it with ./manage.sh s3-keys --show-secrets when you need to configure a client.\n'
      ;;
    "")
      printf 'Use --show-secrets to print the credentials or --rotate to replace them.\n'
      ;;
    *) printf 'Usage: ./manage.sh s3-keys [--show-secrets|--rotate]\n' >&2; return 2 ;;
  esac
}

s3_guide() {
  local lan_ip bind base_domain api_host console_host
  lan_ip="$(ip -4 route get 1.1.1.1 2>/dev/null \
    | awk '{ for (i=1; i<=NF; i++) if ($i == "src") { print $(i+1); exit } }')"
  bind="$(s3_bind_port)"
  # Prefer the host names already recorded in .env, then the shared base
  # domain from the installer, and only then the placeholder zone.
  base_domain="$(env_value "$ENV_FILE" STACK_BASE_DOMAIN)"
  [[ "$base_domain" == "CHANGE_ME" ]] && base_domain=""
  base_domain="${base_domain:-stack.example.com}"
  api_host="$(url_host "$(env_value "$ENV_FILE" S3_PUBLIC_BASE_URL)")"
  console_host="$(url_host "$(env_value "$ENV_FILE" S3_PUBLIC_CONSOLE_URL)")"
  api_host="${api_host:-s3.$base_domain}"
  console_host="${console_host:-console.$base_domain}"
  printf 'Object storage guide: docs/S3-STORAGE.md\n'
  printf '\nPublic domain route (ArvanCloud -> router reverse proxy -> this stack):\n'
  printf '  1) DNS: create %s (and %s if you\n' "$api_host" "$console_host"
  printf '     want the console) in ArvanCloud, proxied as usual.\n'
  printf '  2) The stack must listen on the LAN address instead of loopback:\n'
  printf '     ./manage.sh s3-enable --rustfs --bind-ip %s\n' "${lan_ip:-<LAN-IP>}"
  printf '     (console bind: set RUSTFS_CONSOLE_BIND_IP=%s in .env the same way)\n' "${lan_ip:-<LAN-IP>}"
  printf '  3) On the MikroTik Caddy container add:\n'
  printf '     %s {\n         encode zstd gzip\n         reverse_proxy %s:%s\n     }\n' \
    "$api_host" "${lan_ip:-<STACK-LAN-IP>}" "$(printf '%s' "$bind" | awk '{print $2}')"
  printf '     For the console, publish port 9001 under its own host name. Sign-in sends a\n'
  printf '     signed POST to "/" (Action=AssumeRole) on that host, so a blanket\n'
  printf '     redirect on "/" breaks login. Publish it with no redirect at all:\n'
  printf '       %s {\n           encode zstd gzip\n           reverse_proxy %s:9001\n       }\n' \
    "$console_host" "${lan_ip:-<STACK-LAN-IP>}"
  printf '     or keep the friendly root redirect and restrict it to GET:\n'
  printf '       @console_root {\n           method GET\n           path /\n       }\n'
  printf '       redir @console_root /rustfs/console/ 302\n'
  printf '  4) Record the public origin for the stack: S3_PUBLIC_BASE_URL=https://%s in .env\n' "$api_host"
  printf '     For the console add S3_PUBLIC_CONSOLE_URL=https://%s/rustfs/console and\n' "$console_host"
  printf '     RUSTFS_CONSOLE_BIND_IP=%s, then restart rustfs.\n' "${lan_ip:-<LAN-IP>}"
  printf '\nSecurity: keep the console off the public internet unless you accept the risk; the S3 API\n'
  printf 'must always sit behind strong credentials. ./manage.sh s3-keys --rotate replaces them.\n'
  printf 'External provider instead of RustFS: ./manage.sh s3-enable --external\n'
}

storage_menu() {
  local choice
  while true; do
    menu_title 'Object Storage (S3)'
    printf '%s\n' '1) Status                  Backend, endpoints, bucket and consumers'
    printf '%s\n' '2) Enable RustFS           Bundled S3 server for the stack'
    printf '%s\n' '3) Use an external S3      Point the stack at another provider'
    printf '%s\n' '4) Verify                  Signed request, credentials and bucket'
    printf '%s\n' '5) Create the bucket       Only when it is still missing'
    printf '%s\n' '6) Credentials             Show or rotate the RustFS keys'
    printf '%s\n' '7) Public domain guide     ArvanCloud, router proxy and bind addresses'
    printf '%s\n' '8) Follow RustFS logs'
    printf '%s\n' '9) Disable                 Back to local storage'
    printf '%s\n' '0) Back'
    read -r -p 'Choose [0]: ' choice
    case "${choice:-0}" in
      1) s3_status; menu_pause ;;
      2) s3_enable --rustfs; menu_pause ;;
      3) s3_enable --external; menu_pause ;;
      4) s3_verify; menu_pause ;;
      5) s3_verify --create-bucket; menu_pause ;;
      6)
        printf '%s\n' '1) Show access key id  2) Reveal credentials  3) Rotate'
        read -r -p 'Choose [1]: ' choice
        case "${choice:-1}" in
          1) s3_keys ;;
          2) s3_keys --show-secrets ;;
          3) s3_keys --rotate ;;
          *) printf 'Unknown choice.\n' >&2 ;;
        esac
        menu_pause
        ;;
      7) s3_guide; menu_pause ;;
      8) compose logs -f --tail=100 rustfs ;;
      9) s3_disable; menu_pause ;;
      0) return 0 ;;
      *) printf 'Unknown choice.\n' >&2 ;;
    esac
  done
}

# Extracts the host name from a recorded public URL such as
# https://s3.stack.example.com (empty input returns an empty string).
url_host() {
  local url="${1-}" host
  host="${url#*//}"
  host="${host%%/*}"
  printf '%s' "$host"
}

env_value() {
  local file="$1" key="$2" value="" count
  [[ -f "$file" ]] || return 0
  count="$(grep -c "^${key}=" "$file" || true)"
  if (( count > 1 )); then
    printf 'Duplicate %s entries in %s are unsafe; keep exactly one value.\n' \
      "$key" "${file#$ROOT_DIR/}" >&2
    return 1
  fi
  value="$(sed -n "s/^${key}=//p" "$file")"
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}


n8n_status() {
  local profiles mode public_url
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," != *,n8n,* ]]; then
    printf 'n8n is not enabled in COMPOSE_PROFILES. Run ./manage.sh configure to enable it.\n'
    return 1
  fi
  mode="$(n8n_mcp_mode 2>/dev/null || printf invalid)"
  public_url="$(env_value "$ENV_FILE" N8N_PUBLIC_URL)"
  if [[ -z "$public_url" ]]; then
    public_url="http://$(env_value "$ENV_FILE" N8N_BIND_IP):$(env_value "$ENV_FILE" N8N_PORT)"
  fi
  printf 'n8n provisioning status\n'
  printf '  URL: %s\n' "$public_url"
  printf '  MCP mode: %s\n' "$mode"
  if [[ -n "$(n8n_api_key 2>/dev/null || true)" ]]; then
    printf '  owner API key: stored (secret not shown)\n'
  else
    printf '  owner API key: NOT stored; managed workflow reconciliation is pending\n'
  fi
  if [[ -f "$HERMES_ENV" && -n "$(env_value "$HERMES_ENV" N8N_INSTANCE_MCP_TOKEN 2>/dev/null || true)" ]]; then
    printf '  Instance MCP token: stored (secret not shown)\n'
  else
    printf '  Instance MCP token: not stored\n'
  fi
  if [[ -f "$HERMES_ENV" && -n "$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN 2>/dev/null || true)" ]]; then
    printf '  Trigger MCP token: stored (secret not shown)\n'
  else
    printf '  Trigger MCP token: not stored\n'
  fi
  if [[ -f "$N8N_BOOTSTRAP_STATE" && ! -L "$N8N_BOOTSTRAP_STATE" ]]; then
    printf '  managed n8n state: present\n'
  else
    printf '  managed n8n state: pending\n'
  fi
  case "$mode" in
    instance)
      printf '  Instance setup: Settings -> Instance-level MCP -> Enable MCP access -> Connection details -> Access Token\n'
      ;;
    trigger)
      printf '  Trigger setup: stack-managed MCP Server Trigger workflow\n'
      ;;
  esac
}

n8n_menu() {
  local choice value
  while true; do
    printf '\nn8n Provisioning / MCP Manager\n'
    printf '%s\n' '=============================='
    printf '%s\n' '1) Show provisioning / MCP status'
    printf '%s\n' '2) Store or replace owner API key'
    printf '%s\n' '3) Store or replace Instance-level MCP token'
    printf '%s\n' '4) Select MCP mode (instance / trigger / off)'
    printf '%s\n' '5) Bootstrap / reconcile stack-owned n8n objects'
    printf '%s\n' '6) Verify current n8n + MCP configuration'
    printf '%s\n' '7) Rotate retained Trigger-mode bearer token'
    printf '%s\n' '8) Remove stored owner API key'
    printf '%s\n' '9) Remove stored Instance MCP token (when mode is not instance)'
    printf '%s\n' '0) Back'
    read -r -p 'Choose: ' choice
    case "$choice" in
      1) "$ROOT_DIR/manage.sh" n8n-status ;;
      2)
        printf '%s\n' 'Create the key in n8n as the owner/admin, then paste it at the hidden prompt.'
        "$ROOT_DIR/manage.sh" set-n8n-api-key
        ;;
      3)
        printf '%s\n' 'In n8n: Settings -> Instance-level MCP -> enable access -> Connection details -> Access Token.'
        printf '%s\n' 'Generate/copy the token, then paste it at the hidden prompt.'
        "$ROOT_DIR/manage.sh" set-n8n-instance-mcp-token
        ;;
      4)
        printf '%s\n' '  instance - n8n built-in Instance-level MCP endpoint'
        printf '%s\n' '  trigger  - stack-managed MCP Server Trigger workflow'
        printf '%s\n' '  off      - disconnect Hermes from n8n MCP'
        read -r -p 'Mode (instance/trigger/off): ' value
        "$ROOT_DIR/manage.sh" set-n8n-mcp-mode "$value"
        ;;
      5) "$ROOT_DIR/manage.sh" bootstrap-n8n ;;
      6) "$ROOT_DIR/manage.sh" verify-n8n ;;
      7) "$ROOT_DIR/manage.sh" rotate-n8n-trigger-token ;;
      8) "$ROOT_DIR/manage.sh" remove-n8n-bootstrap-key ;;
      9) "$ROOT_DIR/manage.sh" remove-n8n-instance-mcp-token ;;
      0) return 0 ;;
      *) printf 'Unknown choice.\n' >&2 ;;
    esac
  done
}

require_profiles() {
  local profiles required
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  for required in "$@"; do
    [[ ",$profiles," == *",$required,"* ]] || {
      printf '%s is not selected. Run ./manage.sh configure first.\n' "$required" >&2
      exit 1
    }
  done
}

require_router_backend() {
  local profiles
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," != *,9router,* && ",$profiles," != *,omniroute,* ]]; then
    printf 'This command requires a router backend (9router or OmniRoute). Run ./manage.sh configure first.\n' >&2
    exit 1
  fi
}

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "${1:-32}"
  else
    od -An -N "${1:-32}" -tx1 /dev/urandom | tr -d ' \n'
  fi
}

# The reconciler and verifier containers run --cap-drop ALL, so their uid has no
# CAP_DAC_OVERRIDE and cannot traverse a mode-0700 /state owned by anyone else.
# Keep the directory and its secrets owned by whoever invokes manage.sh.
ensure_stack_secrets_dir() {
  local owner
  if [[ -e "$STACK_SECRETS_DIR" || -L "$STACK_SECRETS_DIR" ]]; then
    [[ -d "$STACK_SECRETS_DIR" && ! -L "$STACK_SECRETS_DIR" ]] || {
      printf 'Refusing unsafe data/stack-secrets path; expected a real directory.\n' >&2
      return 1
    }
  else
    mkdir -p "$STACK_SECRETS_DIR"
  fi
  chmod 700 "$STACK_SECRETS_DIR"
  owner="$(stat -c '%u:%g' "$STACK_SECRETS_DIR")"
  if [[ "$owner" != "$(id -u):$(id -g)" ]]; then
    chown "$(id -u):$(id -g)" "$STACK_SECRETS_DIR" 2>/dev/null || {
      printf 'data/stack-secrets is owned by %s; rerun as that user or fix its ownership.\n' \
        "$owner" >&2
      return 1
    }
  fi
}

execution_root() { printf '%s/execution' "$STACK_SECRETS_DIR"; }

execution_hermes_uid() {
  local uid
  uid="$(env_value "$ENV_FILE" HERMES_UID)"
  uid="${uid:-10000}"
  [[ "$uid" =~ ^[1-9][0-9]*$ ]] || {
    printf 'HERMES_UID must be a non-root numeric uid before configuring execution.\n' >&2
    return 1
  }
  printf '%s' "$uid"
}

ensure_execution_paths() {
  local root file hermes_uid
  ensure_stack_secrets_dir || return 1
  hermes_uid="$(execution_hermes_uid)" || return 1
  root="$(execution_root)"
  [[ ! -L "$root" ]] || { printf 'Refusing unsafe execution symlink: %s\n' "$root" >&2; return 1; }
  install -d -m 0700 "$root"
  for file in "$root/docker-state" "$root/ssh-state" "$root/approver-state" "$root/admin-state" "$root/ssh"; do
    [[ ! -L "$file" ]] || { printf 'Refusing unsafe execution symlink: %s\n' "$file" >&2; return 1; }
    install -d -o 10003 -g 10003 -m 0700 "$file"
    chown 10003:10003 "$file"
    chmod 700 "$file"
  done
  for file in "$root/control-secret" "$root/users" "$root/features" "$root/policy-generation"; do
    [[ ! -L "$file" && ( ! -e "$file" || -f "$file" ) ]] || {
      printf 'Refusing unsafe execution policy path: %s\n' "$file" >&2; return 1;
    }
    if [[ ! -e "$file" ]]; then
      install -o "$hermes_uid" -g 10003 -m 0660 /dev/null "$file"
      case "${file##*/}" in
        features) printf '%s\n' "$(env_value "$ENV_FILE" EXECUTION_FEATURES)" > "$file" ;;
        policy-generation) printf '%s\n' "$(env_value "$ENV_FILE" EXECUTION_POLICY_GENERATION | sed 's/^$/0/')" > "$file" ;;
      esac
    fi
    chown "$hermes_uid:10003" "$file"
    chmod 660 "$file"
  done
  for file in "$root/approval-request-secret" "$root/approval-signing-key.pem" \
    "$root/approval-public-key.pem" "$root/approval-bot-token" \
    "$root/ssh-profile-integrity-secret" "$root/admin-key"; do
    [[ ! -L "$file" && ( ! -e "$file" || -f "$file" ) ]] || {
      printf 'Refusing unsafe execution policy path: %s\n' "$file" >&2; return 1;
    }
    [[ -e "$file" ]] || install -o 10003 -g 10003 -m 0600 /dev/null "$file"
    chown 10003:10003 "$file"
    chmod 600 "$file"
  done
  for file in "$root/telegram-allowed-users" "$root/hermes-bot-token.sha256"; do
    [[ ! -L "$file" && ( ! -e "$file" || -f "$file" ) ]] || {
      printf 'Refusing unsafe execution admin metadata path: %s\n' "$file" >&2; return 1;
    }
    [[ -e "$file" ]] || install -o "$hermes_uid" -g 10003 -m 0640 /dev/null "$file"
    chown "$hermes_uid:10003" "$file"
    chmod 640 "$file"
  done
  install -d -o 10002 -g 10002 -m 0700 "$ROOT_DIR/data/execution-workspace"
}

execution_features() {
  local path="$(execution_root)/features"
  [[ -f "$path" ]] && { tr -d '[:space:]' < "$path"; return; }
  env_value "$ENV_FILE" EXECUTION_FEATURES
}

write_execution_features() {
  local features="$1" root tmp hermes_uid
  ensure_execution_paths || return 1
  hermes_uid="$(execution_hermes_uid)"; root="$(execution_root)"
  tmp="$(mktemp "$root/features.tmp.XXXXXX")"
  printf '%s\n' "$features" > "$tmp"; chown "$hermes_uid:10003" "$tmp"; chmod 660 "$tmp"; mv "$tmp" "$root/features"
  replace_env_value "$ENV_FILE" EXECUTION_FEATURES "$features"
}
execution_users() { [[ -f "$(execution_root)/users" ]] && tr -d '[:space:]' < "$(execution_root)/users" || true; }

telegram_users() {
  local value
  value="$(env_value "$HERMES_ENV" TELEGRAM_ALLOWED_USERS)"
  value="${value#[}"; value="${value%]}"; value="${value//\"/}"; value="${value// /}"
  printf '%s' "$value"
}

sync_execution_admin_metadata() {
  local root hermes_uid tmp token
  ensure_execution_paths || return 1
  root="$(execution_root)"; hermes_uid="$(execution_hermes_uid)"
  tmp="$(mktemp "$root/telegram-allowed-users.tmp.XXXXXX")"
  printf '%s\n' "$(telegram_users)" > "$tmp"; chown "$hermes_uid:10003" "$tmp"; chmod 640 "$tmp"; mv "$tmp" "$root/telegram-allowed-users"
  token="$(env_value "$HERMES_ENV" TELEGRAM_BOT_TOKEN)"
  tmp="$(mktemp "$root/hermes-bot-token.sha256.tmp.XXXXXX")"
  if [[ -n "$token" ]]; then printf '%s' "$token" | sha256sum | awk '{print $1}' > "$tmp"; else : > "$tmp"; fi
  chown "$hermes_uid:10003" "$tmp"; chmod 640 "$tmp"; mv "$tmp" "$root/hermes-bot-token.sha256"
}

execution_users_valid() {
  local users="$1" allowed user
  valid_ids "$users" || return 1
  allowed=",$(telegram_users),"
  IFS=, read -ra entries <<< "$users"
  for user in "${entries[@]}"; do [[ "$allowed" == *",$user,"* ]] || return 1; done
}

write_execution_users() {
  local users="$1" root tmp hermes_uid
  ensure_execution_paths || return 1
  hermes_uid="$(execution_hermes_uid)"
  root="$(execution_root)"
  tmp="$(mktemp "$root/users.tmp.XXXXXX")"
  printf '%s\n' "$users" > "$tmp"
  chown "$hermes_uid:10003" "$tmp"
  chmod 640 "$tmp"
  mv "$tmp" "$root/users"
}

rotate_execution_generation() {
  local current workspace root tmp hermes_uid
  ensure_execution_paths || return 1
  root="$(execution_root)"; hermes_uid="$(execution_hermes_uid)"
  current="$(tr -d '[:space:]' < "$root/policy-generation" 2>/dev/null || true)"; current="${current:-$(env_value "$ENV_FILE" EXECUTION_POLICY_GENERATION)}"; current="${current:-0}"
  workspace="$(env_value "$ENV_FILE" EXECUTION_WORKSPACE_GENERATION)"; workspace="${workspace:-0}"
  [[ "$current" =~ ^[0-9]+$ ]] || current=0
  [[ "$workspace" =~ ^[0-9]+$ ]] || workspace=0
  current="$((current + 1))"
  tmp="$(mktemp "$root/policy-generation.tmp.XXXXXX")"; printf '%s\n' "$current" > "$tmp"; chown "$hermes_uid:10003" "$tmp"; chmod 660 "$tmp"; mv "$tmp" "$root/policy-generation"
  replace_env_value "$ENV_FILE" EXECUTION_POLICY_GENERATION "$current"
  replace_env_value "$ENV_FILE" EXECUTION_WORKSPACE_GENERATION "$((workspace + 1))"
}

sync_execution_profiles() {
  local features profiles base admin_enabled
  features="$(execution_features)"
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  admin_enabled="$(env_value "$ENV_FILE" EXECUTION_ADMIN_ENABLED)"
  base="$(printf '%s' "$profiles" | tr ',' '\n' | grep -v -E '^execution-(docker|ssh|approval|admin)$' | paste -sd, -)"
  if [[ -n "$features" ]]; then base="${base:+$base,}execution-approval"; fi
  [[ ",$features," == *,local,* || ",$features," == *,docker,* ]] \
    && base="${base:+$base,}execution-docker"
  [[ ",$features," == *,ssh,* ]] && base="${base:+$base,}execution-ssh"
  [[ "$admin_enabled" == true ]] && base="${base:+$base,}execution-admin"
  replace_env_value "$ENV_FILE" COMPOSE_PROFILES "$base"
}

apply_execution_features() {
  local features="$1"
  write_execution_features "$features"
  rotate_execution_generation
  sync_execution_profiles
  if [[ -n "$features" ]]; then
    compose pull execution-approver execution-docker-broker execution-ssh-broker
  fi
  compose up -d --no-build --remove-orphans
}

set_execution_feature() {
  local requested="$1" enabled="$2" current item output=""
  [[ "$requested" == sandbox ]] && requested=local
  current="$(execution_features)"
  for item in local ssh docker; do
    if [[ "$enabled" == true && ( "$requested" == all || "$requested" == "$item" ) ]]; then
      [[ ",$current," == *",$item,"* ]] || output="${output:+$output,}$item"
    elif [[ "$enabled" != true && ( "$requested" == all || "$requested" == "$item" ) ]]; then
      continue
    elif [[ ",$current," == *",$item,"* ]]; then
      output="${output:+$output,}$item"
    fi
  done
  printf '%s' "$output"
}

write_n8n_bootstrap_key() {
  local key="$1" tmp
  ensure_stack_secrets_dir || return 1
  if [[ -e "$N8N_BOOTSTRAP_ENV" && ( ! -f "$N8N_BOOTSTRAP_ENV" || -L "$N8N_BOOTSTRAP_ENV" ) ]]; then
    printf 'Refusing unsafe n8n bootstrap secret path.\n' >&2
    return 1
  fi
  tmp="$(mktemp "$STACK_SECRETS_DIR/n8n-bootstrap.env.tmp.XXXXXX")"
  printf 'N8N_API_KEY=%s\n' "$key" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$N8N_BOOTSTRAP_ENV"
}

n8n_api_key() {
  env_value "$N8N_BOOTSTRAP_ENV" N8N_API_KEY
}

n8n_api_check() {
  local key="$1" image env_file status
  image_repo="$(env_value "$ENV_FILE" N8N_IMAGE_REPOSITORY)"; image_repo="${image_repo:-n8nio/n8n}"
  image_tag="$(env_value "$ENV_FILE" N8N_IMAGE_TAG)"; image_tag="${image_tag:-latest}"
  image="$image_repo:$image_tag"
  ensure_stack_secrets_dir || return 1
  env_file="$(mktemp "$STACK_SECRETS_DIR/n8n-api-check.env.tmp.XXXXXX")"
  TEMP_SECRET_FILES+=("$env_file")
  chmod 600 "$env_file"
  printf 'N8N_API_KEY=%s\n' "$key" > "$env_file"
  if "${DOCKER[@]}" run --rm --network hermes-9router-net \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --env-file "$env_file" --entrypoint node "$image" -e '
      fetch("http://n8n:5678/api/v1/workflows?limit=1", {
        headers: {"X-N8N-API-KEY": process.env.N8N_API_KEY},
      }).then(async response => {
        if (!response.ok) throw new Error(`n8n API returned ${response.status}`);
        return response.json();
      }).then(() => process.exit(0)).catch(error => {
        console.error(error.message); process.exit(1);
      });'; then
    status=0
  else
    status=$?
  fi
  rm -f -- "$env_file"
  return "$status"
}

n8n_instance_mcp_check() {
  local token="$1" image env_file status
  image_repo="$(env_value "$ENV_FILE" N8N_IMAGE_REPOSITORY)"; image_repo="${image_repo:-n8nio/n8n}"
  image_tag="$(env_value "$ENV_FILE" N8N_IMAGE_TAG)"; image_tag="${image_tag:-latest}"
  image="$image_repo:$image_tag"
  ensure_stack_secrets_dir || return 1
  env_file="$(mktemp "$STACK_SECRETS_DIR/n8n-instance-mcp-check.env.tmp.XXXXXX")"
  TEMP_SECRET_FILES+=("$env_file")
  chmod 600 "$env_file"
  printf 'N8N_INSTANCE_MCP_TOKEN=%s\n' "$token" > "$env_file"
  if "${DOCKER[@]}" run --rm --network hermes-9router-net \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --env-file "$env_file" --entrypoint node "$image" -e '
      const url = "http://n8n:5678/mcp-server/http";
      const initialize = {jsonrpc:"2.0",id:1,method:"initialize",params:{
        protocolVersion:"2025-03-26",capabilities:{},
        clientInfo:{name:"hermes-n8n-token-validator",version:"1"}}};
      const baseHeaders = {Accept:"application/json, text/event-stream","Content-Type":"application/json"};
      const fail = message => { console.error(message); process.exit(1); };
      const messages = async response => {
        const text = await response.text();
        if (!text.trim()) return [];
        if ((response.headers.get("content-type") || "").includes("text/event-stream")) {
          return text.split(/\r?\n/).filter(line => line.startsWith("data:"))
            .map(line => line.slice(5).trim()).filter(value => value && value !== "[DONE]")
            .map(value => JSON.parse(value));
        }
        return [JSON.parse(text)];
      };
      (async () => {
        let session;
        let failure;
        try {
          const anonymous = await fetch(url,{method:"POST",headers:baseHeaders,
            body:JSON.stringify(initialize),redirect:"manual",signal:AbortSignal.timeout(15000)});
          await anonymous.body?.cancel();
          if (![401,403].includes(anonymous.status)) throw new Error("Instance MCP did not reject an unauthenticated request");
          const request = async body => {
            const response = await fetch(url,{method:"POST",headers:{...baseHeaders,
              Authorization:`Bearer ${process.env.N8N_INSTANCE_MCP_TOKEN}`,
              ...(session?{"Mcp-Session-Id":session}:{})},body:JSON.stringify(body),
              redirect:"manual",signal:AbortSignal.timeout(15000)});
            if (!response.ok) throw new Error(`Instance MCP returned HTTP ${response.status}`);
            session = response.headers.get("mcp-session-id") || session;
            return messages(response);
          };
          const initialized = await request(initialize);
          if (!initialized.some(item => item?.id === 1 && item?.result?.protocolVersion)) throw new Error("Instance MCP initialize failed");
          await request({jsonrpc:"2.0",method:"notifications/initialized",params:{}});
          const listed = await request({jsonrpc:"2.0",id:2,method:"tools/list",params:{}});
          const tools = listed.find(item => item?.id === 2)?.result?.tools;
          // n8n adds Instance MCP capabilities over time. Require the stable
          // workflow core so a valid token is not rejected only because newer,
          // version-gated tools are absent from this installed n8n image.
          for (const name of ["search_workflows","get_workflow_details","execute_workflow"]) {
            if (!Array.isArray(tools) || !tools.some(tool => tool?.name === name)) throw new Error(`Instance MCP core tool ${name} is missing`);
          }
        } catch (error) {
          failure = error;
        }
        if (session) {
          try {
            const closed = await fetch(url,{method:"DELETE",headers:{
              Authorization:`Bearer ${process.env.N8N_INSTANCE_MCP_TOKEN}`,"Mcp-Session-Id":session},
              redirect:"manual",signal:AbortSignal.timeout(15000)});
            if (!closed.ok) throw new Error(`Instance MCP session close returned HTTP ${closed.status}`);
          } catch (error) {
            failure ||= error;
          }
        }
        if (failure) throw failure;
      })().catch(error => fail(error.message));'; then
    status=0
  else
    status=$?
  fi
  rm -f -- "$env_file"
  return "$status"
}

write_omniroute_n8n_router_key() {
  local key="$1" id="$2" tmp
  ensure_stack_secrets_dir || return 1
  tmp="$(mktemp "$STACK_SECRETS_DIR/omniroute-n8n-router.env.tmp.XXXXXX")"
  TEMP_SECRET_FILES+=("$tmp")
  chmod 600 "$tmp"
  {
    printf 'OMNIROUTE_N8N_API_KEY=%s\n' "$key"
    printf 'OMNIROUTE_N8N_API_KEY_ID=%s\n' "$id"
  } > "$tmp"
  mv "$tmp" "$OMNIROUTE_N8N_KEY_ENV"
  chmod 600 "$OMNIROUTE_N8N_KEY_ENV"
}

stored_omniroute_n8n_router_key() {
  [[ -f "$OMNIROUTE_N8N_KEY_ENV" && ! -L "$OMNIROUTE_N8N_KEY_ENV" ]] || return 0
  chmod 600 "$OMNIROUTE_N8N_KEY_ENV"
  env_value "$OMNIROUTE_N8N_KEY_ENV" OMNIROUTE_N8N_API_KEY
}

validate_omniroute_n8n_router_key() {
  local key="$1"
  [[ -n "$key" ]] || return 1
  if printf '%s' "$key" | compose exec -T omniroute node -e '
    let key="";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", chunk => key += chunk);
    process.stdin.on("end", async () => {
      try {
        const response = await fetch("http://127.0.0.1:20129/v1/models", {
          headers: {Authorization: `Bearer ${key}`},
          signal: AbortSignal.timeout(10000),
        });
        process.exit(response.ok ? 0 : 1);
      } catch {
        process.exit(1);
      }
    });'; then
    return 0
  fi
  return 1
}

create_omniroute_n8n_router_key() {
  local output key id
  output="$(compose exec -T \
    -e 'HERMES_N8N_SERVICE_KEY_NAME=n8n (content-manager stack)' \
    omniroute node -e '
      (async () => {
        const managementKey = process.env.OMNIROUTE_API_KEY || "";
        if (!managementKey) {
          throw new Error("OMNIROUTE_API_KEY management bootstrap credential is missing");
        }
        const response = await fetch("http://127.0.0.1:20128/api/keys", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${managementKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({name: process.env.HERMES_N8N_SERVICE_KEY_NAME}),
          signal: AbortSignal.timeout(15000),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.key || !data.id) {
          throw new Error(`OmniRoute key provisioning returned HTTP ${response.status}`);
        }
        process.stdout.write(`OMNIROUTE_N8N_API_KEY=${data.key}\n`);
        process.stdout.write(`OMNIROUTE_N8N_API_KEY_ID=${data.id}\n`);
      })().catch(error => {
        console.error(error.message);
        process.exit(1);
      });
    ')" || {
      printf '%s\n' \
        'OmniRoute could not auto-provision the dedicated n8n API key.' \
        'Ensure the current OmniRoute image supports management POST /api/keys and OMNIROUTE_MANAGEMENT_API_KEY is configured.' >&2
      return 1
    }

  key="$(sed -n 's/^OMNIROUTE_N8N_API_KEY=//p' <<< "$output" | tail -n1)"
  id="$(sed -n 's/^OMNIROUTE_N8N_API_KEY_ID=//p' <<< "$output" | tail -n1)"
  [[ -n "$key" && -n "$id" ]] || {
    printf 'OmniRoute did not return the dedicated n8n API key.\n' >&2
    return 1
  }

  write_omniroute_n8n_router_key "$key" "$id" || return 1
  printf '%s' "$key"
}

provision_n8n_router_key() {
  local profiles output key
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," == *,smart-router,* ]]; then
    key="$(env_value "$ENV_FILE" SMART_ROUTER_CLIENT_API_KEY)"
    [[ -n "$key" ]] || { printf 'SMART_ROUTER_CLIENT_API_KEY is missing.\n' >&2; return 1; }
    printf '%s' "$key"
    return 0
  fi
  if [[ ",$profiles," == *,omniroute,* ]]; then
    key="$(stored_omniroute_n8n_router_key)"
    if [[ -n "$key" ]] && validate_omniroute_n8n_router_key "$key"; then
      printf '%s' "$key"
      return 0
    fi
    create_omniroute_n8n_router_key
    return $?
  fi
  output="$(compose exec -T -e PROVISION_HERMES=false -e PROVISION_OPENWEBUI=false \
    -e PROVISION_SMART_ROUTER=false -e PROVISION_N8N=true nine-router \
    node --input-type=module < "$ROOT_DIR/scripts/bootstrap-openwebui.mjs")"
  key="$(sed -n 's/^N8N_API_KEY=//p' <<< "$output" | tail -n1)"
  [[ -n "$key" ]] || { printf '9router did not return the dedicated n8n key.\n' >&2; return 1; }
  printf '%s' "$key"
}

run_n8n_reconciler_with_token() {
  local mcp_token="$1" previous_mcp_token="${2:-$1}" requested_mode="${3:-}" mode api_key router_key image env_file status
  local profiles router_base_url router_model previous_router_base_url state_dir state_tmp
  mode="${requested_mode:-$(n8n_mcp_mode)}"
  api_key="$(n8n_api_key)"
  [[ -n "$api_key" ]] || {
    printf 'No n8n bootstrap API key is stored. Run ./manage.sh set-n8n-api-key.\n' >&2
    return 1
  }
  router_key="$(provision_n8n_router_key)" || return 1
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," == *,smart-router,* ]]; then
    router_base_url="http://smart-router:8080/v1"
    router_model="auto"
  elif [[ ",$profiles," == *,omniroute,* ]]; then
    router_base_url="http://omniroute:20129/v1"
    router_model="auto/best-chat"
  else
    router_base_url="http://nine-router:20128/v1"
    router_model="ai"
  fi
  previous_router_base_url="$router_base_url"
  if [[ -f "$N8N_BOOTSTRAP_STATE" ]]; then
    previous_router_base_url="$(python3 - "$N8N_BOOTSTRAP_STATE" "$router_base_url" <<'PY'
import json
import sys
try:
    state = json.load(open(sys.argv[1], encoding="utf-8"))
    print(state.get("routerBaseUrl") or sys.argv[2])
except Exception:
    print(sys.argv[2])
PY
)"
  fi
  ensure_stack_secrets_dir || return 1
  if [[ -e "$N8N_BOOTSTRAP_STATE" ]]; then
    [[ -f "$N8N_BOOTSTRAP_STATE" && ! -L "$N8N_BOOTSTRAP_STATE" ]] || {
      printf 'Refusing unsafe n8n bootstrap state path.\n' >&2
      return 1
    }
    chmod 600 "$N8N_BOOTSTRAP_STATE"
  fi
  state_dir="$(mktemp -d "$STACK_SECRETS_DIR/n8n-reconcile-state.tmp.XXXXXX")"
  TEMP_SECRET_FILES+=("$state_dir")
  chmod 700 "$state_dir"
  if [[ -f "$N8N_BOOTSTRAP_STATE" ]]; then
    cp --preserve=mode,timestamps "$N8N_BOOTSTRAP_STATE" "$state_dir/n8n-bootstrap-state.json"
  fi
  env_file="$(mktemp "$STACK_SECRETS_DIR/n8n-reconcile.env.tmp.XXXXXX")"
  TEMP_SECRET_FILES+=("$env_file")
  chmod 600 "$env_file"
  # The shared tool registry lives in the Content Manager config directory; it
  # is mounted read-only so the bootstrap can report the n8n entries without
  # copying the file into the container image.
  registry_args=()
  if [[ -d "$ROOT_DIR/data/content-manager/config" ]]; then
    registry_args=(-v "$ROOT_DIR/data/content-manager/config:/tools:ro")
  fi
  {
    printf 'N8N_API_URL=http://n8n:5678/api/v1\n'
    printf 'N8N_API_KEY=%s\n' "$api_key"
    printf 'N8N_MCP_MODE=%s\n' "$mode"
    [[ -n "$mcp_token" ]] && printf 'N8N_TRIGGER_MCP_TOKEN=%s\n' "$mcp_token"
    [[ -n "$previous_mcp_token" ]] && printf 'N8N_PREVIOUS_TRIGGER_MCP_TOKEN=%s\n' "$previous_mcp_token"
    printf 'NINEROUTER_API_KEY=%s\n' "$router_key"
    printf 'N8N_ROUTER_BASE_URL=%s\n' "$router_base_url"
    printf 'N8N_PREVIOUS_ROUTER_BASE_URL=%s\n' "$previous_router_base_url"
    printf 'N8N_CHAT_MODEL=%s\n' "$router_model"
    printf 'N8N_STATE_FILE=/state/n8n-bootstrap-state.json\n'
    if [[ "${#registry_args[@]}" -gt 0 ]]; then
      printf 'N8N_TOOLS_REGISTRY=/tools/tools.json\n'
    fi
  } > "$env_file"
  image_repo="$(env_value "$ENV_FILE" N8N_IMAGE_REPOSITORY)"; image_repo="${image_repo:-n8nio/n8n}"
  image_tag="$(env_value "$ENV_FILE" N8N_IMAGE_TAG)"; image_tag="${image_tag:-latest}"
  image="$image_repo:$image_tag"
  if "${DOCKER[@]}" run --rm --network hermes-9router-net \
    --user "$(id -u):$(id -g)" \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:size=16m,mode=1777 \
    -v "$ROOT_DIR/scripts:/stack/scripts:ro" \
    ${registry_args[@]+"${registry_args[@]}"} \
    -v "$state_dir:/state" \
    --env-file "$env_file" \
    --entrypoint node "$image" \
    /stack/scripts/bootstrap-n8n.mjs; then
    status=0
    state_tmp="$(mktemp "$STACK_SECRETS_DIR/n8n-bootstrap-state.tmp.XXXXXX")"
    if cp "$state_dir/n8n-bootstrap-state.json" "$state_tmp"; then
      chmod 600 "$state_tmp"
      mv "$state_tmp" "$N8N_BOOTSTRAP_STATE"
    else
      rm -f -- "$state_tmp"
      status=1
    fi
  else
    status=$?
  fi
  rm -f -- "$env_file"
  rm -rf -- "$state_dir"
  return "$status"
}

run_n8n_reconciler() {
  local mode mcp_token
  mode="$(n8n_mcp_mode)" || return 1
  migrate_legacy_trigger_env || return 1
  mcp_token="$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN)"
  if [[ "$mode" == trigger && -z "$mcp_token" ]]; then
    printf 'No Trigger MCP token is configured. Run ./manage.sh configure and select Trigger mode.\n' >&2
    return 1
  fi
  run_n8n_reconciler_with_token "$mcp_token" "$mcp_token" "$mode"
}

run_n8n_verifier() {
  local api_key mode mcp_token="" mcp_url="" image env_file status profiles router_health_url state_dir
  [[ -f "$N8N_BOOTSTRAP_STATE" && ! -L "$N8N_BOOTSTRAP_STATE" ]] || {
    printf 'Managed n8n state is missing or unsafe; run bootstrap-n8n.\n' >&2
    return 1
  }
  mode="$(n8n_mcp_mode)" || return 1
  migrate_legacy_trigger_env || return 1
  case "$mode" in
    instance)
      mcp_token="$(env_value "$HERMES_ENV" N8N_INSTANCE_MCP_TOKEN)"
      mcp_url="$(env_value "$HERMES_ENV" N8N_INSTANCE_MCP_URL)"
      ;;
    trigger)
      mcp_token="$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN)"
      mcp_url="$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_URL)"
      ;;
  esac
  if [[ "$mode" != off && ( -z "$mcp_token" || -z "$mcp_url" ) ]]; then
    printf 'Hermes n8n %s MCP configuration is incomplete.\n' "$mode" >&2
    return 1
  fi
  api_key="$(n8n_api_key)"
  profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
  if [[ ",$profiles," == *,smart-router,* ]]; then
    router_health_url="http://smart-router:8080/ready"
  elif [[ ",$profiles," == *,omniroute,* ]]; then
    router_health_url="http://omniroute:20128/api/monitoring/health"
  else
    router_health_url="http://nine-router:20128/api/health"
  fi
  ensure_stack_secrets_dir || return 1
  env_file="$(mktemp "$STACK_SECRETS_DIR/n8n-verify.env.tmp.XXXXXX")"
  TEMP_SECRET_FILES+=("$env_file")
  chmod 600 "$env_file"
  state_dir="$(mktemp -d "$STACK_SECRETS_DIR/n8n-verify-state.tmp.XXXXXX")"
  TEMP_SECRET_FILES+=("$state_dir")
  chmod 700 "$state_dir"
  cp --preserve=mode,timestamps "$N8N_BOOTSTRAP_STATE" "$state_dir/n8n-bootstrap-state.json"
  {
    printf 'N8N_API_URL=http://n8n:5678/api/v1\n'
    [[ -n "$api_key" ]] && printf 'N8N_API_KEY=%s\n' "$api_key"
    printf 'N8N_MCP_MODE=%s\n' "$mode"
    case "$mode" in
      instance)
        printf 'N8N_INSTANCE_MCP_URL=%s\n' "$mcp_url"
        printf 'N8N_INSTANCE_MCP_TOKEN=%s\n' "$mcp_token"
        ;;
      trigger)
        printf 'N8N_TRIGGER_MCP_URL=%s\n' "$mcp_url"
        printf 'N8N_TRIGGER_MCP_TOKEN=%s\n' "$mcp_token"
        ;;
    esac
    printf 'N8N_STATE_FILE=/state/n8n-bootstrap-state.json\n'
    printf 'N8N_ROUTER_HEALTH_URL=%s\n' "$router_health_url"
  } > "$env_file"
  image_repo="$(env_value "$ENV_FILE" N8N_IMAGE_REPOSITORY)"; image_repo="${image_repo:-n8nio/n8n}"
  image_tag="$(env_value "$ENV_FILE" N8N_IMAGE_TAG)"; image_tag="${image_tag:-latest}"
  image="$image_repo:$image_tag"
  if "${DOCKER[@]}" run --rm --network hermes-9router-net \
    --user "$(id -u):$(id -g)" \
    --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:size=16m,mode=1777 \
    -v "$ROOT_DIR/scripts:/stack/scripts:ro" \
    -v "$state_dir:/state:ro" \
    --env-file "$env_file" \
    --entrypoint node "$image" \
    /stack/scripts/verify-n8n.mjs; then
    status=0
  else
    status=$?
  fi
  rm -f -- "$env_file"
  rm -rf -- "$state_dir"
  return "$status"
}

check_hermes_file() {
  local path="$1" label="$2" container_path
  [[ -f "$path" ]] || return 0
  container_path="/opt/data/${path#"$ROOT_DIR/data/hermes/"}"
  if compose exec -T hermes setpriv --reuid=hermes --regid=hermes --clear-groups \
    test -r "$container_path" >/dev/null 2>&1; then
    printf '%s: readable by Hermes gateway\n' "$label"
  else
    printf 'WARNING: %s is not readable by the Hermes gateway user. Run ./manage.sh configure to repair ownership.\n' "$label"
  fi
}

command="${1:-}"
case "$command" in
  menu) interactive_menu ;;
  services|services-menu) services_menu ;;
  router|router-menu) router_menu ;;
  hermes|hermes-menu) hermes_menu ;;
  n8n|n8n-menu) n8n_menu ;;
  content|content-menu) content_menu ;;
  media|media-menu) media_menu ;;
  panel|panel-menu) panel_menu ;;
  panel-enable) panel_enable ;;
  panel-disable) panel_disable ;;
  panel-status) panel_status ;;
  panel-token) panel_token ;;
  panel-rotate-token) panel_rotate_token ;;
  panel-build) panel_build ;;
  storage|storage-menu) storage_menu ;;
  s3-status) s3_status ;;
  s3-enable) shift; s3_enable "$@" ;;
  s3-disable) s3_disable ;;
  s3-verify) shift; s3_verify "$@" ;;
  s3-keys) shift; s3_keys "${1:-}" ;;
  s3-guide) s3_guide ;;
  execution|execution-menu) execution_menu ;;
  maintenance|maintenance-menu) maintenance_menu ;;
  security|security-menu) security_menu ;;
  n8n-status) n8n_status ;;
  content-status) content_status ;;
  content-connect-instagram) content_connect_instagram ;;
  instagram-media-status) ig_media_status ;;
  instagram-media-enable) shift; ig_media_enable "${1:-}" ;;
  instagram-media-disable) ig_media_disable ;;
  instagram-media-tunnel-off) ig_media_tunnel_off ;;
  instagram-media-verify) ig_media_verify ;;
  content-configure) content_configure ;;
  media-status) media_status ;;
  media-guide) media_guide ;;
  media-configure) media_configure ;;
  pipeline-status) pipeline_status ;;
  uninstall)
    shift
    uninstall_stack "${1:-}"
    ;;
  start)
    # A missing token makes the panel exit; create one before a full start
    # when the optional profile is enabled.
    panel_enabled && panel_token >/dev/null
    compose up -d --build
    ;;
  stop) compose stop ;;
  restart) compose restart ;;
  update)
    shift
    ops update "$@"
    ;;
  status) if [[ "${2:-}" == --json ]]; then ops status --json; else compose ps; fi ;;
  logs)
    case "${2:-}" in
      "") compose logs -f --tail=100 ;;
      hermes) compose logs -f --tail=100 hermes ;;
      9router|nine-router) compose logs -f --tail=100 nine-router ;;
      omniroute|omni) compose logs -f --tail=100 omniroute ;;
      smart-router|router) compose logs -f --tail=100 smart-router ;;
      webui|open-webui) compose logs -f --tail=100 open-webui ;;
      n8n) compose logs -f --tail=100 n8n ;;
      content|content-bot) compose logs -f --tail=100 content-bot ;;
      media|media-studio) compose logs -f --tail=100 media-studio ;;
      caddy) compose logs -f --tail=100 caddy ;;
      rustfs|s3) compose logs -f --tail=100 rustfs ;;
      *) printf 'Choose hermes, 9router, omniroute, smart-router, webui, n8n, content, media, caddy, or rustfs.\n' >&2; exit 2 ;;
    esac
    ;;
  dashboard-access)
    shift
    hermes_dashboard_access "${1:-}"
    ;;
  migrate-hermes-permissions)
    shift
    case "$#:${1:-}" in
      0:) dry_run=false ;;
      1:--dry-run) dry_run=true ;;
      *) printf 'Usage: ./manage.sh migrate-hermes-permissions [--dry-run]\n' >&2; exit 2 ;;
    esac
    migrate_hermes_permissions "$dry_run"
    ;;
  doctor)
    compose config --quiet
    printf 'Compose configuration: valid\n'
    compose ps
    if [[ -f "$HERMES_ENV" ]]; then
      mode="$(stat -c '%a' "$HERMES_ENV")"
      printf 'Hermes secret file mode: %s\n' "$mode"
      [[ "$mode" == 600 ]] || printf 'WARNING: expected data/hermes/.env mode 600\n'
    fi
    profiles="$(sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE")"
    if [[ "$profiles" == *hermes* ]]; then
      check_hermes_file "$ROOT_DIR/data/hermes/config.yaml" "Hermes config"
      check_hermes_file "$HERMES_ENV" "Hermes secret file"
      check_hermes_log_permissions
      if compose exec -T hermes sh -c \
        'test -f /opt/data/plugins/stack-package-policy/plugin.yaml && test ! -w /opt/data/plugins/stack-package-policy/plugin.yaml' \
        >/dev/null 2>&1; then
        printf 'Hermes package policy plugin: mounted read-only\n'
      else
        printf 'WARNING: stack-package-policy is missing or writable inside Hermes.\n'
      fi
      configured_turns="$(hermes_agent_max_turns || true)"
      effective_turns="$(compose exec -T hermes sh -lc 'printf "%s" "${HERMES_MAX_ITERATIONS:-}"' 2>/dev/null || true)"
      printf 'Hermes agent iteration budget: configured %s, effective %s\n' \
        "${configured_turns:-unset}" "${effective_turns:-unknown}"
      if [[ -n "$configured_turns" ]] && (( configured_turns <= 30 )); then
        printf 'WARNING: an iteration budget of %s stops multi-step tool tasks early. Raise it with ./manage.sh set-agent-max-turns 90.\n' \
          "$configured_turns"
      fi
      if [[ -n "$configured_turns" && -n "$effective_turns" && "$configured_turns" != "$effective_turns" ]]; then
        printf 'WARNING: the running gateway budget does not match config.yaml; recreate Hermes.\n'
      fi
      dashboard_enabled="$(env_value "$ENV_FILE" HERMES_DASHBOARD)"
      if [[ "$dashboard_enabled" == 1 ]]; then
        dashboard_user="$(env_value "$ENV_FILE" HERMES_DASHBOARD_BASIC_AUTH_USERNAME)"
        dashboard_hash="$(env_value "$ENV_FILE" HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH)"
        dashboard_secret="$(env_value "$ENV_FILE" HERMES_DASHBOARD_BASIC_AUTH_SECRET)"
        if [[ -z "$dashboard_user" || -z "$dashboard_hash" || -z "$dashboard_secret" ]]; then
          printf 'WARNING: HERMES_DASHBOARD is enabled but Basic Auth credentials are incomplete. The gateway refuses to bind a non-loopback dashboard without an auth provider. Run ./manage.sh configure to provision credentials.\n'
        else
          printf 'Hermes dashboard: Basic Auth configured (user %s)\n' "$dashboard_user"
        fi
        if [[ -f "$HERMES_DASHBOARD_ACCESS_FILE" ]]; then
          dashboard_access_mode="$(stat -c '%a' "$HERMES_DASHBOARD_ACCESS_FILE")"
          [[ "$dashboard_access_mode" == 600 ]] || printf 'WARNING: expected %s mode 600, found %s\n' "$HERMES_DASHBOARD_ACCESS_FILE" "$dashboard_access_mode"
        elif [[ -n "$dashboard_user" ]]; then
          printf 'WARNING: %s is missing; dashboard-access --show-password will not work until Hermes is reconfigured.\n' "$HERMES_DASHBOARD_ACCESS_FILE"
        fi
      fi
      if grep -q '^[[:space:]]*- stack-package-policy[[:space:]]*$' "$ROOT_DIR/data/hermes/config.yaml"; then
        printf 'Hermes package policy plugin: enabled in config\n'
      else
        printf 'WARNING: stack-package-policy is not enabled in Hermes config.\n'
      fi
      if compose exec -T hermes sh -lc '
        cd /opt/hermes
        /opt/hermes/.venv/bin/hermes plugins list --enabled --user --plain 2>/dev/null \
          | grep -Eq "enabled[[:space:]]+user[[:space:]]+[^[:space:]]+[[:space:]]+stack-package-policy"
      '; then
        printf 'Hermes package policy plugin: registered at runtime\n'
      else
        printf 'WARNING: stack-package-policy is not registered as an enabled user plugin.\n'
      fi
      if compose exec -T hermes sh -lc '
        cd /opt/hermes
        /opt/hermes/.venv/bin/hermes plugins list --enabled --user --plain 2>/dev/null \
          | grep -Eq "enabled[[:space:]]+user[[:space:]]+[^[:space:]]+[[:space:]]+stack-execution-policy"
      '; then
        printf 'Hermes execution policy plugin: registered at runtime\n'
      else
        printf 'WARNING: stack-execution-policy is not registered as an enabled user plugin.\n'
      fi
      telegram_tools="$(compose exec -T hermes sh -lc '
        cd /opt/hermes
        /opt/hermes/.venv/bin/hermes tools list --platform telegram 2>/dev/null
      ' 2>/dev/null || true)"
      if grep -Eq "enabled[[:space:]]+stack-package-policy([[:space:]]|$)" <<< "$telegram_tools"; then
        printf 'Hermes package policy toolset: enabled for Telegram\n'
      else
        printf 'WARNING: stack-package-policy is not enabled for Telegram.\n'
      fi
      if grep -Eq "enabled[[:space:]]+stack-execution-policy([[:space:]]|$)" <<< "$telegram_tools"; then
        printf 'Hermes execution policy toolset: enabled for Telegram\n'
      else
        printf 'WARNING: stack-execution-policy is not enabled for Telegram.\n'
      fi
      # Upstream terminal/code_execution are a local decision, so report their
      # real state rather than asserting one. Enabled means an approved command
      # runs as the gateway uid, which can read /opt/data/.env.
      upstream_terminal=unknown
      if [[ -n "$telegram_tools" ]]; then
        if grep -Eq "disabled[[:space:]]+terminal([[:space:]]|$)" <<< "$telegram_tools" \
          && grep -Eq "disabled[[:space:]]+code_execution([[:space:]]|$)" <<< "$telegram_tools"; then
          upstream_terminal=disabled
        else
          upstream_terminal=enabled
        fi
      fi
      if [[ "$upstream_terminal" == disabled ]]; then
        printf 'Upstream terminal/code execution: disabled (isolated stack tools only)\n'
      elif [[ "$upstream_terminal" == enabled ]]; then
        printf 'Upstream terminal/code execution: ENABLED — approved commands run as the\n'
        printf '  gateway uid inside hermes-agent and can read /opt/data/.env. Keep\n'
        printf '  approvals.mode=manual and the Telegram allowlist tight.\n'
      else
        printf 'WARNING: could not determine the upstream terminal state.\n'
      fi
    fi
    if [[ "$profiles" == *smart-router* ]]; then
      compose exec -T smart-router python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=5)'
      compose exec -T smart-router python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=5)'
      printf 'Smart Router health/readiness: valid\n'
    fi
    if [[ -d "$STACK_SECRETS_DIR" ]]; then
      secret_dir_mode="$(stat -c '%a' "$STACK_SECRETS_DIR")"
      printf 'Stack secret directory mode: %s\n' "$secret_dir_mode"
      [[ "$secret_dir_mode" == 700 ]] || printf 'WARNING: expected data/stack-secrets mode 700\n'
      # The bootstrap containers drop all capabilities, so a mismatched owner
      # makes mode 700 unreadable to them even when manage.sh runs as root.
      secret_dir_owner="$(stat -c '%u:%g' "$STACK_SECRETS_DIR")"
      if [[ "$secret_dir_owner" == "$(id -u):$(id -g)" ]]; then
        printf 'Stack secret directory owner: matches the invoking user\n'
      else
        printf 'WARNING: data/stack-secrets is owned by %s, not %s:%s; n8n bootstrap will fail with EACCES\n' \
          "$secret_dir_owner" "$(id -u)" "$(id -g)"
      fi
      if [[ -f "$N8N_BOOTSTRAP_ENV" ]]; then
        bootstrap_mode="$(stat -c '%a' "$N8N_BOOTSTRAP_ENV")"
        printf 'n8n bootstrap secret mode: %s\n' "$bootstrap_mode"
        [[ "$bootstrap_mode" == 600 ]] || printf 'WARNING: expected n8n bootstrap secret mode 600\n'
      fi
      if [[ -d "$(execution_root)" ]]; then
        printf 'Execution features: %s\n' "$(execution_features | sed 's/^$/off/')"
        execution_gateway_uid="$(execution_hermes_uid)"
        for execution_file in "$(execution_root)/control-secret" "$(execution_root)/users"; do
          if [[ -f "$execution_file" && ! -L "$execution_file" \
            && "$(stat -c %a "$execution_file")" == 640 \
            && "$(stat -c %u "$execution_file")" == "$execution_gateway_uid" \
            && "$(stat -c %g "$execution_file")" == 10003 ]]; then
            printf 'Execution policy %s: safe owner %s, group 10003, mode 640\n' \
              "${execution_file##*/}" "$execution_gateway_uid"
          else
            printf 'WARNING: shared execution policy %s is missing or has unsafe permissions.\n' "${execution_file##*/}"
          fi
        done
        for execution_file in "$(execution_root)/approval-request-secret" \
          "$(execution_root)/approval-signing-key.pem" "$(execution_root)/approval-public-key.pem" \
          "$(execution_root)/approval-bot-token" "$(execution_root)/ssh-profile-integrity-secret"; do
          if [[ -f "$execution_file" && ! -L "$execution_file" && "$(stat -c %a "$execution_file")" == 600 ]]; then
            printf 'Execution policy %s: safe private mode 600\n' "${execution_file##*/}"
          else
            printf 'WARNING: private execution policy %s is missing, unsafe, or not mode 600.\n' "${execution_file##*/}"
          fi
        done
        users="$(execution_users)"
        if [[ -z "$users" ]] || execution_users_valid "$users"; then
          printf 'Execution user policy: valid Telegram subset\n'
        else
          printf 'WARNING: execution users are not a subset of TELEGRAM_ALLOWED_USERS.\n'
        fi
        rendered="$(compose config 2>/dev/null || true)"
        rendered_json="$(compose config --format json 2>/dev/null || true)"
        socket_owners="$(python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except (json.JSONDecodeError, TypeError):
    raise SystemExit(0)
for name, service in (data.get("services") or {}).items():
    for volume in service.get("volumes") or []:
        source = volume.get("source", "") if isinstance(volume, dict) else str(volume).split(":", 1)[0]
        target = volume.get("target", "") if isinstance(volume, dict) else ""
        if source == "/var/run/docker.sock" or target == "/var/run/docker.sock":
            print(name)
' <<< "$rendered_json")"
        socket_count="$(grep -c . <<< "$socket_owners" || true)"
        if [[ "$socket_count" == 1 && "$socket_owners" == execution-docker-broker ]]; then
          printf 'Docker socket boundary: mounted once, Docker broker only\n'
        else
          printf 'WARNING: expected exactly one Docker socket mount on Docker broker; found %s (%s).\n' \
            "$socket_count" "${socket_owners:-none}"
        fi
        if grep -A35 '^  hermes:' <<< "$rendered" | grep -q docker.sock; then
          printf 'WARNING: Hermes has the Docker socket; remove it immediately.\n'
        fi
        if grep -A50 '^  execution-ssh-broker:' <<< "$rendered" | grep -q docker.sock; then
          printf 'WARNING: SSH broker has the Docker socket; remove it immediately.\n'
        fi
        approval_token_count="$(grep -c '/run/secrets/execution-approval-bot-token' <<< "$rendered" || true)"
        [[ "$approval_token_count" == 2 ]] \
          && printf 'Approval bot token boundary: approver mount and environment only\n' \
          || printf 'WARNING: approval bot token wiring count is unexpected: %s.\n' "$approval_token_count"
        hermes_block="$(grep -A55 '^  hermes:' <<< "$rendered")"
        if grep -q 'execution-approval\|execution-approval-bot-token' <<< "$hermes_block"; then
          printf 'WARNING: Hermes has independent approval authority; disable execution immediately.\n'
        fi
        approver_block="$(grep -A55 '^  execution-approver:' <<< "$rendered")"
        if grep -q 'docker.sock\|/profiles\|execution-ssh-profile-integrity' <<< "$approver_block"; then
          printf 'WARNING: the approver has Docker or SSH execution authority.\n'
        fi
        ssh_integrity_count="$(grep -c '/run/secrets/execution-ssh-profile-integrity' <<< "$rendered" || true)"
        [[ "$ssh_integrity_count" == 2 ]] \
          && printf 'SSH password integrity boundary: SSH broker mount and environment only\n' \
          || printf 'WARNING: SSH password integrity secret wiring count is unexpected: %s.\n' "$ssh_integrity_count"
        if grep -q '^[[:space:]]*- stack-execution-policy[[:space:]]*$' "$ROOT_DIR/data/hermes/config.yaml"; then
          printf 'Hermes execution policy plugin: enabled in config\n'
        else
          printf 'WARNING: stack-execution-policy is not enabled in Hermes config.\n'
        fi
      fi
    fi
    if [[ "$profiles" == *execution-approval* ]]; then
      compose exec -T execution-approver python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8751/health", timeout=5)'
      printf 'Independent execution approver: healthy\n'
    fi
    if [[ "$profiles" == *execution-docker* ]]; then
      compose exec -T execution-docker-broker python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8750/health", timeout=5)'
      printf 'Docker execution broker: healthy\n'
    fi
    if [[ "$profiles" == *execution-ssh* ]]; then
      compose exec -T execution-ssh-broker python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8750/health", timeout=5)'
      printf 'SSH execution broker: healthy\n'
    fi
    if [[ "$profiles" == *n8n* ]]; then
      compose exec -T n8n node -e \
        "fetch('http://127.0.0.1:5678/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
      printf 'n8n health: valid\n'
      if [[ "$profiles" == *hermes* && -f "$HERMES_ENV" ]]; then
        selected_mcp_mode="$(n8n_mcp_mode 2>/dev/null || true)"
        printf 'Hermes n8n MCP mode: %s\n' "${selected_mcp_mode:-invalid}"
        mcp_url=""
        case "$selected_mcp_mode" in
          instance)
            mcp_url="$(env_value "$HERMES_ENV" N8N_INSTANCE_MCP_URL)"
            if [[ -z "$(env_value "$HERMES_ENV" N8N_INSTANCE_MCP_TOKEN)" ]]; then
              printf 'WARNING: Instance MCP mode is pending a token. Enable it in n8n and run ./manage.sh set-n8n-instance-mcp-token.\n'
            fi
            ;;
          trigger)
            mcp_url="$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_URL)"
            [[ -n "$mcp_url" ]] || mcp_url="$(env_value "$HERMES_ENV" N8N_MCP_URL)"
            ;;
          off) printf 'Hermes -> n8n MCP: disabled; retained n8n objects are not deleted.\n' ;;
        esac
        if [[ -n "$mcp_url" ]]; then
          code="$(compose exec -T -e HERMES_N8N_MCP_URL="$mcp_url" hermes sh -c \
            'curl -s -o /dev/null -w "%{http_code}" --max-time 5 -X POST -H "Content-Type: application/json" --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"doctor\",\"version\":\"1\"}}}" "$HERMES_N8N_MCP_URL"' \
            2>/dev/null || true)"
          case "$code" in
            401|403) printf 'Hermes -> n8n %s MCP endpoint: reachable and rejects anonymous access (HTTP %s)\n' "$selected_mcp_mode" "$code" ;;
            404)
              if [[ "$selected_mcp_mode" == trigger ]]; then
                printf 'Hermes -> n8n Trigger MCP endpoint: HTTP 404; reconcile Trigger mode to publish it.\n'
              else
                printf 'Hermes -> n8n Instance MCP endpoint: HTTP 404; enable Instance-level MCP in n8n Settings.\n'
              fi
              ;;
            000|"") printf 'WARNING: Hermes cannot reach %s over the Docker network.\n' "$mcp_url" ;;
            *) printf 'Hermes -> n8n %s MCP endpoint: unexpected anonymous HTTP %s\n' "$selected_mcp_mode" "$code" ;;
          esac
        fi
      fi
    fi
    if [[ "$profiles" == *caddy* ]]; then
      compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile
    fi
    ;;
  configure) exec "$ROOT_DIR/install.sh" ;;
  set-router-mode)
    mode="${2:-}"
    [[ "$mode" == observe || "$mode" == route ]] || {
      printf 'Mode must be observe or route.\n' >&2
      exit 2
    }
    profiles="$(sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE")"
    [[ ",$profiles," == *,smart-router,* ]] || {
      printf 'Smart Router is not selected. Run ./manage.sh configure first.\n' >&2
      exit 1
    }
    replace_env_value "$ENV_FILE" SMART_ROUTER_MODE "$mode"
    if [[ ",$profiles," == *,omniroute,* ]]; then
      compose up -d omniroute smart-router
    else
      compose up -d nine-router smart-router
    fi
    ready=false
    for _ in {1..60}; do
      if compose exec -T smart-router python -c \
        'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=5)' \
        >/dev/null 2>&1; then
        ready=true
        break
      fi
      sleep 2
    done
    [[ "$ready" == true ]] || {
      printf 'Smart Router was recreated but did not become ready.\n' >&2
      exit 1
    }
    if ! router_runtime_set "{\"router_mode\":\"$mode\"}" >/dev/null; then
      printf 'WARNING: mode was written to .env, but the Operations Center runtime override could not be synchronized.\n' >&2
      printf 'If a previous UI override exists, open System and choose Reset to environment, then retry.\n' >&2
    fi
    printf 'Smart Router mode changed to %s and is ready.\n' "$mode"
    ;;
  show-telegram-users)
    [[ -f "$HERMES_ENV" ]] || { printf 'Hermes is not configured.\n' >&2; exit 1; }
    grep '^TELEGRAM_ALLOWED_USERS=' "$HERMES_ENV" || true
    ;;
  set-telegram-users)
    ids="${2:-}"
    ids="${ids//[[:space:]]/}"
    valid_ids "$ids" || { printf 'Use numeric comma-separated IDs.\n' >&2; exit 2; }
    [[ -f "$HERMES_ENV" ]] || { printf 'Hermes is not configured.\n' >&2; exit 1; }
    replace_env_value "$HERMES_ENV" TELEGRAM_ALLOWED_USERS "$ids"
    restart_hermes
    printf 'Telegram allowlist replaced: %s\n' "$ids"
    ;;
  add-telegram-user)
    new_id="${2:-}"
    [[ "$new_id" =~ ^[0-9]+$ ]] || { printf 'A numeric Telegram ID is required.\n' >&2; exit 2; }
    [[ -f "$HERMES_ENV" ]] || { printf 'Hermes is not configured.\n' >&2; exit 1; }
    current="$(sed -n 's/^TELEGRAM_ALLOWED_USERS=//p' "$HERMES_ENV" | head -n1)"
    if [[ ",$current," == *",$new_id,"* ]]; then
      printf 'Telegram ID %s is already allowed.\n' "$new_id"
      exit 0
    fi
    if [[ -n "$current" ]]; then updated="$current,$new_id"; else updated="$new_id"; fi
    replace_env_value "$HERMES_ENV" TELEGRAM_ALLOWED_USERS "$updated"
    restart_hermes
    printf 'Telegram ID %s added.\n' "$new_id"
    ;;
  restart-hermes)
    profiles="$(sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE")"
    [[ "$profiles" == *hermes* ]] || { printf 'Hermes is not selected.\n' >&2; exit 1; }
    restart_hermes
    printf 'Hermes recreated; config.yaml and MCP tools were reloaded.\n'
    ;;
  set-agent-max-turns)
    require_profiles hermes
    turns="${2:-}"
    [[ -z "${3:-}" && "$turns" =~ ^[0-9]+$ ]] || {
      printf 'Usage: ./manage.sh set-agent-max-turns N\n' >&2
      exit 2
    }
    turns=$((10#$turns))
    (( turns >= AGENT_MAX_TURNS_MIN && turns <= AGENT_MAX_TURNS_MAX )) || {
      printf 'Choose a budget between %s and %s. 90 suits most tasks; 150 suits long exploration. An unbounded budget amplifies stuck tool loops and cost.\n' \
        "$AGENT_MAX_TURNS_MIN" "$AGENT_MAX_TURNS_MAX" >&2
      exit 2
    }
    config_file="$ROOT_DIR/data/hermes/config.yaml"
    ensure_stack_secrets_dir
    turns_backup="$(mktemp "$STACK_SECRETS_DIR/max-turns-config.backup.XXXXXX")"
    TEMP_SECRET_FILES+=("$turns_backup")
    cp --preserve=mode,ownership,timestamps "$config_file" "$turns_backup"
    set_hermes_agent_max_turns "$turns" || {
      cp --preserve=mode,ownership,timestamps "$turns_backup" "$config_file"
      printf 'Hermes config was not modified.\n' >&2
      exit 1
    }
    if ! restart_hermes; then
      cp --preserve=mode,ownership,timestamps "$turns_backup" "$config_file"
      restart_hermes || true
      printf 'Hermes failed to start with the new budget; the prior config was restored.\n' >&2
      exit 1
    fi
    effective="$(compose exec -T hermes sh -lc 'printf "%s" "${HERMES_MAX_ITERATIONS:-}"' 2>/dev/null || true)"
    if [[ -n "$effective" && "$effective" != "$turns" ]]; then
      printf 'WARNING: config.yaml requests %s turns but the gateway reports %s.\n' \
        "$turns" "$effective" >&2
    fi
    printf 'Agent iteration budget set to %s. Reaching it stops the turn safely; send a new message to continue from the summary.\n' "$turns"
    ;;
  set-upstream-terminal)
    require_profiles hermes
    state="${2:-}"
    [[ -z "${3:-}" && ( "$state" == enabled || "$state" == disabled ) ]] || {
      printf 'Usage: ./manage.sh set-upstream-terminal enabled|disabled\n' >&2
      exit 2
    }
    if [[ "$state" == enabled ]]; then
      printf 'Enabling upstream terminal and code_execution.\n'
      printf 'They run as the gateway uid inside hermes-agent, which owns /opt/data/.env:\n'
      printf '  the Telegram bot token, router backend key, API server key, and n8n Instance token.\n'
      printf 'Every call still passes the hardline floor and a manual approval prompt, but an\n'
      printf 'approved command can read those secrets, and prompt injection reaching the model\n'
      printf 'can request one. Rotating afterwards does not undo an exfiltration.\n'
      read -r -p 'Type ENABLE to confirm: ' confirm
      [[ "$confirm" == ENABLE ]] || { printf 'Unchanged.\n' >&2; exit 1; }
    fi
    config_file="$ROOT_DIR/data/hermes/config.yaml"
    ensure_stack_secrets_dir
    terminal_backup="$(mktemp "$STACK_SECRETS_DIR/terminal-config.backup.XXXXXX")"
    TEMP_SECRET_FILES+=("$terminal_backup")
    cp --preserve=mode,ownership,timestamps "$config_file" "$terminal_backup"
    set_upstream_terminal "$state" || {
      cp --preserve=mode,ownership,timestamps "$terminal_backup" "$config_file"
      printf 'Hermes config was not modified.\n' >&2
      exit 1
    }
    if ! restart_hermes; then
      cp --preserve=mode,ownership,timestamps "$terminal_backup" "$config_file"
      restart_hermes || true
      printf 'Hermes failed to start; the prior config was restored.\n' >&2
      exit 1
    fi
    printf 'Upstream terminal/code execution: %s. Run ./manage.sh doctor to confirm.\n' "$state"
    ;;
  execution-status)
    printf 'Execution features: %s\n' "$(execution_features | sed 's/^$/off/')"
    printf 'Execution users: %s\n' "$(execution_users | sed 's/^$/none/')"
    printf 'Policy generation: %s\n' "$(tr -d '[:space:]' < "$(execution_root)/policy-generation" 2>/dev/null || env_value "$ENV_FILE" EXECUTION_POLICY_GENERATION)"
    printf 'Execution admin UI: %s\n' "$(env_value "$ENV_FILE" EXECUTION_ADMIN_ENABLED | sed 's/^$/false/')"
    if [[ -d "$(execution_root)/ssh" ]]; then
      printf 'SSH profiles:'
      found=false
      for profile_dir in "$(execution_root)/ssh"/*; do
        [[ -d "$profile_dir" && ! -L "$profile_dir" ]] || continue
        profile_auth="$(python3 - "$profile_dir/profile.json" <<'PY' 2>/dev/null || printf invalid
import json,sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("auth", "publickey"))
PY
)"
        printf ' %s(%s)' "${profile_dir##*/}" "$profile_auth"; found=true
      done
      [[ "$found" == true ]] || printf ' none'
      printf '\n'
    fi
    ;;
  set-execution-users)
    users="${2:-}"
    [[ -z "${3:-}" && -n "$users" ]] || { printf 'Usage: ./manage.sh set-execution-users ID1,ID2,...\n' >&2; exit 2; }
    execution_users_valid "$users" || {
      printf 'Execution users must be numeric and a subset of TELEGRAM_ALLOWED_USERS.\n' >&2; exit 2;
    }
    write_execution_users "$users"
    rotate_execution_generation
    restart_hermes
    printf 'Execution users updated; all pending operations were revoked.\n'
    ;;
  add-execution-user)
    user="${2:-}"
    [[ -z "${3:-}" && "$user" =~ ^[0-9]+$ ]] || { printf 'Usage: ./manage.sh add-execution-user ID\n' >&2; exit 2; }
    users="$(execution_users)"
    [[ ",$users," == *",$user,"* ]] || users="${users:+$users,}$user"
    execution_users_valid "$users" || { printf 'ID must already be in TELEGRAM_ALLOWED_USERS.\n' >&2; exit 2; }
    write_execution_users "$users"; rotate_execution_generation; restart_hermes
    printf 'Execution user %s added.\n' "$user"
    ;;
  remove-execution-user)
    user="${2:-}"
    [[ -z "${3:-}" && "$user" =~ ^[0-9]+$ ]] || { printf 'Usage: ./manage.sh remove-execution-user ID\n' >&2; exit 2; }
    users="$(execution_users | tr ',' '\n' | grep -vx "$user" | paste -sd, -)"
    [[ -n "$users" ]] && write_execution_users "$users" || write_execution_users ""
    rotate_execution_generation; restart_hermes
    printf 'Execution user %s removed; pending operations revoked.\n' "$user"
    ;;
  enable-execution|disable-execution)
    requested_feature="${2:-}"
    [[ -z "${3:-}" && "$requested_feature" =~ ^(sandbox|ssh|docker|all)$ ]] || {
      printf 'Usage: ./manage.sh %s sandbox|ssh|docker|all\n' "$1" >&2; exit 2;
    }
    if [[ "$1" == enable-execution ]]; then
      features="$(set_execution_feature "$requested_feature" true)"
    else
      features="$(set_execution_feature "$requested_feature" false)"
    fi
    ensure_execution_paths
    if [[ "$1" == enable-execution ]]; then
      [[ -n "$(execution_users)" ]] || { printf 'Set execution users first.\n' >&2; exit 1; }
      [[ -s "$(execution_root)/control-secret" \
        && -s "$(execution_root)/approval-bot-token" \
        && -s "$(execution_root)/approval-request-secret" \
        && -s "$(execution_root)/approval-signing-key.pem" \
        && -s "$(execution_root)/approval-public-key.pem" ]] || {
        printf 'Configure the dedicated approval bot first: ./manage.sh set-execution-approval-bot-token\n' >&2; exit 1;
      }
      if [[ ",$features," == *,ssh,* && ! -s "$(execution_root)/ssh-profile-integrity-secret" ]]; then
        tmp="$(mktemp "$(execution_root)/ssh-profile-integrity-secret.tmp.XXXXXX")"
        random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"
        mv "$tmp" "$(execution_root)/ssh-profile-integrity-secret"
      fi
      enable_hermes_execution_plugin
      replace_env_value "$ENV_FILE" EXECUTION_DOCKER_GID "$(stat -c %g /var/run/docker.sock 2>/dev/null || printf 999)"
      replace_env_value "$ENV_FILE" EXECUTION_WORKSPACE_HOST_PATH "$ROOT_DIR/data/execution-workspace"
      [[ -n "$(env_value "$ENV_FILE" EXECUTION_SANDBOX_IMAGE)" ]] || \
        replace_env_value "$ENV_FILE" EXECUTION_SANDBOX_IMAGE \
          'python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419'
      if [[ ",$features," == *,local,* ]]; then
        sandbox_image="$(env_value "$ENV_FILE" EXECUTION_SANDBOX_IMAGE)"
        [[ "$sandbox_image" =~ @sha256:[0-9a-f]{64}$ ]] || {
          printf 'EXECUTION_SANDBOX_IMAGE must use an immutable sha256 digest.\n' >&2
          exit 1
        }
        "${DOCKER[@]}" pull "$sandbox_image"
      fi
    fi
    apply_execution_features "$features"
    printf 'Execution features now: %s\n' "${features:-off}"
    ;;
  set-execution-approval-bot-token)
    [[ -z "${2:-}" ]] || { printf 'Do not pass Telegram tokens in argv.\n' >&2; exit 2; }
    ensure_execution_paths
    sync_execution_admin_metadata
    read -r -s -p 'Dedicated execution approval Telegram bot token: ' token
    printf '\n' >&2
    [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{20,}$ ]] || {
      printf 'The Telegram bot token format is invalid.\n' >&2; exit 2;
    }
    hermes_bot_token="$(env_value "$HERMES_ENV" TELEGRAM_BOT_TOKEN)"
    [[ -z "$hermes_bot_token" || "$token" != "$hermes_bot_token" ]] || {
      printf 'The execution approver must use a different Telegram bot from Hermes.\n' >&2
      exit 2
    }
    root="$(execution_root)"
    tmp="$(mktemp "$root/approval-bot-token.tmp.XXXXXX")"
    printf '%s\n' "$token" > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$root/approval-bot-token"
    if [[ ! -s "$root/control-secret" ]]; then
      tmp="$(mktemp "$root/control-secret.tmp.XXXXXX")"
      random_hex 32 > "$tmp"; chown "$(execution_hermes_uid):10003" "$tmp"; chmod 640 "$tmp"; mv "$tmp" "$root/control-secret"
    fi
    if [[ ! -s "$root/approval-request-secret" ]]; then
      tmp="$(mktemp "$root/approval-request-secret.tmp.XXXXXX")"
      random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$root/approval-request-secret"
    fi
    if [[ ! -s "$root/ssh-profile-integrity-secret" ]]; then
      tmp="$(mktemp "$root/ssh-profile-integrity-secret.tmp.XXXXXX")"
      random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$root/ssh-profile-integrity-secret"
    fi
    if [[ ! -s "$root/approval-signing-key.pem" || ! -s "$root/approval-public-key.pem" ]]; then
      private_tmp="$(mktemp "$root/approval-signing-key.tmp.XXXXXX")"
      public_tmp="$(mktemp "$root/approval-public-key.tmp.XXXXXX")"
      openssl genpkey -algorithm ED25519 -out "$private_tmp" >/dev/null 2>&1
      openssl pkey -in "$private_tmp" -pubout -out "$public_tmp" >/dev/null 2>&1
      chown 10003:10003 "$private_tmp" "$public_tmp"
      chmod 600 "$private_tmp" "$public_tmp"
      mv "$private_tmp" "$root/approval-signing-key.pem"
      mv "$public_tmp" "$root/approval-public-key.pem"
    fi
    rotate_execution_generation
    printf 'Dedicated execution approval bot configured without printing its token. Execution remains off until explicitly enabled.\n'
    ;;
  enable-execution-admin)
    [[ -z "${2:-}" ]] || { printf 'Usage: ./manage.sh enable-execution-admin\n' >&2; exit 2; }
    ensure_execution_paths; sync_execution_admin_metadata
    root="$(execution_root)"
    if [[ ! -s "$root/admin-key" ]]; then
      tmp="$(mktemp "$root/admin-key.tmp.XXXXXX")"; random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$root/admin-key"
    fi
    replace_env_value "$ENV_FILE" EXECUTION_ADMIN_ENABLED true
    [[ -n "$(env_value "$ENV_FILE" EXECUTION_ADMIN_PORT)" ]] || replace_env_value "$ENV_FILE" EXECUTION_ADMIN_PORT 8752
    sync_execution_profiles
    compose pull execution-admin
    compose up -d --no-deps --force-recreate execution-admin
    printf 'Execution admin UI enabled on %s:%s. The separate admin key was not printed.\n' "$(env_value "$ENV_FILE" EXECUTION_ADMIN_BIND_IP | sed 's/^$/127.0.0.1/')" "$(env_value "$ENV_FILE" EXECUTION_ADMIN_PORT | sed 's/^$/8752/')"
    printf 'Use ./manage.sh show-execution-admin-key from a trusted terminal when you need to connect the Operations Center page.\n'
    ;;
  configure-execution-admin-browser)
    origin="${2:-}"; bind_ip="${3:-}"
    [[ -z "${4:-}" && -n "$origin" ]] || { printf 'Usage: ./manage.sh configure-execution-admin-browser ORIGIN [PRIVATE_IPV4]\n' >&2; exit 2; }
    validation="$(python3 - "$origin" "$bind_ip" <<'EXECADMINPY'
import ipaddress,sys
from urllib.parse import urlsplit
origin=sys.argv[1].strip().rstrip('/')
bind=sys.argv[2].strip()
try:
    u=urlsplit(origin)
except Exception:
    raise SystemExit('Invalid origin.')
if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password or (u.path not in {'','/'}) or u.query or u.fragment:
    raise SystemExit('Origin must be an exact http(s) origin such as http://192.168.1.20:8787.')
try:
    port=u.port
except ValueError:
    raise SystemExit('Origin port is invalid.')
if port is not None and not (1 <= port <= 65535):
    raise SystemExit('Origin port is invalid.')
if not bind:
    try: bind=str(ipaddress.ip_address(u.hostname))
    except ValueError: raise SystemExit('When ORIGIN uses DNS, pass the server private IPv4 as the second argument.')
try: ip=ipaddress.ip_address(bind)
except ValueError: raise SystemExit('Bind address must be an IPv4 address.')
if ip.version != 4: raise SystemExit('This helper currently requires IPv4.')
if ip.is_unspecified or ip.is_multicast:
    raise SystemExit('Wildcard/multicast binds are refused. Use the exact private administration address.')
if not (ip.is_private or ip.is_loopback or ip.is_link_local):
    raise SystemExit('Refusing a public Execution Admin bind. Use a private/loopback administration address or a trusted reverse proxy.')
print(origin)
print(str(ip))
EXECADMINPY
    )" || { printf '%s\n' "$validation" >&2; exit 2; }
    origin="$(printf '%s\n' "$validation" | sed -n '1p')"
    bind_ip="$(printf '%s\n' "$validation" | sed -n '2p')"
    current="$(env_value "$ENV_FILE" EXECUTION_ADMIN_ALLOWED_ORIGINS)"
    current="${current:-http://127.0.0.1:8787,http://localhost:8787}"
    allowed="$(python3 - "$current" "$origin" <<'EXECORIGINPY'
import sys
seen=[]
for item in (sys.argv[1]+','+sys.argv[2]).split(','):
    item=item.strip().rstrip('/')
    if item and item not in seen: seen.append(item)
print(','.join(seen))
EXECORIGINPY
    )"
    ensure_execution_paths; sync_execution_admin_metadata
    root="$(execution_root)"
    if [[ ! -s "$root/admin-key" ]]; then
      tmp="$(mktemp "$root/admin-key.tmp.XXXXXX")"; random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$root/admin-key"
    fi
    replace_env_value "$ENV_FILE" EXECUTION_ADMIN_ENABLED true
    replace_env_value "$ENV_FILE" EXECUTION_ADMIN_BIND_IP "$bind_ip"
    replace_env_value "$ENV_FILE" EXECUTION_ADMIN_ALLOWED_ORIGINS "$allowed"
    [[ -n "$(env_value "$ENV_FILE" EXECUTION_ADMIN_PORT)" ]] || replace_env_value "$ENV_FILE" EXECUTION_ADMIN_PORT 8752
    sync_execution_profiles
    compose pull execution-admin
    compose up -d --no-deps --force-recreate execution-admin
    printf 'Execution Admin browser access configured.\n'
    printf 'Bind: %s:%s\n' "$bind_ip" "$(env_value "$ENV_FILE" EXECUTION_ADMIN_PORT | sed 's/^$/8752/')"
    printf 'Allowed origin added: %s\n' "$origin"
    printf 'The separate admin key was not printed. Use ./manage.sh show-execution-admin-key in a trusted terminal.\n'
    ;;
  disable-execution-admin)
    [[ -z "${2:-}" ]] || { printf 'Usage: ./manage.sh disable-execution-admin\n' >&2; exit 2; }
    replace_env_value "$ENV_FILE" EXECUTION_ADMIN_ENABLED false
    sync_execution_profiles
    COMPOSE_PROFILES=execution-admin compose rm -sf execution-admin >/dev/null 2>&1 || true
    printf 'Execution admin UI disabled. Broker/approver execution policy is unchanged.\n'
    ;;
  execution-admin-status)
    ensure_execution_paths
    printf 'Enabled: %s\n' "$(env_value "$ENV_FILE" EXECUTION_ADMIN_ENABLED | sed 's/^$/false/')"
    printf 'Bind: %s:%s\n' "$(env_value "$ENV_FILE" EXECUTION_ADMIN_BIND_IP | sed 's/^$/127.0.0.1/')" "$(env_value "$ENV_FILE" EXECUTION_ADMIN_PORT | sed 's/^$/8752/')"
    printf 'Allowed origins: %s\n' "$(env_value "$ENV_FILE" EXECUTION_ADMIN_ALLOWED_ORIGINS | sed 's/^$/http:\/\/127.0.0.1:8787,http:\/\/localhost:8787/')"
    [[ -s "$(execution_root)/admin-key" ]] && printf 'Admin key: configured (hidden)\n' || printf 'Admin key: missing\n'
    printf 'Remote browser helper: ./manage.sh configure-execution-admin-browser ORIGIN PRIVATE_IPV4\n'
    ;;
  rotate-execution-admin-key)
    [[ -z "${2:-}" ]] || { printf 'Do not pass execution admin keys in argv.\n' >&2; exit 2; }
    ensure_execution_paths; root="$(execution_root)"
    tmp="$(mktemp "$root/admin-key.tmp.XXXXXX")"; random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$root/admin-key"
    COMPOSE_PROFILES=execution-admin compose up -d --no-deps --force-recreate execution-admin >/dev/null 2>&1 || true
    printf 'Execution admin key rotated. Existing browser sessions must reconnect.\n'
    ;;
  show-execution-admin-key)
    [[ -z "${2:-}" ]] || { printf 'Usage: ./manage.sh show-execution-admin-key\n' >&2; exit 2; }
    [[ -t 1 ]] || { printf 'Refusing to print the execution admin key to a non-interactive output.\n' >&2; exit 1; }
    ensure_execution_paths
    [[ -s "$(execution_root)/admin-key" ]] || { printf 'Admin key is not configured; run ./manage.sh enable-execution-admin first.\n' >&2; exit 1; }
    printf 'Sensitive: enter this only into Operations Center → Execution & Approvals. Do not save it in browser storage.\n'
    cat "$(execution_root)/admin-key"; printf '\n'
    ;;
  rotate-execution-broker-secret)
    [[ -z "${2:-}" ]] || { printf 'Do not pass broker secrets in argv.\n' >&2; exit 2; }
    ensure_execution_paths
    secret_file="$(execution_root)/control-secret"
    tmp="$(mktemp "$(execution_root)/control-secret.tmp.XXXXXX")"
    random_hex 32 > "$tmp"; chown "$(execution_hermes_uid):10003" "$tmp"; chmod 640 "$tmp"; mv "$tmp" "$secret_file"
    secret_file="$(execution_root)/ssh-profile-integrity-secret"
    tmp="$(mktemp "$(execution_root)/ssh-profile-integrity-secret.tmp.XXXXXX")"
    random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$secret_file"
    rotate_execution_generation
    compose up -d --force-recreate hermes execution-approver execution-docker-broker execution-ssh-broker
    printf 'Execution broker secret rotated; pending operations revoked.\n'
    ;;
  add-ssh-profile)
    name="${2:-}"
    [[ -z "${3:-}" && "$name" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || { printf 'Use a lowercase safe profile name.\n' >&2; exit 2; }
    ensure_execution_paths
    integrity_secret="$(execution_root)/ssh-profile-integrity-secret"
    if [[ ! -s "$integrity_secret" ]]; then
      tmp="$(mktemp "$(execution_root)/ssh-profile-integrity-secret.tmp.XXXXXX")"
      random_hex 32 > "$tmp"; chown 10003:10003 "$tmp"; chmod 600 "$tmp"; mv "$tmp" "$integrity_secret"
    fi
    root="$(execution_root)/ssh"; target="$root/$name"
    [[ ! -e "$target" && ! -L "$target" ]] || { printf 'Profile already exists.\n' >&2; exit 1; }
    read -r -p 'SSH host: ' host
    read -r -p 'SSH port [22]: ' port; port="${port:-22}"
    read -r -p 'SSH user: ' ssh_user
    read -r -p 'Authority [user|root|sudo-nopasswd]: ' authority
    read -r -p 'Authentication [publickey|password]: ' auth
    auth="${auth:-publickey}"
    [[ "$host" =~ ^[A-Za-z0-9._:-]+$ && "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 \
      && "$ssh_user" =~ ^[A-Za-z0-9._-]+$ && "$authority" =~ ^(user|root|sudo-nopasswd)$ \
      && "$auth" =~ ^(publickey|password)$ ]] || { printf 'Invalid profile values.\n' >&2; exit 2; }
    [[ "$ssh_user" != root || "$authority" == root ]] || { printf 'The root SSH user must use authority root.\n' >&2; exit 2; }
    stage="$(mktemp -d "$root/.${name}.tmp.XXXXXX")"; chmod 700 "$stage"
    TEMP_SECRET_FILES+=("$stage")
    if [[ "$auth" == publickey ]]; then
      if read -r -p 'Generate a dedicated Ed25519 key? [Y/n] ' answer && [[ "${answer:-y}" =~ ^[Yy]$ ]]; then
        ssh-keygen -q -t ed25519 -N '' -f "$stage/identity"
      else
        printf 'Paste an unencrypted private key, then Ctrl-D:\n' >&2
        umask 077; cat > "$stage/identity"
        ssh-keygen -y -f "$stage/identity" > "$stage/identity.pub"
      fi
      chmod 600 "$stage/identity" "$stage/identity.pub"
    fi
    ssh-keyscan -p "$port" -T 10 -- "$host" > "$stage/known_hosts.scan" 2>/dev/null || { rm -rf "$stage"; printf 'Host-key scan failed.\n' >&2; exit 1; }
    read -r -p 'Enter the independently verified SHA256 host fingerprint: ' expected
    [[ "$expected" =~ ^SHA256:[A-Za-z0-9+/]{20,}={0,2}$ ]] || { rm -rf "$stage"; printf 'Invalid host fingerprint.\n' >&2; exit 2; }
    status=0
    python3 - "$stage/known_hosts.scan" "$stage/known_hosts" "$expected" <<'PY' || status=$?
import subprocess, sys
source, target, expected = sys.argv[1:]
matches = []
for line in open(source, encoding="utf-8"):
    if not line.strip():
        continue
    completed = subprocess.run(["ssh-keygen", "-lf", "-", "-E", "sha256"], input=line,
        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fields = completed.stdout.split()
    if completed.returncode == 0 and len(fields) > 1 and fields[1] == expected:
        matches.append(line)
if len(matches) != 1:
    raise SystemExit("The independently verified fingerprint did not select exactly one scanned host key.")
open(target, "w", encoding="utf-8").write(matches[0])
PY
    rm -f "$stage/known_hosts.scan"
    [[ "$status" -eq 0 ]] || { rm -rf "$stage"; printf 'Host fingerprint mismatch or ambiguity.\n' >&2; exit 1; }
    if [[ "$auth" == password ]]; then
      [[ -r /dev/tty && -w /dev/tty ]] || { rm -rf "$stage"; printf 'A controlling terminal is required for password input.\n' >&2; exit 1; }
      IFS= read -r -s -p 'SSH password: ' password < /dev/tty; printf '\n' > /dev/tty
      IFS= read -r -s -p 'Confirm SSH password: ' password_confirm < /dev/tty; printf '\n' > /dev/tty
      [[ -n "$password" && "$password" == "$password_confirm" && ${#password} -le 1024 \
        && "$password" != *$'\n'* && "$password" != *$'\r'* ]] || {
        unset password password_confirm; rm -rf "$stage"; printf 'Passwords are empty, mismatched, or too long.\n' >&2; exit 2;
      }
      umask 077; printf '%s' "$password" > "$stage/password"
      unset password password_confirm
      chmod 600 "$stage/password"
    fi
    revision="$(random_hex 32)"
    python3 - "$stage/profile.json" "$host" "$port" "$ssh_user" "$authority" "$expected" "$auth" "$revision" <<'PY'
import json, sys
path, host, port, user, authority, fingerprint, auth, revision = sys.argv[1:]
value = {"version": 2, "auth": auth, "credential_revision": revision, "host": host,
         "port": int(port), "user": user, "authority": authority, "fingerprint": fingerprint}
open(path, "w", encoding="utf-8").write(json.dumps(value, indent=2) + "\n")
PY
    chown -R 10003:10003 "$stage"
    chmod 700 "$stage"; find "$stage" -type f -exec chmod 600 {} +
    printf 'Pinned independently verified host fingerprint: %s\n' "$expected"
    if [[ "$auth" == publickey ]]; then
      printf 'Install this public key on %s@%s, then verify:\n' "$ssh_user" "$host"
      cat "$stage/identity.pub"
    else
      printf 'Password stored only in the protected SSH broker profile.\n'
    fi
    mv "$stage" "$target"
    rotate_execution_generation
    printf 'Profile %s stored. Run ./manage.sh verify-ssh-profile %s.\n' "$name" "$name"
    ;;
  verify-ssh-profile)
    name="${2:-}"; target="$(execution_root)/ssh/$name"
    [[ -z "${3:-}" && "$name" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && -d "$target" && ! -L "$target" ]] || { printf 'Unknown or unsafe profile.\n' >&2; exit 2; }
    compose exec -T execution-ssh-broker python -m broker.ssh --probe "$name"
    printf 'SSH profile %s verified.\n' "$name"
    ;;
  set-ssh-profile-password)
    name="${2:-}"; target="$(execution_root)/ssh/$name"
    [[ -z "${3:-}" && "$name" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && -d "$target" && ! -L "$target" ]] || { printf 'Unknown or unsafe profile.\n' >&2; exit 2; }
    auth="$(python3 - "$target/profile.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("auth", "publickey"))
PY
)"
    [[ "$auth" == password ]] || { printf 'Only password profiles can use this command.\n' >&2; exit 2; }
    [[ -r /dev/tty && -w /dev/tty ]] || { printf 'A controlling terminal is required for password input.\n' >&2; exit 1; }
    IFS= read -r -s -p 'New SSH password: ' password < /dev/tty; printf '\n' > /dev/tty
    IFS= read -r -s -p 'Confirm new SSH password: ' password_confirm < /dev/tty; printf '\n' > /dev/tty
    [[ -n "$password" && "$password" == "$password_confirm" && ${#password} -le 1024 \
      && "$password" != *$'\n'* && "$password" != *$'\r'* ]] || {
      unset password password_confirm; printf 'Passwords are empty, mismatched, or too long.\n' >&2; exit 2;
    }
    root="$(execution_root)/ssh"; stage="$(mktemp -d "$root/.${name}.rotate.XXXXXX")"
    TEMP_SECRET_FILES+=("$stage"); cp -a "$target/." "$stage/"; chmod 700 "$stage"
    umask 077; printf '%s' "$password" > "$stage/password"; unset password password_confirm
    revision="$(random_hex 32)"
    python3 - "$stage/profile.json" "$revision" <<'PY'
import json,sys
path, revision = sys.argv[1:]
value=json.load(open(path, encoding="utf-8")); value["credential_revision"]=revision
open(path, "w", encoding="utf-8").write(json.dumps(value, indent=2)+"\n")
PY
    chown -R 10003:10003 "$stage"; chmod 700 "$stage"; find "$stage" -type f -exec chmod 600 {} +
    backup="$root/.${name}.backup.$(random_hex 8)"
    compose stop execution-ssh-broker >/dev/null 2>&1 || true
    mv "$target" "$backup"; mv "$stage" "$target"; rotate_execution_generation
    if compose up -d --force-recreate execution-ssh-broker && compose exec -T execution-ssh-broker python -m broker.ssh --probe "$name"; then
      rm -rf -- "$backup"; printf 'SSH profile %s password rotated and verified.\n' "$name"
    else
      rm -rf -- "$target"; mv "$backup" "$target"; rotate_execution_generation
      compose up -d --force-recreate execution-ssh-broker || true
      printf 'Password verification failed; the prior profile was restored.\n' >&2; exit 1
    fi
    ;;
  remove-ssh-profile)
    name="${2:-}"; target="$(execution_root)/ssh/$name"
    [[ -z "${3:-}" && "$name" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ && -d "$target" && ! -L "$target" ]] || { printf 'Unknown or unsafe profile.\n' >&2; exit 2; }
    auth="$(python3 - "$target/profile.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("auth", "publickey"))
PY
)"
    rm -rf -- "$target"; rotate_execution_generation
    compose up -d --force-recreate execution-ssh-broker hermes
    if [[ "$auth" == password ]]; then
      printf 'Local profile removed. Change or disable the remote account password separately.\n'
    else
      printf 'Local profile removed. Revoke its public key from remote authorized_keys separately.\n'
    fi
    ;;
  purge-execution)
    [[ -z "${2:-}" ]] || { printf 'Usage: ./manage.sh purge-execution\n' >&2; exit 2; }
    read -r -p 'Type PURGE-EXECUTION to delete keys, state, and workspace: ' confirm
    [[ "$confirm" == PURGE-EXECUTION ]] || { printf 'Unchanged.\n' >&2; exit 1; }
    replace_env_value "$ENV_FILE" EXECUTION_FEATURES ""; sync_execution_profiles
    COMPOSE_PROFILES=execution-approval,execution-docker,execution-ssh compose rm -sf execution-approver execution-docker-broker execution-ssh-broker || true
    rm -rf -- "$(execution_root)" "$ROOT_DIR/data/execution-workspace"
    ensure_execution_paths; restart_hermes
    printf 'Execution state purged. Remote authorized_keys and prior Docker effects are not reverted.\n'
    ;;
  set-n8n-api-key)
    require_profiles n8n
    if [[ -n "${2:-}" ]]; then
      printf 'For safety, do not pass the n8n API key in argv. Run without an argument.\n' >&2
      exit 2
    fi
    read -r -s -p 'n8n owner API key: ' key
    printf '\n' >&2
    [[ -n "$key" && "$key" != *[[:space:]]* ]] || {
      printf 'A non-empty API key without whitespace is required.\n' >&2; exit 2;
    }
    n8n_api_check "$key" || { printf 'n8n rejected the API key; nothing was stored.\n' >&2; exit 1; }
    write_n8n_bootstrap_key "$key"
    printf 'n8n bootstrap API key validated and stored with mode 0600.\n'
    ;;
  set-n8n-instance-mcp-token)
    require_router_backend
    require_profiles hermes n8n
    if [[ -n "${2:-}" ]]; then
      printf 'For safety, do not pass the Instance MCP token in argv. Run without an argument.\n' >&2
      exit 2
    fi
    read -r -s -p 'n8n Instance-level MCP token: ' token
    printf '\n' >&2
    [[ -n "$token" && "$token" != *[[:space:]]* ]] || {
      printf 'A non-empty token without whitespace is required.\n' >&2; exit 2;
    }
    case "$token" in
      Bearer\ *|bearer\ *|http://*|https://*|\"*|\'*)
        printf 'Paste only the n8n Instance MCP token, without Bearer, quotes, JSON, or the connection URL.\n' >&2
        exit 2
        ;;
    esac
    n8n_instance_mcp_check "$token" || {
      printf 'n8n rejected the Instance MCP token or required tools are unavailable; nothing was stored.\n' >&2
      exit 1
    }
    [[ -f "$HERMES_ENV" && ! -L "$HERMES_ENV" ]] || {
      printf 'Hermes secret configuration is missing or unsafe.\n' >&2; exit 1;
    }
    migrate_legacy_trigger_env
    replace_env_value "$HERMES_ENV" N8N_INSTANCE_MCP_TOKEN "$token"
    chmod 600 "$HERMES_ENV"
    mode="$(n8n_mcp_mode)"
    if [[ "$mode" == instance ]]; then
      if ! run_n8n_reconciler; then
        printf 'The validated replacement token remains stored because regenerating an Instance token revokes the old token immediately; n8n reconciliation failed before Hermes was recreated.\n' >&2
        exit 1
      fi
      if ! set_hermes_n8n_mcp_entry instance; then
        printf 'The validated replacement token remains stored because regenerating an Instance token revokes the old token immediately; Hermes configuration reconciliation failed.\n' >&2
        exit 1
      fi
      finish_legacy_trigger_env_migration
      if ! restart_hermes; then
        printf 'The validated replacement token remains stored because regenerating an Instance token revokes the old token immediately; Hermes recreation failed.\n' >&2
        exit 1
      fi
      if ! "$ROOT_DIR/manage.sh" verify-n8n; then
        printf 'The validated replacement token remains stored because regenerating an Instance token revokes the old token immediately; Instance verification failed.\n' >&2
        exit 1
      fi
      printf 'n8n Instance MCP token validated, stored with mode 0600, and connected to Hermes.\n'
    else
      printf 'n8n Instance MCP token validated and stored with mode 0600. Activate it with ./manage.sh set-n8n-mcp-mode instance.\n'
    fi
    ;;
  remove-n8n-instance-mcp-token)
    require_profiles hermes n8n
    [[ -z "${2:-}" ]] || {
      printf 'Usage: ./manage.sh remove-n8n-instance-mcp-token\n' >&2
      exit 2
    }
    [[ "$(n8n_mcp_mode)" != instance ]] || {
      printf 'Instance MCP mode is active. Switch to trigger or off before removing its token.\n' >&2
      exit 1
    }
    [[ -f "$HERMES_ENV" && ! -L "$HERMES_ENV" ]] || {
      printf 'Hermes secret configuration is missing or unsafe.\n' >&2
      exit 1
    }
    if [[ -n "$(env_value "$HERMES_ENV" N8N_INSTANCE_MCP_TOKEN)" ]]; then
      remove_env_values "$HERMES_ENV" N8N_INSTANCE_MCP_TOKEN
      chmod 600 "$HERMES_ENV"
      printf 'Stored n8n Instance MCP token removed. Instance-level MCP in n8n was not disabled.\n'
    else
      printf 'No stored n8n Instance MCP token was present.\n'
    fi
    ;;
  set-n8n-mcp-mode)
    require_router_backend
    require_profiles hermes n8n
    target_mode="${2:-}"
    [[ -z "${3:-}" && ( "$target_mode" == instance || "$target_mode" == trigger || "$target_mode" == off ) ]] || {
      printf 'Usage: ./manage.sh set-n8n-mcp-mode instance|trigger|off\n' >&2
      exit 2
    }
    current_mode="$(n8n_mcp_mode)"
    trigger_token="$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN)"
    if [[ -z "$trigger_token" ]]; then
      trigger_token="$(env_value "$HERMES_ENV" N8N_MCP_TOKEN)"
    fi
    instance_token="$(env_value "$HERMES_ENV" N8N_INSTANCE_MCP_TOKEN)"
    [[ -n "$(n8n_api_key)" ]] || {
      printf 'A stored owner API key is required to reconcile hosted chat and trigger publication. Run ./manage.sh set-n8n-api-key.\n' >&2
      exit 1
    }
    case "$target_mode" in
      instance)
        [[ -n "$instance_token" ]] || {
          printf 'No Instance MCP token is stored. Enable Instance-level MCP in n8n, generate its token, then run ./manage.sh set-n8n-instance-mcp-token.\n' >&2
          exit 1
        }
        n8n_instance_mcp_check "$instance_token" || {
          printf 'The stored Instance MCP token failed validation; mode was not changed.\n' >&2
          exit 1
        }
        ;;
      trigger)
        [[ -n "$trigger_token" ]] || {
          printf 'No Trigger MCP token is stored. Run ./manage.sh configure and select Trigger mode.\n' >&2
          exit 1
        }
        ;;
    esac
    ensure_stack_secrets_dir
    env_backup="$(mktemp "$STACK_SECRETS_DIR/mode-env.backup.XXXXXX")"
    hermes_backup="$(mktemp "$STACK_SECRETS_DIR/mode-hermes-env.backup.XXXXXX")"
    config_backup="$(mktemp "$STACK_SECRETS_DIR/mode-hermes-config.backup.XXXXXX")"
    TEMP_SECRET_FILES+=("$env_backup" "$hermes_backup" "$config_backup")
    cp --preserve=mode,ownership,timestamps "$ENV_FILE" "$env_backup"
    cp --preserve=mode,ownership,timestamps "$HERMES_ENV" "$hermes_backup"
    cp --preserve=mode,ownership,timestamps "$ROOT_DIR/data/hermes/config.yaml" "$config_backup"
    if ! run_n8n_reconciler_with_token "$trigger_token" "$trigger_token" "$target_mode"; then
      printf 'n8n rejected the mode transition before local configuration changed.\n' >&2
      exit 1
    fi
    migrate_legacy_trigger_env
    replace_env_value "$ENV_FILE" N8N_MCP_MODE "$target_mode"
    if set_hermes_n8n_mcp_entry "$target_mode"; then
      finish_legacy_trigger_env_migration
    else
      cp --preserve=mode,ownership,timestamps "$env_backup" "$ENV_FILE"
      cp --preserve=mode,ownership,timestamps "$hermes_backup" "$HERMES_ENV"
      cp --preserve=mode,ownership,timestamps "$config_backup" "$ROOT_DIR/data/hermes/config.yaml"
      if run_n8n_reconciler_with_token "$trigger_token" "$trigger_token" "$current_mode" >/dev/null; then
        printf 'Hermes configuration update failed; prior files and controllable trigger publication state were restored.\n' >&2
      else
        printf 'Hermes configuration update failed; prior local files were restored, but trigger publication rollback could not be verified. Manual recovery is required.\n' >&2
      fi
      exit 1
    fi
    if restart_hermes && "$ROOT_DIR/manage.sh" verify-n8n; then
      printf 'Hermes n8n MCP mode changed to %s.\n' "$target_mode"
    else
      cp --preserve=mode,ownership,timestamps "$env_backup" "$ENV_FILE"
      cp --preserve=mode,ownership,timestamps "$hermes_backup" "$HERMES_ENV"
      cp --preserve=mode,ownership,timestamps "$config_backup" "$ROOT_DIR/data/hermes/config.yaml"
      if run_n8n_reconciler_with_token "$trigger_token" "$trigger_token" "$current_mode" && restart_hermes && "$ROOT_DIR/manage.sh" verify-n8n; then
        printf 'Mode verification failed; the prior local configuration and controllable trigger publication state were restored and verified.\n' >&2
      else
        printf 'Mode verification failed and rollback could not be fully verified; manual recovery is required.\n' >&2
      fi
      exit 1
    fi
    ;;
  bootstrap-n8n|reconcile-n8n)
    require_router_backend
    require_profiles hermes n8n
    run_n8n_reconciler
    restart_hermes
    "$ROOT_DIR/manage.sh" verify-n8n
    ;;
  verify-n8n)
    require_profiles hermes n8n
    run_n8n_verifier
    ;;
  rotate-n8n-trigger-token|rotate-n8n-token)
    require_router_backend
    require_profiles hermes n8n
    [[ -f "$HERMES_ENV" ]] || { printf 'Hermes is not configured.\n' >&2; exit 1; }
    migrate_legacy_trigger_env
    old_token="$(env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN)"
    [[ -n "$old_token" ]] || { printf 'No Trigger MCP token is configured. Run bootstrap-n8n first.\n' >&2; exit 1; }
    current_mode="$(n8n_mcp_mode)"
    if [[ "$current_mode" == instance ]]; then
      printf 'This rotates only the retained Trigger credential. To replace the Instance token, regenerate it in n8n and run ./manage.sh set-n8n-instance-mcp-token.\n' >&2
    fi
    new_token="$(random_hex 32)"
    if run_n8n_reconciler_with_token "$new_token" "$old_token" "$current_mode"; then
      replace_env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN "$new_token"
      finish_legacy_trigger_env_migration
      if restart_hermes && "$ROOT_DIR/manage.sh" verify-n8n; then
        printf 'n8n Trigger MCP bearer token rotated without printing it.\n'
      else
        if run_n8n_reconciler_with_token "$old_token" "$new_token" "$current_mode"; then
          replace_env_value "$HERMES_ENV" N8N_TRIGGER_MCP_TOKEN "$old_token"
          if restart_hermes && "$ROOT_DIR/manage.sh" verify-n8n; then
            printf 'Rotation verification failed; the prior n8n Trigger credential and Hermes token were restored and verified.\n' >&2
          else
            printf 'Rotation verification failed; the prior values were restored, but their operation could not be verified. Manual recovery is required.\n' >&2
          fi
        else
          printf 'Rotation verification failed, and n8n Trigger credential rollback also failed. Hermes retains the new token; manual recovery is required.\n' >&2
        fi
        exit 1
      fi
    else
      printf 'Rotation failed before Hermes changed; the prior Trigger token remains active.\n' >&2
      exit 1
    fi
    ;;
  remove-n8n-bootstrap-key)
    if [[ -f "$N8N_BOOTSTRAP_ENV" ]]; then
      rm -f "$N8N_BOOTSTRAP_ENV"
      printf 'Stored n8n API key removed. Managed IDs and fingerprints were retained.\n'
    else
      printf 'No stored n8n API key was present.\n'
    fi
    ;;
  set-backend-api-key)
    new_key="${2:-}"
    [[ "$new_key" =~ ^[A-Za-z0-9._:-]+$ ]] || {
      printf 'Provide a non-empty API key containing letters, digits, dot, underscore, colon, or hyphen.
' >&2
      exit 2
    }
    profiles="$(sed -n 's/^COMPOSE_PROFILES=//p' "$ENV_FILE")"
    if [[ ",$profiles," == *,smart-router,* ]]; then
      replace_env_value "$ENV_FILE" SMART_ROUTER_UPSTREAM_API_KEY "$new_key"
      compose up -d --no-deps --force-recreate smart-router
      printf 'Router backend upstream API key updated for Smart Router.
'
    else
      updated=false
      if [[ ",$profiles," == *,hermes,* && -f "$HERMES_ENV" ]]; then
        replace_env_value "$HERMES_ENV" NINEROUTER_API_KEY "$new_key"
        [[ -n "$(env_value "$HERMES_ENV" NINEROUTER_KEY)" ]] \
          && replace_env_value "$HERMES_ENV" NINEROUTER_KEY "$new_key"
        updated=true
      fi
      if [[ ",$profiles," == *,open-webui,* ]]; then
        replace_env_value "$ENV_FILE" OPENWEBUI_OPENAI_API_KEY "$new_key"
        compose up -d --no-deps --force-recreate open-webui
        updated=true
      fi
      if [[ "$updated" != true ]]; then
        printf 'Neither Smart Router nor a direct backend consumer (Hermes/Open WebUI) is selected.
' >&2
        exit 1
      fi
      if [[ ",$profiles," == *,hermes,* && -f "$HERMES_ENV" ]]; then
        restart_hermes
      fi
      printf 'Direct backend API key updated for local consumers.
'
    fi
    ;;
  health) shift; ops health "$@" ;;
  version) ops version ;;
  backup|backup-sections|backup-list|restore|rollback|lock-images|verify-images) cmd="$1"; shift; ops "$cmd" "$@" ;;
  router-status)
    profiles="$(env_value "$ENV_FILE" COMPOSE_PROFILES)"
    if [[ ",$profiles," != *,smart-router,* ]]; then
      printf 'Smart Router is not enabled in COMPOSE_PROFILES. Run ./manage.sh configure to enable it.\n'
      exit 1
    fi
    base="$(router_local_base_url)"
    printf 'Smart Router v0.5.9 stack integration\n'
    printf '  image: %s:%s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_IMAGE_REPOSITORY)" "$(env_value "$ENV_FILE" SMART_ROUTER_IMAGE_TAG)"
    printf '  base URL: %s\n' "$base"
    printf '  OpenAI API: %s/v1\n' "$base"
    printf '  mode: %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_MODE)"
    printf '  policy: %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_POLICY)"
    printf '  tier override aliases: %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_ALLOW_TIER_OVERRIDES)"
    printf '  dashboard: %s (%s/dashboard)\n' "$(env_value "$ENV_FILE" SMART_ROUTER_DASHBOARD_ENABLED)" "$base"
    printf '  operations center: %s (%s/control/)\n' "$(env_value "$ENV_FILE" SMART_ROUTER_CONTROL_PLANE_ENABLED)" "$base"
    printf '  require auth: %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_REQUIRE_AUTH)"
    printf '  provider health/circuit breakers: %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_PROVIDER_HEALTH_ENABLED)"
    printf '  OIDC: %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_OIDC_ENABLED)"
    printf '  HA mode: %s; Redis required: %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_HA_MODE)" "$(env_value "$ENV_FILE" SMART_ROUTER_REDIS_REQUIRED)"
    printf '  route profiles: fast=%s standard=%s strong=%s coding=%s vision=%s\n' \
      "$(env_value "$ENV_FILE" SMART_ROUTER_FAST_MODEL)" "$(env_value "$ENV_FILE" SMART_ROUTER_STANDARD_MODEL)" \
      "$(env_value "$ENV_FILE" SMART_ROUTER_STRONG_MODEL)" "$(env_value "$ENV_FILE" SMART_ROUTER_CODING_MODEL)" \
      "$(env_value "$ENV_FILE" SMART_ROUTER_VISION_MODEL)"
    printf '\nRuntime /router/info:\n'
    router_api_get /router/info | pretty_json || printf 'Runtime info unavailable; is smart-router running?\n' >&2
    ;;
  router-access)
    base="$(router_local_base_url)"
    printf 'Dashboard:     %s/dashboard\n' "$base"
    printf 'Operations Center: %s/control/\n' "$base"
    printf 'OpenAI API:    %s/v1\n' "$base"
    printf 'Admin user:    %s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_BOOTSTRAP_ADMIN_USER)"
    printf 'Dashboard telemetry uses SMART_ROUTER_CLIENT_API_KEY. Operations Center accepts the bootstrap admin user/password or SMART_ROUTER_ADMIN_API_KEY.\n'
    if [[ "${2:-}" == --show-secrets ]]; then
      [[ -r /dev/tty && -w /dev/tty ]] || { printf 'A controlling terminal is required to reveal secrets.\n' >&2; exit 1; }
      read -r -p 'Reveal Smart Router access secrets on this terminal? [y/N]: ' answer </dev/tty
      [[ "$answer" =~ ^[Yy]$ ]] || { printf 'Secrets not shown.\n'; exit 0; }
      printf 'SMART_ROUTER_CLIENT_API_KEY=%s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_CLIENT_API_KEY)"
      printf 'SMART_ROUTER_ADMIN_API_KEY=%s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_ADMIN_API_KEY)"
      printf 'SMART_ROUTER_BOOTSTRAP_ADMIN_PASSWORD=%s\n' "$(env_value "$ENV_FILE" SMART_ROUTER_BOOTSTRAP_ADMIN_PASSWORD)"
    elif [[ -n "${2:-}" ]]; then
      printf 'Usage: ./manage.sh router-access [--show-secrets]\n' >&2; exit 2
    else
      printf 'Use ./manage.sh router-access --show-secrets only when you need to reveal local credentials.\n'
    fi
    ;;
  router-summary)
    hours="${2:-24}"
    [[ "$hours" =~ ^[0-9]+([.][0-9]+)?$ ]] || { printf 'Hours must be numeric.\n' >&2; exit 2; }
    key="$(env_value "$ENV_FILE" SMART_ROUTER_CLIENT_API_KEY)"
    router_api_get "/dashboard/api/summary?hours=$hours" "$key" | pretty_json
    ;;
  router-routes)
    key="$(env_value "$ENV_FILE" SMART_ROUTER_ADMIN_API_KEY)"
    router_api_get /control/api/routes "$key" | pretty_json
    ;;
  router-provider-health)
    key="$(env_value "$ENV_FILE" SMART_ROUTER_ADMIN_API_KEY)"
    router_api_get /control/api/provider-health "$key" | pretty_json
    ;;
  router-system)
    key="$(env_value "$ENV_FILE" SMART_ROUTER_ADMIN_API_KEY)"
    router_api_get /control/api/system "$key" | pretty_json
    ;;
  router-policy)
    policy="${2:-}"; [[ "$policy" == heuristic || "$policy" == calibrated || "$policy" == learned ]] || { printf 'heuristic|calibrated|learned required\n' >&2; exit 2; }
    [[ "$policy" != calibrated || -s "$ROOT_DIR/smart-router/policy/calibrated.json" ]] || { printf 'calibrated policy missing\n' >&2; exit 1; }
    replace_env_value "$ENV_FILE" SMART_ROUTER_POLICY "$policy"
    compose up -d --no-deps --force-recreate smart-router
    if ! router_runtime_set "{\"router_policy\":\"$policy\"}" >/dev/null; then
      printf 'WARNING: policy was written to .env, but the Operations Center runtime override could not be synchronized.\n' >&2
      printf 'If a previous UI override exists, open System and choose Reset to environment, then retry.\n' >&2
    fi
    printf 'router policy: %s\n' "$policy"
    ;;
  router-info)
    compose exec -T smart-router python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/router/info',timeout=5).read().decode())"
    ;;
  router-calibrate)
    dataset="${2:-}"; [[ -f "$dataset" ]] || { printf 'Labeled JSONL file required\n' >&2; exit 2; }
    dataset="$(cd "$(dirname "$dataset")" && pwd)/$(basename "$dataset")"
    compose run --rm --no-deps -v "$dataset:/work/input.jsonl:ro" -v "$ROOT_DIR/smart-router/policy:/out" smart-router python -m smart_router.eval.calibrate /work/input.jsonl -o /out/calibrated.json
    ;;
  router-report)
    dataset="${2:-}"; [[ -f "$dataset" ]] || { printf 'Labeled JSONL file required\n' >&2; exit 2; }
    dataset="$(cd "$(dirname "$dataset")" && pwd)/$(basename "$dataset")"
    compose run --rm --no-deps -v "$dataset:/work/input.jsonl:ro" smart-router python -m smart_router.eval.report /work/input.jsonl
    ;;
  router-replay)
    dataset="${2:-}"; [[ -f "$dataset" ]] || { printf 'Requests JSONL file required\n' >&2; exit 2; }
    output="${3:-$ROOT_DIR/data/smart-router/replay-v0.5.9.jsonl}"
    dataset="$(cd "$(dirname "$dataset")" && pwd)/$(basename "$dataset")"; mkdir -p "$(dirname "$output")"; touch "$output"; output="$(cd "$(dirname "$output")" && pwd)/$(basename "$output")"
    compose run --rm --no-deps -v "$dataset:/work/input.jsonl:ro" -v "$output:/work/output.jsonl" smart-router python -m smart_router.eval.replay /work/input.jsonl -o /work/output.jsonl
    ;;
  *) printf 'Unknown command: %s\n' "$command" >&2; usage >&2; exit 2 ;;
esac
