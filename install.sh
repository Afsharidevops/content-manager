#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_URL="${CONTENT_MANAGER_REPOSITORY_URL:-https://github.com/Afsharidevops/content-manager.git}"
REPOSITORY_BRANCH="${CONTENT_MANAGER_BRANCH:-main}"
SOURCE_PATH="${BASH_SOURCE[0]:-}"
SOURCE_DIR="$(cd -- "$(dirname -- "${SOURCE_PATH:-.}")" 2>/dev/null && pwd || pwd)"

# When install.sh is streamed with curl, fetch the complete repository first.
# The real wizard is then executed from the clone with prompts attached to the
# user's terminal instead of the exhausted curl pipe.
if [[ ! -f "$SOURCE_DIR/docker-compose.yml" ]]; then
  command -v git >/dev/null 2>&1 || {
    printf 'git is required. Install git, then run this command again.\n' >&2
    exit 1
  }
  [[ -r /dev/tty ]] || {
    printf 'Interactive installation requires a terminal. Clone the repository and run ./install.sh.\n' >&2
    exit 1
  }

  INSTALL_TARGET="${CONTENT_MANAGER_DIR:-$HOME/content-manager}"
  if [[ -d "$INSTALL_TARGET/.git" ]]; then
    printf '[INFO] Updating existing installation in %s\n' "$INSTALL_TARGET"
    git -C "$INSTALL_TARGET" fetch origin "$REPOSITORY_BRANCH"
    git -C "$INSTALL_TARGET" switch "$REPOSITORY_BRANCH"
    git -C "$INSTALL_TARGET" pull --ff-only origin "$REPOSITORY_BRANCH"
  elif [[ -e "$INSTALL_TARGET" ]]; then
    printf 'Target exists but is not a Git repository: %s\n' "$INSTALL_TARGET" >&2
    printf 'Set CONTENT_MANAGER_DIR to another path or move the existing directory.\n' >&2
    exit 1
  else
    printf '[INFO] Cloning into %s\n' "$INSTALL_TARGET"
    git clone --depth 1 --branch "$REPOSITORY_BRANCH" "$REPOSITORY_URL" "$INSTALL_TARGET"
  fi

  chmod +x "$INSTALL_TARGET/install.sh" "$INSTALL_TARGET/manage.sh"
  exec "$INSTALL_TARGET/install.sh" "$@" </dev/tty >/dev/tty
fi

ROOT_DIR="$SOURCE_DIR"
ENV_FILE="$ROOT_DIR/.env"
HERMES_DIR="$ROOT_DIR/data/hermes"
NINEROUTER_DIR="$ROOT_DIR/data/9router"
OPENWEBUI_DIR="$ROOT_DIR/data/open-webui"
SMART_ROUTER_DIR="$ROOT_DIR/data/smart-router"
N8N_DIR="$ROOT_DIR/data/n8n"
CADDY_DIR="$ROOT_DIR/data/caddy"
HERMES_DASHBOARD_ACCESS_FILE="$ROOT_DIR/data/stack-secrets/hermes-dashboard-access.env"
DRY_RUN=false
NO_START=false

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --no-start) NO_START=true ;;
    -h|--help)
      printf '%s\n' "Usage: ./install.sh [--dry-run] [--no-start]"
      exit 0
      ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

prompt() {
  local label="$1" default="${2-}" value
  if [[ -n "$default" ]]; then
    read -r -p "$label [$default]: " value
    printf '%s' "${value:-$default}"
  else
    read -r -p "$label: " value
    printf '%s' "$value"
  fi
}

prompt_secret() {
  local label="$1" allow_empty="${2:-false}" value
  while true; do
    read -r -s -p "$label: " value
    printf '\n' >&2
    if [[ -n "$value" || "$allow_empty" == true ]]; then
      printf '%s' "$value"
      return
    fi
    warn "A value is required."
  done
}

confirm() {
  local label="$1" default="${2:-n}" answer suffix
  if [[ "$default" == y ]]; then suffix="Y/n"; else suffix="y/N"; fi
  read -r -p "$label [$suffix]: " answer
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy]$ ]]
}

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "${1:-32}"
  else
    od -An -N "${1:-32}" -tx1 /dev/urandom | tr -d ' \n'
  fi
}

hash_hermes_dashboard_password() {
  python3 -c '
import base64, hashlib, secrets, sys
password = sys.stdin.read().encode()
salt = secrets.token_bytes(16)
digest = hashlib.scrypt(password, salt=salt, n=2**14, r=8, p=1, dklen=32, maxmem=0)
print("scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode())
'
}

dotenv_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\$\$}"
  printf '"%s"' "$value"
}

# Compose treats single-quoted .env values literally. The dashboard scrypt hash
# contains dollar separators, so this avoids interpolation without multiplying
# escapes every time the installer preserves an existing hash.
dotenv_literal_quote() {
  local value="$1"
  [[ "$value" != *"'"* ]] || die "A literal .env value contains an unsupported single quote."
  printf "'%s'" "$value"
}

yaml_quote() {
  local value="$1"
  value="${value//\'/\'\'}"
  printf "'%s'" "$value"
}

# Emits the installer-owned mcp_servers block, or nothing when the selected
# bridge is off or still waiting for an Instance-level token. Hermes resolves
# the mode-specific values from data/hermes/.env at connect time, so neither
# bearer token lands in the world-readable config.yaml.
render_mcp_block() {
  [[ "${install_n8n_mcp:-false}" == true ]] || return 0
  printf 'mcp_servers:\n'
  render_mcp_entry
}

render_mcp_entry() {
  [[ "${install_n8n_mcp:-false}" == true ]] || return 0
  local url_var token_var
  case "${n8n_mcp_mode:-off}" in
    instance)
      url_var='N8N_INSTANCE_MCP_URL'
      token_var='N8N_INSTANCE_MCP_TOKEN'
      ;;
    trigger)
      url_var='N8N_TRIGGER_MCP_URL'
      token_var='N8N_TRIGGER_MCP_TOKEN'
      ;;
    *) return 0 ;;
  esac
  printf '%s\n' \
    '  # >>> hermes-stack n8n mcp (managed) >>>' \
    '  n8n:' \
    "    url: \"\${$url_var}\"" \
    '    headers:' \
    "      Authorization: \"Bearer \${$token_var}\"" \
    '  # <<< hermes-stack n8n mcp (managed) <<<'
}

ensure_hermes_policy_config() {
  local file="$1" tmp next
  [[ -f "$file" ]] || return 0
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  cp "$file" "$tmp"

  command -v python3 >/dev/null 2>&1 || die "python3 is required to reconcile Hermes policy settings."
  next="$(mktemp "$file.tmp.XXXXXX")"
  python3 - "$tmp" > "$next" <<'PY'
import re
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines()


def section_bounds(name):
    starts = [i for i, line in enumerate(lines) if re.fullmatch(rf"{re.escape(name)}:\s*", line)]
    if len(starts) > 1:
        raise SystemExit(f"Duplicate top-level {name}: sections are unsafe; restore the backup and merge them first.")
    if not starts:
        return None
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[^\s#]", lines[i])), len(lines))
    return start, end


def enforce_fields(name, required):
    bounds = section_bounds(name)
    if bounds is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"{name}:")
        lines.extend(f"  {key}: {value}" for key, value in required.items())
        return
    start, end = bounds
    found = set()
    for i in range(start + 1, end):
        match = re.match(r"^  ([A-Za-z0-9_-]+):", lines[i])
        if match and match.group(1) in required:
            key = match.group(1)
            lines[i] = f"  {key}: {required[key]}"
            found.add(key)
    insert_at = end
    for key, value in required.items():
        if key not in found:
            lines.insert(insert_at, f"  {key}: {value}")
            insert_at += 1


# Upstream terminal/code_execution enablement is a local operator decision made
# with ./manage.sh set-upstream-terminal, so the installer neither forces them
# off nor turns them on: it leaves whatever disabled_toolsets already says.
# Still validate that the top-level agent map is unambiguous before any rewrite.
section_bounds("agent")


def enable_plugin(name):
    bounds = section_bounds("plugins")
    if bounds is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(["plugins:", "  enabled:", f"    - {name}"])
        return
    start, end = bounds
    if any(re.fullmatch(rf"\s*-\s*{re.escape(name)}\s*", line) for line in lines[start + 1:end]):
        return
    enabled = next((i for i in range(start + 1, end) if re.fullmatch(r"  enabled:\s*", lines[i])), None)
    if enabled is None:
        lines[end:end] = ["  enabled:", f"    - {name}"]
    else:
        lines.insert(enabled + 1, f"    - {name}")


enable_plugin("stack-package-policy")
enable_plugin("stack-execution-policy")
enforce_fields("approvals", {"mode": "manual", "timeout": "300", "cron_mode": "deny"})
enforce_fields("skills", {"write_approval": "true"})
print("\n".join(lines) + "\n", end="")
PY
  mv "$next" "$tmp"
  chmod 640 "$tmp"
  chown "$hermes_uid:$hermes_gid" "$tmp"
  mv "$tmp" "$file"
}

enable_hermes_telegram_policy_toolsets() {
  [[ "${install_hermes:-false}" == true && -n "${telegram_token:-}" ]] || return 0

  info "Enabling stack policy toolsets for Telegram..."
  local attempt
  for attempt in {1..30}; do
    if "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" exec -T hermes sh -lc '
      cd /opt/hermes
      /opt/hermes/.venv/bin/hermes tools enable stack-execution-policy --platform telegram >/dev/null \
        && /opt/hermes/.venv/bin/hermes tools enable stack-package-policy --platform telegram >/dev/null \
        && tools="$(/opt/hermes/.venv/bin/hermes tools list --platform telegram 2>/dev/null)" \
        && printf "%s\\n" "$tools" | grep -Eq "enabled[[:space:]]+stack-execution-policy([[:space:]]|$)" \
        && printf "%s\\n" "$tools" | grep -Eq "enabled[[:space:]]+stack-package-policy([[:space:]]|$)"
    ' >/dev/null 2>&1; then
      "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" restart hermes >/dev/null
      ok "Hermes Telegram stack policy toolsets enabled."
      return 0
    fi
    sleep 2
  done

  die "Could not enable stack-execution-policy and stack-package-policy for Telegram."
}

valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( 1 <= 10#$1 && 10#$1 <= 65535 ))
}

prompt_port() {
  local label="$1" default="$2" value
  while true; do
    value="$(prompt "$label" "$default")"
    if valid_port "$value"; then
      printf '%s' "$value"
      return 0
    fi
    warn "Enter a numeric port from 1 to 65535." >&2
  done
}

prompt_optional_integer() {
  local label="$1" value
  while true; do
    value="$(prompt "$label")"
    if [[ -z "$value" || "$value" =~ ^-?[0-9]+$ ]]; then
      printf '%s' "$value"
      return 0
    fi
    warn "Enter a numeric ID, or press Enter to skip." >&2
  done
}

valid_bind_ip() {
  local ip="$1" octet
  local -a octets
  [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  IFS=. read -r -a octets <<< "$ip"
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]+$ ]] && ((10#$octet <= 255)) || return 1
  done
}

private_ipv4() {
  local first second
  IFS=. read -r first second _ <<< "$1"
  (( 10#$first == 10 \
    || (10#$first == 172 && 16 <= 10#$second && 10#$second <= 31) \
    || (10#$first == 192 && 10#$second == 168) ))
}

detect_lan_ipv4() {
  local candidate
  if [[ -n "${CONTENT_MANAGER_LAN_IP:-}" ]]; then
    valid_bind_ip "$CONTENT_MANAGER_LAN_IP" \
      || die "CONTENT_MANAGER_LAN_IP is not a valid IPv4 address: $CONTENT_MANAGER_LAN_IP"
    printf '%s' "$CONTENT_MANAGER_LAN_IP"
    return 0
  fi

  if command -v ip >/dev/null 2>&1; then
    candidate="$(ip -4 route get 1.1.1.1 2>/dev/null \
      | awk '{ for (i=1; i<=NF; i++) if ($i == "src") { print $(i+1); exit } }')"
    if valid_bind_ip "$candidate" && private_ipv4 "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi

    while read -r candidate; do
      if valid_bind_ip "$candidate" && private_ipv4 "$candidate"; then
        printf '%s' "$candidate"
        return 0
      fi
    done < <(ip -o -4 addr show scope global 2>/dev/null | awk '{ sub(/\/.*/, "", $4); print $4 }')
  fi

  if command -v hostname >/dev/null 2>&1; then
    for candidate in $(hostname -I 2>/dev/null); do
      if valid_bind_ip "$candidate" && private_ipv4 "$candidate"; then
        printf '%s' "$candidate"
        return 0
      fi
    done
  fi
  return 1
}

suggested_bind_ip() {
  local current="$1"
  if [[ -n "$lan_ip" && "$current" == 127.0.0.1 ]]; then
    printf '%s' "$lan_ip"
  else
    printf '%s' "$current"
  fi
}

service_url_host() {
  local bind_ip="$1"
  if [[ "$bind_ip" == 127.0.0.1 ]]; then
    printf 'localhost'
  elif [[ "$bind_ip" == 0.0.0.0 ]]; then
    printf '%s' "${lan_ip:-localhost}"
  else
    printf '%s' "$bind_ip"
  fi
}

prompt_bind_ip() {
  local label="$1" default="$2" value
  while true; do
    value="$(prompt "$label" "$default")"
    if valid_bind_ip "$value"; then
      printf '%s' "$value"
      return 0
    fi
    warn "Enter an IPv4 address such as 127.0.0.1, 192.168.1.10, or 0.0.0.0." >&2
  done
}

valid_ids() {
  [[ "$1" =~ ^[0-9]+(,[0-9]+)*$ ]]
}

valid_domain() {
  [[ ${#1} -le 253 && "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]]
}

prompt_domain() {
  local label="$1" value
  while true; do
    value="$(prompt "$label")"
    value="${value,,}"
    if valid_domain "$value"; then
      printf '%s' "$value"
      return 0
    fi
    warn "Enter a domain only, such as chat.example.com, without https://, a port, or a path." >&2
  done
}

install_docker() {
  command -v curl >/dev/null 2>&1 || die "curl is required to install Docker."
  local installer
  installer="$(mktemp)"
  curl -fsSL https://get.docker.com -o "$installer"
  if [[ "$(id -u)" -eq 0 ]]; then
    sh "$installer"
  elif command -v sudo >/dev/null 2>&1; then
    sudo sh "$installer"
  else
    rm -f "$installer"
    die "Docker installation requires root or sudo."
  fi
  rm -f "$installer"
}

detect_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    confirm "Docker is not installed. Install it using Docker's official installer?" y \
      || die "Install Docker Engine and the Compose plugin, then rerun this script."
    install_docker
  fi
  docker compose version >/dev/null 2>&1 \
    || die "The Docker Compose plugin is required (the command must be 'docker compose')."
  if docker info >/dev/null 2>&1; then
    DOCKER=(docker)
  elif command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
  else
    die "Docker exists but this user cannot access the daemon. Add the user to the docker group or run with sudo."
  fi
}

backup_existing() {
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  [[ -f "$ENV_FILE" ]] && cp -p "$ENV_FILE" "$ENV_FILE.backup-$stamp"
  [[ -f "$HERMES_DIR/.env" ]] && cp -p "$HERMES_DIR/.env" "$HERMES_DIR/.env.backup-$stamp"
  [[ -f "$HERMES_DIR/config.yaml" ]] && cp -p "$HERMES_DIR/config.yaml" "$HERMES_DIR/config.yaml.backup-$stamp"
  [[ -f "$CADDY_DIR/Caddyfile" ]] && cp -p "$CADDY_DIR/Caddyfile" "$CADDY_DIR/Caddyfile.backup-$stamp"
  return 0
}

read_unique_env_value() {
  local file="$1" key="$2" value="" count
  [[ -f "$file" ]] || { printf '%s' "$value"; return 0; }
  count="$(grep -c "^${key}=" "$file" || true)"
  (( count <= 1 )) || die "Duplicate ${key} entries in ${file#$ROOT_DIR/} are unsafe; restore the backup and keep one value."
  value="$(sed -n "s/^${key}=//p" "$file")"
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "$value"
}

existing_env_value() {
  read_unique_env_value "$ENV_FILE" "$1"
}

existing_hermes_env_value() {
  read_unique_env_value "$HERMES_DIR/.env" "$1"
}

replace_env_value() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp "$file.tmp.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { changed=0 }
    index($0, key "=") == 1 { print key "=" value; changed=1; next }
    { print }
    END { if (!changed) print key "=" value }
  ' "$file" > "$tmp"
  chmod --reference="$file" "$tmp"
  mv "$tmp" "$file"
}

profile_enabled() {
  local profile="$1" configured
  configured="$(existing_env_value COMPOSE_PROFILES)"
  [[ ",$configured," == *",$profile,"* ]]
}

printf '\nContent Manager - Easy Installer\n'
printf '%s\n' '================================'
printf '%s\n' 'Hermes Linux Stack v0.5.9 platform extended with the Content Manager daily content pipeline layer.'
lan_ip="$(detect_lan_ipv4 || true)"
if [[ -n "$lan_ip" ]]; then
  info "Detected LAN IPv4 address: $lan_ip (press Enter to use it when suggested)."
else
  warn "No private LAN IPv4 address was detected; localhost will be suggested."
fi
configure_nine=false
configure_hermes=false
configure_webui=false
configure_smart_router=false
configure_n8n=false
configure_caddy=false
existing_install=false
smart_router_was_enabled=false
n8n_was_enabled=false
change_bind_ips=false
n8n_ready=false

if [[ -f "$ENV_FILE" ]]; then
  existing_install=true
  install_nine=false; profile_enabled 9router && install_nine=true
  install_hermes=false; profile_enabled hermes && install_hermes=true
  install_webui=false; profile_enabled open-webui && install_webui=true
  install_smart_router=false; profile_enabled smart-router && install_smart_router=true
  smart_router_was_enabled="$install_smart_router"
  install_n8n=false; profile_enabled n8n && install_n8n=true
  n8n_was_enabled="$install_n8n"
  install_caddy=false; profile_enabled caddy && install_caddy=true

  printf 'Existing components: %s\n' "$(existing_env_value COMPOSE_PROFILES)"
  printf '%s\n' 'The wizard keeps existing components, secrets, and data by default.'

  if [[ "$install_nine" == true ]]; then
    confirm "Reconfigure existing 9router settings?" n && configure_nine=true
  elif confirm "Add 9router?" n; then
    install_nine=true; configure_nine=true
  fi
  if [[ "$install_hermes" == true ]]; then
    confirm "Reconfigure existing Hermes Agent settings?" n && configure_hermes=true
  elif confirm "Add Hermes Agent?" n; then
    install_hermes=true; configure_hermes=true
  fi
  if [[ "$install_webui" == true ]]; then
    confirm "Reconfigure existing Open WebUI settings?" n && configure_webui=true
  elif confirm "Add Open WebUI?" n; then
    install_webui=true; configure_webui=true
  fi
  if [[ "$install_smart_router" == true ]]; then
    if ! confirm "Keep the Hermes Smart Router enabled?" y; then
      install_smart_router=false
      configure_smart_router=true
    elif confirm "Reconfigure existing Smart Router settings?" n; then
      configure_smart_router=true
    fi
  elif [[ "$install_nine" == true && "$install_hermes" == true ]] \
    && confirm "Enable Hermes Smart Router v0.5.9 (recommended)?" y; then
    install_smart_router=true
    configure_smart_router=true
  fi
  if [[ "$install_n8n" == true ]]; then
    if ! confirm "Keep n8n workflow automation enabled?" y; then
      install_n8n=false
      configure_n8n=true
    elif confirm "Reconfigure existing n8n settings?" n; then
      configure_n8n=true
    fi
  elif confirm "Add optional n8n workflow automation?" n; then
    install_n8n=true
    configure_n8n=true
  fi
  confirm "Change published container bind IPs only?" n && change_bind_ips=true
else
  printf '%s\n' '1) Install both 9router and Hermes Agent (recommended)'
  printf '%s\n' '2) Install 9router only'
  printf '%s\n' '3) Install Hermes Agent only'
  printf '%s\n' '4) Install Open WebUI only'
  while true; do
    selection="$(prompt "Choose installation" "1")"
    case "$selection" in
      1) install_nine=true; install_hermes=true; install_webui=false; break ;;
      2) install_nine=true; install_hermes=false; install_webui=false; break ;;
      3) install_nine=false; install_hermes=true; install_webui=false; break ;;
      4) install_nine=false; install_hermes=false; install_webui=true; break ;;
      *) warn "Choose 1, 2, 3, or 4." >&2 ;;
    esac
  done

  if [[ "$selection" != 4 ]] && confirm "Also install Open WebUI?" n; then
    install_webui=true
  fi
  configure_nine="$install_nine"
  configure_hermes="$install_hermes"
  configure_webui="$install_webui"
  install_smart_router=false
  if [[ "$install_nine" == true && "$install_hermes" == true ]] \
    && confirm "Enable Hermes Smart Router v0.5.9 (recommended)?" y; then
    install_smart_router=true
    configure_smart_router=true
  fi
  install_n8n=false
  if confirm "Add optional n8n workflow automation?" n; then
    install_n8n=true
    configure_n8n=true
  fi
  install_caddy=false
fi

profiles=""
[[ "$install_nine" == true ]] && profiles="9router"
[[ "$install_smart_router" == true ]] && profiles="${profiles:+$profiles,}smart-router"
[[ "$install_hermes" == true ]] && profiles="${profiles:+$profiles,}hermes"
[[ "$install_webui" == true ]] && profiles="${profiles:+$profiles,}open-webui"
[[ "$install_n8n" == true ]] && profiles="${profiles:+$profiles,}n8n"

mkdir -p "$HERMES_DIR" "$NINEROUTER_DIR" "$OPENWEBUI_DIR" "$SMART_ROUTER_DIR" "$N8N_DIR" "$CADDY_DIR"
mkdir -p "$HERMES_DIR/lazy-packages" "$HERMES_DIR/npm-packages" "$ROOT_DIR/data/stack-secrets"
chmod 700 "$ROOT_DIR/data/stack-secrets"
# Empty execution policy files keep the normal Compose profile renderable while
# execution remains off. manage.sh fills them only after a local opt-in.
execution_owner_uid="${SUDO_UID:-$(id -u)}"
execution_owner_gid="${SUDO_GID:-$(id -g)}"
execution_runtime_uid="$execution_owner_uid"
execution_runtime_gid="$execution_owner_gid"
execution_gateway_uid="$execution_owner_uid"
execution_workspace_uid="$execution_owner_uid"
execution_workspace_gid="$execution_owner_gid"
if [[ "$(id -u)" == 0 ]]; then
  execution_runtime_uid=10003
  execution_runtime_gid=10003
  execution_gateway_uid="$(existing_env_value HERMES_UID)"
  execution_gateway_uid="${execution_gateway_uid:-10000}"
  [[ "$execution_gateway_uid" =~ ^[1-9][0-9]*$ ]] \
    || die "HERMES_UID must be a non-root numeric uid."
  execution_workspace_uid=10002
  execution_workspace_gid=10002
fi
install -d -m 0700 -o "$execution_owner_uid" -g "$execution_owner_gid" \
  "$ROOT_DIR/data/stack-secrets/execution"
install -d -m 0700 -o "$execution_runtime_uid" -g "$execution_runtime_gid" \
  "$ROOT_DIR/data/stack-secrets/execution/docker-state" \
  "$ROOT_DIR/data/stack-secrets/execution/ssh-state" \
  "$ROOT_DIR/data/stack-secrets/execution/approver-state" \
  "$ROOT_DIR/data/stack-secrets/execution/ssh"
for execution_file in control-secret users; do
  execution_path="$ROOT_DIR/data/stack-secrets/execution/$execution_file"
  [[ -e "$execution_path" ]] \
    || install -m 0640 -o "$execution_gateway_uid" -g "$execution_runtime_gid" \
      /dev/null "$execution_path"
  chown "$execution_gateway_uid:$execution_runtime_gid" "$execution_path"
  chmod 640 "$execution_path"
done
for execution_file in approval-request-secret approval-signing-key.pem approval-public-key.pem approval-bot-token ssh-profile-integrity-secret; do
  execution_path="$ROOT_DIR/data/stack-secrets/execution/$execution_file"
  [[ -e "$execution_path" ]] \
    || install -m 0600 -o "$execution_runtime_uid" -g "$execution_runtime_gid" \
      /dev/null "$execution_path"
  chown "$execution_runtime_uid:$execution_runtime_gid" "$execution_path"
  chmod 600 "$execution_path"
done
install -d -m 0700 -o "$execution_workspace_uid" -g "$execution_workspace_gid" \
  "$ROOT_DIR/data/execution-workspace"
# Root installations assign the isolated runtime IDs. Unprivileged dry runs and
# fixtures retain their caller's IDs; manage.sh applies runtime ownership before
# execution can be enabled on a deployed root-managed stack.
chown "$execution_owner_uid:$execution_owner_gid" "$ROOT_DIR/data/stack-secrets" 2>/dev/null || true
backup_existing

nine_bind="$(existing_env_value NINEROUTER_BIND_IP)"; nine_bind="${nine_bind:-${lan_ip:-127.0.0.1}}"
nine_port="$(existing_env_value NINEROUTER_PORT)"; nine_port="${nine_port:-20128}"
nine_password="$(existing_env_value NINEROUTER_INITIAL_PASSWORD)"; nine_password="${nine_password:-not-installed}"
nine_require_key="$(existing_env_value NINEROUTER_REQUIRE_API_KEY)"; nine_require_key="${nine_require_key:-false}"
nine_cookie_secure="$(existing_env_value NINEROUTER_AUTH_COOKIE_SECURE)"; nine_cookie_secure="${nine_cookie_secure:-false}"
nine_public_url="$(existing_env_value NINEROUTER_PUBLIC_BASE_URL)"; nine_public_url="${nine_public_url:-http://localhost:20128}"
existing_nine_jwt="$(existing_env_value NINEROUTER_JWT_SECRET)"
existing_nine_key_secret="$(existing_env_value NINEROUTER_API_KEY_SECRET)"
existing_nine_salt="$(existing_env_value NINEROUTER_MACHINE_ID_SALT)"
nine_jwt="${existing_nine_jwt:-$(random_hex 32)}"
nine_key_secret="${existing_nine_key_secret:-$(random_hex 32)}"
nine_salt="${existing_nine_salt:-$(random_hex 32)}"

if [[ "$configure_nine" == true ]]; then
  printf '\n9router settings\n'
  printf '%s\n' '----------------'
  nine_bind="$(prompt_bind_ip "9router host bind address" "$(suggested_bind_ip "$nine_bind")")"
  nine_port="$(prompt_port "Dashboard/API port" "20128")"
  nine_password="$(prompt_secret "Initial 9router dashboard password")"
  nine_public_url="$(prompt "Public dashboard URL (or local URL)" "http://$(service_url_host "$nine_bind"):$nine_port")"
  if [[ "$nine_public_url" == https://* ]]; then nine_cookie_secure="true"; fi
  if confirm "Require a 9router Bearer API key on /v1 routes?" n; then
    nine_require_key="true"
  fi
fi

openwebui_bind="$(existing_env_value OPENWEBUI_BIND_IP)"; openwebui_bind="${openwebui_bind:-${lan_ip:-127.0.0.1}}"
openwebui_port="$(existing_env_value OPENWEBUI_PORT)"; openwebui_port="${openwebui_port:-3000}"
openwebui_url="$(existing_env_value OPENWEBUI_URL)"; openwebui_url="${openwebui_url:-http://localhost:3000}"
existing_openwebui_secret="$(existing_env_value OPENWEBUI_SECRET_KEY)"
openwebui_secret="${existing_openwebui_secret:-$(random_hex 32)}"
openwebui_api_url="$(existing_env_value OPENWEBUI_OPENAI_BASE_URL)"; openwebui_api_url="${openwebui_api_url:-http://nine-router:20128/v1}"
openwebui_api_key="$(existing_env_value OPENWEBUI_OPENAI_API_KEY)"; openwebui_api_key="${openwebui_api_key:-local-no-auth}"
openwebui_signup="$(existing_env_value OPENWEBUI_ENABLE_SIGNUP)"; openwebui_signup="${openwebui_signup:-true}"

# The current Hermes image requires an auth provider for non-loopback dashboard
# binds. The installer always provisions Basic Auth when the dashboard is enabled,
# so the same configuration works safely on localhost, a trusted LAN, or Caddy.
hermes_bind="$(existing_env_value HERMES_BIND_IP)"; hermes_bind="${hermes_bind:-127.0.0.1}"
hermes_api_port="$(existing_env_value HERMES_API_PORT)"; hermes_api_port="${hermes_api_port:-8642}"
hermes_dashboard_port="$(existing_env_value HERMES_DASHBOARD_PORT)"; hermes_dashboard_port="${hermes_dashboard_port:-9119}"
hermes_dashboard="$(existing_env_value HERMES_DASHBOARD)"; hermes_dashboard="${hermes_dashboard:-0}"
hermes_dashboard_username="$(existing_env_value HERMES_DASHBOARD_BASIC_AUTH_USERNAME)"
hermes_dashboard_password_hash="$(existing_env_value HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH)"
hermes_dashboard_secret="$(existing_env_value HERMES_DASHBOARD_BASIC_AUTH_SECRET)"
hermes_dashboard_password=""
hermes_dashboard_credentials_created=false
if [[ -f "$HERMES_DASHBOARD_ACCESS_FILE" ]]; then
  hermes_dashboard_password="$(read_unique_env_value "$HERMES_DASHBOARD_ACCESS_FILE" HERMES_DASHBOARD_PASSWORD)"
fi
provider_name="9router"
provider_url="http://nine-router:20128/v1"
provider_key="local-no-auth"
model_name="ai"
telegram_token="$(existing_hermes_env_value TELEGRAM_BOT_TOKEN)"
telegram_ids="$(existing_hermes_env_value TELEGRAM_ALLOWED_USERS)"
telegram_home="$(existing_hermes_env_value TELEGRAM_HOME_CHANNEL)"
api_enabled="$(existing_hermes_env_value API_SERVER_ENABLED)"; api_enabled="${api_enabled:-false}"
api_key="$(existing_hermes_env_value API_SERVER_KEY)"
smart_router_image_repository="$(existing_env_value SMART_ROUTER_IMAGE_REPOSITORY)"; smart_router_image_repository="${smart_router_image_repository:-afsharidevops/hermes-smart-router}"
smart_router_image_tag="$(existing_env_value SMART_ROUTER_IMAGE_TAG)"; smart_router_image_tag="${smart_router_image_tag:-0.5.9}"
smart_router_bind="$(existing_env_value SMART_ROUTER_BIND_IP)"; smart_router_bind="${smart_router_bind:-127.0.0.1}"
smart_router_port="$(existing_env_value SMART_ROUTER_PORT)"; smart_router_port="${smart_router_port:-8787}"
smart_router_mode="$(existing_env_value SMART_ROUTER_MODE)"; smart_router_mode="${smart_router_mode:-observe}"
smart_router_policy="$(existing_env_value SMART_ROUTER_POLICY)"; smart_router_policy="${smart_router_policy:-heuristic}"
smart_router_allow_tier_overrides="$(existing_env_value SMART_ROUTER_ALLOW_TIER_OVERRIDES)"; smart_router_allow_tier_overrides="${smart_router_allow_tier_overrides:-false}"
smart_router_dashboard_enabled="$(existing_env_value SMART_ROUTER_DASHBOARD_ENABLED)"; smart_router_dashboard_enabled="${smart_router_dashboard_enabled:-true}"
smart_router_control_plane_enabled="$(existing_env_value SMART_ROUTER_CONTROL_PLANE_ENABLED)"; smart_router_control_plane_enabled="${smart_router_control_plane_enabled:-true}"
smart_router_require_auth="$(existing_env_value SMART_ROUTER_REQUIRE_AUTH)"; smart_router_require_auth="${smart_router_require_auth:-true}"
smart_router_provider_health_enabled="$(existing_env_value SMART_ROUTER_PROVIDER_HEALTH_ENABLED)"; smart_router_provider_health_enabled="${smart_router_provider_health_enabled:-true}"
smart_router_coding_model="$(existing_env_value SMART_ROUTER_CODING_MODEL)"
smart_router_vision_model="$(existing_env_value SMART_ROUTER_VISION_MODEL)"
smart_router_secret="$(existing_env_value SMART_ROUTER_HMAC_SECRET)"; smart_router_secret="${smart_router_secret:-$(random_hex 32)}"
smart_router_fast_model="$(existing_env_value SMART_ROUTER_FAST_MODEL)"; smart_router_fast_model="${smart_router_fast_model:-combo-fast}"
smart_router_standard_model="$(existing_env_value SMART_ROUTER_STANDARD_MODEL)"; smart_router_standard_model="${smart_router_standard_model:-combo-standard}"
smart_router_strong_model="$(existing_env_value SMART_ROUTER_STRONG_MODEL)"; smart_router_strong_model="${smart_router_strong_model:-combo-strong}"
smart_router_coding_model="${smart_router_coding_model:-combo-strong}"
smart_router_vision_model="${smart_router_vision_model:-combo-strong}"
smart_router_observe_model="$(existing_env_value SMART_ROUTER_OBSERVE_MODEL)"; smart_router_observe_model="${smart_router_observe_model:-ai}"
smart_router_fail_open_model="$(existing_env_value SMART_ROUTER_FAIL_OPEN_MODEL)"; smart_router_fail_open_model="${smart_router_fail_open_model:-ai}"
smart_router_ttl="$(existing_env_value SMART_ROUTER_SESSION_TTL_SECONDS)"; smart_router_ttl="${smart_router_ttl:-2700}"
smart_router_max_age="$(existing_env_value SMART_ROUTER_MAX_SESSION_AGE_SECONDS)"; smart_router_max_age="${smart_router_max_age:-43200}"
smart_router_demotion="$(existing_env_value SMART_ROUTER_DEMOTION_TURNS)"; smart_router_demotion="${smart_router_demotion:-5}"
smart_router_fast_tokens="$(existing_env_value SMART_ROUTER_FAST_MAX_TOKENS)"; smart_router_fast_tokens="${smart_router_fast_tokens:-1024}"
smart_router_standard_tokens="$(existing_env_value SMART_ROUTER_STANDARD_MAX_TOKENS)"; smart_router_standard_tokens="${smart_router_standard_tokens:-4096}"
smart_router_strong_tokens="$(existing_env_value SMART_ROUTER_STRONG_MAX_TOKENS)"; smart_router_strong_tokens="${smart_router_strong_tokens:-6144}"
smart_router_connect_timeout="$(existing_env_value SMART_ROUTER_CONNECT_TIMEOUT_SECONDS)"; smart_router_connect_timeout="${smart_router_connect_timeout:-10}"
smart_router_read_timeout="$(existing_env_value SMART_ROUTER_READ_TIMEOUT_SECONDS)"; smart_router_read_timeout="${smart_router_read_timeout:-600}"
smart_router_max_bytes="$(existing_env_value SMART_ROUTER_MAX_REQUEST_BYTES)"; smart_router_max_bytes="${smart_router_max_bytes:-10485760}"
nine_image="$(existing_env_value NINEROUTER_IMAGE)"; nine_image="${nine_image:-decolua/9router:latest}"
hermes_image="$(existing_env_value HERMES_IMAGE)"; hermes_image="${hermes_image:-nousresearch/hermes-agent:latest}"
openwebui_image="$(existing_env_value OPENWEBUI_IMAGE)"; openwebui_image="${openwebui_image:-ghcr.io/open-webui/open-webui:main}"
caddy_image="$(existing_env_value CADDY_IMAGE)"; caddy_image="${caddy_image:-caddy:2-alpine}"
execution_features="$(existing_env_value EXECUTION_FEATURES)"
execution_generation="$(existing_env_value EXECUTION_POLICY_GENERATION)"; execution_generation="${execution_generation:-0}"
execution_workspace_generation="$(existing_env_value EXECUTION_WORKSPACE_GENERATION)"; execution_workspace_generation="${execution_workspace_generation:-$execution_generation}"
execution_broker_image="$(existing_env_value EXECUTION_BROKER_IMAGE)"; execution_broker_image="${execution_broker_image:-afsharidevops/hermes-execution-broker:0.1.3}"
execution_sandbox_image="$(existing_env_value EXECUTION_SANDBOX_IMAGE)"; execution_sandbox_image="${execution_sandbox_image:-python:3.13.5-slim-bookworm@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419}"
execution_run_as="$(existing_env_value EXECUTION_RUN_AS)"; execution_run_as="${execution_run_as:-$execution_runtime_uid:$execution_runtime_gid}"
execution_docker_gid="$(existing_env_value EXECUTION_DOCKER_GID)"; execution_docker_gid="${execution_docker_gid:-$(stat -c %g /var/run/docker.sock 2>/dev/null || printf 65534)}"
execution_workspace="$(existing_env_value EXECUTION_WORKSPACE_HOST_PATH)"; execution_workspace="${execution_workspace:-$ROOT_DIR/data/execution-workspace}"
caddy_bind="$(existing_env_value CADDY_BIND_IP)"; caddy_bind="${caddy_bind:-0.0.0.0}"
n8n_image="$(existing_env_value N8N_IMAGE)"; n8n_image="${n8n_image:-n8nio/n8n:latest}"
n8n_bind="$(existing_env_value N8N_BIND_IP)"; n8n_bind="${n8n_bind:-${lan_ip:-127.0.0.1}}"
n8n_port="$(existing_env_value N8N_PORT)"; n8n_port="${n8n_port:-5678}"
n8n_hostname="$(existing_env_value N8N_HOSTNAME)"; n8n_hostname="${n8n_hostname:-localhost}"
n8n_protocol="$(existing_env_value N8N_PROTOCOL)"; n8n_protocol="${n8n_protocol:-http}"
n8n_public_url="$(existing_env_value N8N_PUBLIC_URL)"; n8n_public_url="${n8n_public_url:-http://localhost:5678}"
n8n_secure_cookie="$(existing_env_value N8N_SECURE_COOKIE)"; n8n_secure_cookie="${n8n_secure_cookie:-false}"
n8n_proxy_hops="$(existing_env_value N8N_PROXY_HOPS)"; n8n_proxy_hops="${n8n_proxy_hops:-0}"
n8n_timezone="$(existing_env_value N8N_TIMEZONE)"; n8n_timezone="${n8n_timezone:-Asia/Tehran}"
n8n_diagnostics="$(existing_env_value N8N_DIAGNOSTICS_ENABLED)"; n8n_diagnostics="${n8n_diagnostics:-false}"
n8n_version_notifications="$(existing_env_value N8N_VERSION_NOTIFICATIONS_ENABLED)"; n8n_version_notifications="${n8n_version_notifications:-false}"
# Never regenerate: rotating this key makes every stored n8n credential undecryptable.
existing_n8n_key="$(existing_env_value N8N_ENCRYPTION_KEY)"
n8n_encryption_key="${existing_n8n_key:-$(random_hex 32)}"
# Existing generic variables came from the workflow-trigger-only implementation.
# Infer that mode so upgrades preserve the old endpoint until the operator selects
# another one, then migrate the secret into its unambiguous mode-specific key.
legacy_n8n_mcp_token="$(existing_hermes_env_value N8N_MCP_TOKEN)"
n8n_trigger_mcp_token="$(existing_hermes_env_value N8N_TRIGGER_MCP_TOKEN)"
n8n_trigger_mcp_token="${n8n_trigger_mcp_token:-$legacy_n8n_mcp_token}"
n8n_instance_mcp_token="$(existing_hermes_env_value N8N_INSTANCE_MCP_TOKEN)"
n8n_mcp_mode="$(existing_env_value N8N_MCP_MODE)"
if [[ -z "$n8n_mcp_mode" ]]; then
  if [[ -n "$n8n_trigger_mcp_token" ]]; then n8n_mcp_mode=trigger; else n8n_mcp_mode=off; fi
fi
[[ "$n8n_mcp_mode" == instance || "$n8n_mcp_mode" == trigger || "$n8n_mcp_mode" == off ]] \
  || die "N8N_MCP_MODE must be instance, trigger, or off."
n8n_mcp_previous_mode="$n8n_mcp_mode"
n8n_mcp_was_enabled=false
[[ "$n8n_mcp_mode" != off ]] && n8n_mcp_was_enabled=true
install_n8n_mcp=false
case "$n8n_mcp_mode" in
  trigger) [[ -n "$n8n_trigger_mcp_token" ]] && install_n8n_mcp=true ;;
  instance) [[ -n "$n8n_instance_mcp_token" ]] && install_n8n_mcp=true ;;
esac

if [[ "$configure_smart_router" == true && "$install_smart_router" == true ]]; then
  printf '\nHermes Smart Router v0.5.9 settings\n'
  printf '%s\n' '-----------------------------------'
  printf '%s\n' 'Smart routing applies to model=auto. Tier aliases auto-fast/auto-standard/auto-strong are exposed only when tier overrides are enabled.'
  printf '%s\n' 'Explicit upstream model names pass through without automatic tier selection.'
  printf '%s\n' ''
  printf '%s\n' 'Router modes:'
  printf '%s\n' '  observe - evaluate auto requests and record decisions, but dispatch through SMART_ROUTER_OBSERVE_MODEL (recommended first)'
  printf '%s\n' '  route   - apply Smart Router decisions/profiles to auto requests; explicit model names still pass through'
  smart_router_bind="$(prompt_bind_ip "Smart Router bind address" "$smart_router_bind")"
  smart_router_port="$(prompt_port "Smart Router HTTP/API/dashboard/control-plane port" "$smart_router_port")"
  smart_router_mode="$(prompt "Initial mode: observe or route" "$smart_router_mode")"
  [[ "$smart_router_mode" == observe || "$smart_router_mode" == route ]] \
    || die "Smart Router mode must be observe or route."
  printf '%s\n' 'Routing policies: heuristic (built-in/default), calibrated (requires calibrated.json), learned (requires learned model artifacts).'
  smart_router_policy="$(prompt "Routing policy: heuristic, calibrated, or learned" "$smart_router_policy")"
  [[ "$smart_router_policy" == heuristic || "$smart_router_policy" == calibrated || "$smart_router_policy" == learned ]] \
    || die "Smart Router policy must be heuristic, calibrated, or learned."
  if [[ "$smart_router_policy" == calibrated && ! -s "$ROOT_DIR/smart-router/policy/calibrated.json" ]]; then
    die "calibrated policy selected but smart-router/policy/calibrated.json is missing."
  fi
  if [[ "$smart_router_policy" == learned && ! -s "$ROOT_DIR/smart-router/policy/learned-v4.joblib" ]]; then
    warn "learned policy selected but smart-router/policy/learned-v4.joblib is currently missing; create/train the artifact before starting route mode."
  fi
  if confirm "Expose tier override aliases auto-fast/auto-standard/auto-strong?" "$([[ "$smart_router_allow_tier_overrides" == true ]] && printf y || printf n)"; then
    smart_router_allow_tier_overrides=true
  else
    smart_router_allow_tier_overrides=false
  fi
  if confirm "Enable the Smart Router telemetry dashboard (/dashboard)?" "$([[ "$smart_router_dashboard_enabled" == true ]] && printf y || printf n)"; then smart_router_dashboard_enabled=true; else smart_router_dashboard_enabled=false; fi
  if confirm "Enable the v0.5.9 Hermes Operations Center (/control)?" "$([[ "$smart_router_control_plane_enabled" == true ]] && printf y || printf n)"; then smart_router_control_plane_enabled=true; else smart_router_control_plane_enabled=false; fi
  if [[ "$smart_router_control_plane_enabled" == true ]]; then
    if confirm "Require authentication for Smart Router API/control-plane requests?" "$([[ "$smart_router_require_auth" == true ]] && printf y || printf n)"; then smart_router_require_auth=true; else smart_router_require_auth=false; fi
    [[ "$smart_router_require_auth" == true ]] || warn "Authentication is disabled. Keep the Smart Router bound to loopback unless you fully understand the exposure risk."
  fi
  if confirm "Enable provider/model health registry and circuit-breaker fallback?" "$([[ "$smart_router_provider_health_enabled" == true ]] && printf y || printf n)"; then smart_router_provider_health_enabled=true; else smart_router_provider_health_enabled=false; fi
  smart_router_fast_model="$(prompt "Fast route-profile 9router model/combo" "$smart_router_fast_model")"
  smart_router_standard_model="$(prompt "Standard route-profile 9router model/combo" "$smart_router_standard_model")"
  smart_router_strong_model="$(prompt "Strong route-profile 9router model/combo" "$smart_router_strong_model")"
  smart_router_coding_model="$(prompt "Coding route-profile 9router model/combo" "$smart_router_coding_model")"
  smart_router_vision_model="$(prompt "Vision route-profile 9router model/combo" "$smart_router_vision_model")"
  info "The fast/standard/strong values are Smart Router route-profile defaults. 9router controls the actual provider/model composition behind those combo names."
  info "Dashboard URL after start: http://$(service_url_host "$smart_router_bind"):$smart_router_port/dashboard"
  info "Operations Center URL after start: http://$(service_url_host "$smart_router_bind"):$smart_router_port/control/"
fi

if [[ "$configure_n8n" == true && "$install_n8n" == true ]]; then
  printf '\nn8n settings\n'
  printf '%s\n' '------------'
  n8n_bind="$(prompt_bind_ip "n8n editor host bind address" "$(suggested_bind_ip "$n8n_bind")")"
  n8n_port="$(prompt_port "n8n editor port" "$n8n_port")"
  n8n_public_url="$(prompt "Public n8n URL (or local URL)" "http://$(service_url_host "$n8n_bind"):$n8n_port")"
  case "$n8n_public_url" in
    https://*) n8n_protocol="https"; n8n_secure_cookie="true" ;;
    http://*) n8n_protocol="http" ;;
    *) die "The n8n public URL must start with http:// or https://." ;;
  esac
  n8n_hostname="${n8n_public_url#*://}"
  n8n_hostname="${n8n_hostname%%/*}"
  if [[ "$n8n_hostname" == \[*\]* ]]; then
    n8n_hostname="${n8n_hostname#\[}"; n8n_hostname="${n8n_hostname%%\]*}"
  else
    n8n_hostname="${n8n_hostname%%:*}"
  fi
  [[ -n "$n8n_hostname" ]] || die "The n8n public URL must include a host."
  n8n_timezone="$(prompt "n8n timezone (IANA name)" "$n8n_timezone")"
  if [[ -z "$existing_n8n_key" ]]; then
    info "A new n8n encryption key was generated. Back it up: stored n8n credentials cannot be decrypted without it."
  else
    info "Reusing the existing n8n encryption key so stored credentials stay readable."
  fi
fi

# The MCP bridge only makes sense when Hermes and n8n are both present. Instance
# mode is intentionally explicit because its user-bound token has much broader
# n8n management authority than the workflow-scoped trigger mode.
if [[ "$install_hermes" == true && "$install_n8n" == true ]]; then
  choose_n8n_mcp=false
  if [[ "$n8n_mcp_mode" != off ]]; then
    if [[ "$configure_n8n" == true || "$configure_hermes" == true ]]; then
      confirm "Keep the Hermes MCP connection to n8n?" y || { n8n_mcp_mode=off; choose_n8n_mcp=true; }
    fi
  elif confirm "Let Hermes connect to n8n over MCP?" n; then
    choose_n8n_mcp=true
  fi
  if [[ "$choose_n8n_mcp" == true && "$n8n_mcp_mode" != off ]] \
    || [[ "$choose_n8n_mcp" == true && "$n8n_mcp_previous_mode" == off ]]; then
    printf '%s\n' '1) Instance-level MCP (user-scoped n8n MCP access; tools depend on installed n8n version)'
    printf '%s\n' '2) MCP Server Trigger (only explicitly connected workflow tools)'
    while true; do
      n8n_mcp_selection="$(prompt "Choose n8n MCP mode" "1")"
      case "$n8n_mcp_selection" in
        1) n8n_mcp_mode=instance; break ;;
        2) n8n_mcp_mode=trigger; break ;;
        *) warn "Choose 1 or 2." >&2 ;;
      esac
    done
  fi
  case "$n8n_mcp_mode" in
    trigger)
      if [[ -z "$n8n_trigger_mcp_token" ]]; then
        n8n_trigger_mcp_token="$(random_hex 32)"
        info "Generated a bearer token for the managed MCP Server Trigger credential."
      fi
      install_n8n_mcp=true
      ;;
    instance)
      if [[ -n "$n8n_instance_mcp_token" ]]; then
        install_n8n_mcp=true
      else
        install_n8n_mcp=false
        warn "Instance-level MCP needs an n8n owner account first. After n8n starts, this wizard will offer to collect the owner API key and n8n-generated MCP token securely; you can also finish later with ./manage.sh n8n-menu."
      fi
      ;;
    off) install_n8n_mcp=false ;;
  esac
else
  n8n_mcp_mode=off
  install_n8n_mcp=false
fi

if [[ "$install_smart_router" == true ]]; then
  provider_url="http://smart-router:8080/v1"
  openwebui_api_url="http://smart-router:8080/v1"
  model_name="auto"
elif [[ "$install_nine" == true ]]; then
  openwebui_api_url="http://nine-router:20128/v1"
  provider_url="http://nine-router:20128/v1"
  [[ "$model_name" == auto* ]] && model_name="ai"
fi

if [[ "$change_bind_ips" == true ]]; then
  printf '\nPublished container bind IPs\n'
  printf '%s\n' '----------------------------'
  if [[ "$install_nine" == true && "$configure_nine" != true ]]; then
    nine_bind="$(prompt_bind_ip "9router bind IP" "$(suggested_bind_ip "$nine_bind")")"
  fi
  if [[ "$install_hermes" == true && "$configure_hermes" != true ]]; then
    hermes_bind="$(prompt_bind_ip "Hermes API/dashboard bind IP" "$(suggested_bind_ip "$hermes_bind")")"
  fi
  if [[ "$install_webui" == true && "$configure_webui" != true ]]; then
    openwebui_bind="$(prompt_bind_ip "Open WebUI bind IP" "$(suggested_bind_ip "$openwebui_bind")")"
  fi
  if [[ "$install_n8n" == true && "$configure_n8n" != true ]]; then
    n8n_bind="$(prompt_bind_ip "n8n editor bind IP" "$(suggested_bind_ip "$n8n_bind")")"
  fi
  if [[ "$install_caddy" == true && "$configure_caddy" != true ]]; then
    caddy_bind="$(prompt_bind_ip "Caddy HTTP/HTTPS bind IP" "$caddy_bind")"
  fi
  if [[ ( "$install_nine" == true && "$nine_bind" == 0.0.0.0 ) \
    || ( "$install_hermes" == true && "$hermes_bind" == 0.0.0.0 ) \
    || ( "$install_webui" == true && "$openwebui_bind" == 0.0.0.0 ) \
    || ( "$install_n8n" == true && "$n8n_bind" == 0.0.0.0 ) \
    || ( "$install_caddy" == true && "$caddy_bind" == 0.0.0.0 ) ]]; then
    warn "0.0.0.0 publishes a service on every host interface; protect it with a firewall and authentication."
  fi
fi

if [[ "$configure_hermes" == true ]]; then
  printf '\nHermes Agent settings\n'
  printf '%s\n' '---------------------'
  hermes_bind="$(prompt_bind_ip "Hermes API/dashboard host bind address" "$(suggested_bind_ip "$hermes_bind")")"
  if [[ "$install_smart_router" == true ]]; then
    provider_url="http://smart-router:8080/v1"
    info "Hermes will reach 9router through the Smart Router in ${smart_router_mode} mode."
  elif [[ "$install_nine" == true ]]; then
    provider_url="http://nine-router:20128/v1"
    info "Hermes will reach 9router through the private Docker network."
  else
    provider_url="$(prompt "OpenAI-compatible API base URL (include /v1)" "http://host.docker.internal:20128/v1")"
  fi
  provider_name="$(prompt "Hermes provider name" "9router")"
  if [[ "$install_smart_router" == true ]]; then
    model_name="auto"
    info "Hermes model is set to auto; explicit /model selections still pass through unchanged."
  else
    model_name="$(prompt "9router model/combo name" "ai")"
  fi

  if [[ "$install_nine" == true ]]; then
    provider_key="auto-generated-after-9router-starts"
    info "A dedicated 9router API key will be configured automatically for Hermes."
  else
    provider_key="$(prompt_secret "9router/OpenAI-compatible API key")"
  fi

  if confirm "Enable Telegram connectivity?" y; then
    while true; do
      telegram_token="$(prompt_secret "Telegram BotFather token")"
      [[ "$telegram_token" =~ ^[0-9]+:[A-Za-z0-9_-]{20,}$ ]] && break
      warn "The token format does not look valid. Expected digits, a colon, then the token."
    done
    while true; do
      telegram_ids="$(prompt "Allowed numeric Telegram user IDs, comma-separated")"
      telegram_ids="${telegram_ids//[[:space:]]/}"
      valid_ids "$telegram_ids" && break
      warn "Use numeric IDs separated by commas, without usernames."
    done
    telegram_home="$(prompt_optional_integer "Optional Telegram home chat ID for cron results (Enter to skip)")"
  fi

  dashboard_default=n
  [[ "$hermes_dashboard" == 1 ]] && dashboard_default=y
  if confirm "Enable the built-in Hermes dashboard with generated username/password authentication?" "$dashboard_default"; then
    hermes_dashboard="1"
    hermes_dashboard_port="$(prompt_port "Hermes dashboard port" "$hermes_dashboard_port")"
    if [[ -n "$hermes_dashboard_username" && -n "$hermes_dashboard_password_hash" \
      && -n "$hermes_dashboard_secret" && -n "$hermes_dashboard_password" ]]; then
      info "Reusing the existing Hermes dashboard credentials."
    else
      hermes_dashboard_username="hermes_$(random_hex 6)"
      hermes_dashboard_password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
      hermes_dashboard_secret="$(random_hex 32)"
      hermes_dashboard_password_hash=""
      hermes_dashboard_credentials_created=true
      info "Generated a new Hermes dashboard username and password."
    fi
    if [[ "$hermes_bind" == 127.0.0.1 ]]; then
      info "The dashboard will be localhost-only. From another machine, use: ssh -L ${hermes_dashboard_port}:127.0.0.1:${hermes_dashboard_port} USER@SERVER"
    else
      info "Hermes Basic Auth will protect the dashboard on ${hermes_bind}:${hermes_dashboard_port}."
    fi
  else
    hermes_dashboard="0"
  fi

  if confirm "Enable the Hermes OpenAI-compatible API?" n; then
    api_enabled="true"
    hermes_api_port="$(prompt_port "Hermes API port" "8642")"
    api_key="$(random_hex 32)"
  fi
fi

if [[ "$configure_webui" == true ]]; then
  printf '\nOpen WebUI settings\n'
  printf '%s\n' '-------------------'
  openwebui_bind="$(prompt_bind_ip "Open WebUI host bind address" "$(suggested_bind_ip "$openwebui_bind")")"
  openwebui_port="$(prompt_port "Open WebUI port" "3000")"
  openwebui_url="$(prompt "Open WebUI public URL (or local URL)" "http://$(service_url_host "$openwebui_bind"):$openwebui_port")"

  if [[ "$install_nine" == true ]]; then
    if [[ "$install_smart_router" == true ]]; then
      openwebui_api_url="http://smart-router:8080/v1"
      info "Open WebUI will reach 9router through the Smart Router."
    else
      openwebui_api_url="http://nine-router:20128/v1"
      info "Open WebUI will reach 9router through the private Docker network."
    fi
    openwebui_api_key="auto-generated-after-9router-starts"
    info "A dedicated 9router API key and OpenCode-Free model will be configured automatically."
  else
    openwebui_api_url="$(prompt "OpenAI-compatible API base URL for Open WebUI" "http://host.docker.internal:20128/v1")"
    openwebui_api_key="$(prompt_secret "OpenAI-compatible API key for Open WebUI")"
  fi

  if ! confirm "Allow Open WebUI account signup?" n; then
    openwebui_signup="false"
    warn "With signup disabled, create or confirm an administrator account before exposing Open WebUI."
  fi
fi

caddy_email=""
caddy_nine_domain=""
caddy_webui_domain=""
caddy_hermes_dashboard_domain=""
caddy_hermes_api_domain=""
caddy_n8n_domain=""

if [[ "$install_nine" == true || "$install_webui" == true || "$install_n8n" == true \
  || "$hermes_dashboard" == 1 || "$api_enabled" == true ]]; then
  if [[ "$install_caddy" == true ]]; then
    confirm "Reconfigure existing Caddy domains?" n && configure_caddy=true
  elif confirm "Add optional Caddy domains with automatic HTTPS?" n; then
    configure_caddy=true
  fi

  if [[ "$configure_caddy" == true ]]; then
    install_caddy=true
    printf '\nCaddy domain settings\n'
    printf '%s\n' '---------------------'
    caddy_bind="$(prompt_bind_ip "Caddy HTTP/HTTPS host bind address (public HTTPS normally needs 0.0.0.0)" "$caddy_bind")"
    caddy_email="$(prompt "Optional ACME email for certificate notices (Enter to skip)")"
    if [[ -n "$caddy_email" && ( "$caddy_email" != *@* || "$caddy_email" == *[[:space:]]* ) ]]; then
      die "The Caddy certificate email is not valid."
    fi

    declare -A selected_domains=()
    if [[ "$install_nine" == true ]] && confirm "Publish 9router with a domain?" y; then
      caddy_nine_domain="$(prompt_domain "9router domain")"
      selected_domains["$caddy_nine_domain"]=1
      nine_public_url="https://$caddy_nine_domain"
      nine_cookie_secure="true"
    fi
    if [[ "$install_webui" == true ]] && confirm "Publish Open WebUI with a domain?" y; then
      caddy_webui_domain="$(prompt_domain "Open WebUI domain")"
      [[ -z "${selected_domains[$caddy_webui_domain]+x}" ]] || die "Each service needs a unique domain."
      selected_domains["$caddy_webui_domain"]=1
      openwebui_url="https://$caddy_webui_domain"
    fi
    if [[ "$hermes_dashboard" == 1 ]] && confirm "Publish the Hermes dashboard with a domain?" n; then
      caddy_hermes_dashboard_domain="$(prompt_domain "Hermes dashboard domain")"
      [[ -z "${selected_domains[$caddy_hermes_dashboard_domain]+x}" ]] || die "Each service needs a unique domain."
      selected_domains["$caddy_hermes_dashboard_domain"]=1
    fi
    if [[ "$api_enabled" == true ]] && confirm "Publish the Hermes API with a domain?" n; then
      caddy_hermes_api_domain="$(prompt_domain "Hermes API domain")"
      [[ -z "${selected_domains[$caddy_hermes_api_domain]+x}" ]] || die "Each service needs a unique domain."
      selected_domains["$caddy_hermes_api_domain"]=1
    fi
    if [[ "$install_n8n" == true ]] && confirm "Publish n8n with a domain?" n; then
      caddy_n8n_domain="$(prompt_domain "n8n domain")"
      [[ -z "${selected_domains[$caddy_n8n_domain]+x}" ]] || die "Each service needs a unique domain."
      selected_domains["$caddy_n8n_domain"]=1
      n8n_public_url="https://$caddy_n8n_domain"
      n8n_hostname="$caddy_n8n_domain"
      n8n_protocol="https"
      n8n_secure_cookie="true"
      n8n_proxy_hops="1"
    fi

    if ((${#selected_domains[@]} == 0)); then
      warn "No domains were selected; Caddy will not be installed."
      install_caddy=false
    else
      warn "Caddy needs public DNS records plus inbound TCP 80/443 and UDP 443."
      if [[ -n "$caddy_nine_domain" && "$nine_require_key" != true ]]; then
        warn "9router /v1 will be public without Bearer-key enforcement. Enable REQUIRE_API_KEY after creating a 9router endpoint key."
      fi
      if [[ -n "$caddy_webui_domain" && "$openwebui_signup" == true ]]; then
        warn "Create the first Open WebUI administrator promptly, then disable signup in its Admin Panel."
      fi
      if [[ -n "$caddy_n8n_domain" ]]; then
        warn "n8n will be public. Create its owner account immediately: the first visitor to an unclaimed instance becomes the owner."
      fi
    fi
  fi
fi

[[ "$install_caddy" == true ]] && profiles="${profiles:+$profiles,}caddy"

invoking_uid="${SUDO_UID:-$(id -u)}"
invoking_gid="${SUDO_GID:-$(id -g)}"
# The Hermes image refuses to run its gateway as root. A direct-root install has
# no SUDO_UID/SUDO_GID to map, so retain a valid existing identity or use the
# image's 10000:10000 default instead of emitting HERMES_UID=0/HERMES_GID=0.
existing_hermes_uid="$(existing_env_value HERMES_UID)"
existing_hermes_gid="$(existing_env_value HERMES_GID)"
if [[ "$invoking_uid" == 0 ]]; then
  hermes_uid="${existing_hermes_uid:-10000}"
  hermes_gid="${existing_hermes_gid:-10000}"
  [[ "$hermes_uid" != 0 ]] || hermes_uid=10000
  [[ "$hermes_gid" != 0 ]] || hermes_gid=10000
else
  hermes_uid="$invoking_uid"
  hermes_gid="$invoking_gid"
fi

if [[ "$hermes_dashboard" == 1 ]]; then
  [[ -n "$hermes_dashboard_username" && -n "$hermes_dashboard_password" \
    && -n "$hermes_dashboard_secret" ]] \
    || die "Hermes dashboard authentication is incomplete; reconfigure Hermes to provision credentials."
  if [[ -z "$hermes_dashboard_password_hash" || "$hermes_dashboard_credentials_created" == true ]]; then
    hermes_dashboard_password_hash="$(printf '%s' "$hermes_dashboard_password" | hash_hermes_dashboard_password)"
  fi
  [[ "$hermes_dashboard_password_hash" == scrypt\$* ]] \
    || die "Hermes dashboard password hash is invalid; reconfigure Hermes to rotate credentials."

  dashboard_access_tmp="$(mktemp "$ROOT_DIR/data/stack-secrets/hermes-dashboard-access.env.tmp.XXXXXX")"
  {
    printf 'HERMES_DASHBOARD_USERNAME=%s\n' "$(dotenv_quote "$hermes_dashboard_username")"
    printf 'HERMES_DASHBOARD_PASSWORD=%s\n' "$(dotenv_quote "$hermes_dashboard_password")"
  } > "$dashboard_access_tmp"
  chmod 600 "$dashboard_access_tmp"
  chown "$execution_owner_uid:$execution_owner_gid" "$dashboard_access_tmp"
  mv "$dashboard_access_tmp" "$HERMES_DASHBOARD_ACCESS_FILE"
fi

tmp_env="$(mktemp "$ROOT_DIR/.env.tmp.XXXXXX")"
if [[ -f "$ENV_FILE" ]]; then
  cp "$ENV_FILE" "$tmp_env"
else
  cp "$ROOT_DIR/.env.example" "$tmp_env"
fi
chmod 600 "$tmp_env"
# Preserve every v0.5.x setting and update only values owned by this wizard.
replace_env_value "$tmp_env" COMPOSE_PROFILES "$profiles"
replace_env_value "$tmp_env" NINEROUTER_BIND_IP "$nine_bind"
replace_env_value "$tmp_env" NINEROUTER_PORT "$nine_port"
replace_env_value "$tmp_env" NINEROUTER_INITIAL_PASSWORD "$(dotenv_quote "$nine_password")"
replace_env_value "$tmp_env" NINEROUTER_JWT_SECRET "$nine_jwt"
replace_env_value "$tmp_env" NINEROUTER_API_KEY_SECRET "$nine_key_secret"
replace_env_value "$tmp_env" NINEROUTER_MACHINE_ID_SALT "$nine_salt"
replace_env_value "$tmp_env" NINEROUTER_REQUIRE_API_KEY "$nine_require_key"
replace_env_value "$tmp_env" NINEROUTER_AUTH_COOKIE_SECURE "$nine_cookie_secure"
replace_env_value "$tmp_env" NINEROUTER_PUBLIC_BASE_URL "$(dotenv_quote "$nine_public_url")"
replace_env_value "$tmp_env" HERMES_BIND_IP "$hermes_bind"
replace_env_value "$tmp_env" HERMES_API_PORT "$hermes_api_port"
replace_env_value "$tmp_env" HERMES_DASHBOARD_PORT "$hermes_dashboard_port"
replace_env_value "$tmp_env" HERMES_DASHBOARD "$hermes_dashboard"
replace_env_value "$tmp_env" HERMES_DASHBOARD_BASIC_AUTH_USERNAME "$(dotenv_quote "$hermes_dashboard_username")"
replace_env_value "$tmp_env" HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH "$(dotenv_literal_quote "$hermes_dashboard_password_hash")"
replace_env_value "$tmp_env" HERMES_DASHBOARD_BASIC_AUTH_SECRET "$(dotenv_quote "$hermes_dashboard_secret")"
replace_env_value "$tmp_env" HERMES_UID "$hermes_uid"
replace_env_value "$tmp_env" HERMES_GID "$hermes_gid"
replace_env_value "$tmp_env" EXECUTION_FEATURES "$execution_features"
replace_env_value "$tmp_env" EXECUTION_POLICY_GENERATION "$execution_generation"
replace_env_value "$tmp_env" EXECUTION_WORKSPACE_GENERATION "$execution_workspace_generation"
replace_env_value "$tmp_env" EXECUTION_BROKER_IMAGE "$execution_broker_image"
replace_env_value "$tmp_env" EXECUTION_SANDBOX_IMAGE "$execution_sandbox_image"
replace_env_value "$tmp_env" EXECUTION_RUN_AS "$execution_run_as"
replace_env_value "$tmp_env" EXECUTION_DOCKER_GID "$execution_docker_gid"
replace_env_value "$tmp_env" EXECUTION_WORKSPACE_HOST_PATH "$execution_workspace"
replace_env_value "$tmp_env" SMART_ROUTER_IMAGE_REPOSITORY "$smart_router_image_repository"
replace_env_value "$tmp_env" SMART_ROUTER_IMAGE_TAG "$smart_router_image_tag"
replace_env_value "$tmp_env" SMART_ROUTER_BIND_IP "$smart_router_bind"
replace_env_value "$tmp_env" SMART_ROUTER_PORT "$smart_router_port"
replace_env_value "$tmp_env" SMART_ROUTER_MODE "$smart_router_mode"
replace_env_value "$tmp_env" SMART_ROUTER_POLICY "$smart_router_policy"
replace_env_value "$tmp_env" SMART_ROUTER_ALLOW_TIER_OVERRIDES "$smart_router_allow_tier_overrides"
replace_env_value "$tmp_env" SMART_ROUTER_DASHBOARD_ENABLED "$smart_router_dashboard_enabled"
replace_env_value "$tmp_env" SMART_ROUTER_CONTROL_PLANE_ENABLED "$smart_router_control_plane_enabled"
replace_env_value "$tmp_env" SMART_ROUTER_REQUIRE_AUTH "$smart_router_require_auth"
replace_env_value "$tmp_env" SMART_ROUTER_PROVIDER_HEALTH_ENABLED "$smart_router_provider_health_enabled"
replace_env_value "$tmp_env" SMART_ROUTER_FAST_MODEL "$smart_router_fast_model"
replace_env_value "$tmp_env" SMART_ROUTER_STANDARD_MODEL "$smart_router_standard_model"
replace_env_value "$tmp_env" SMART_ROUTER_STRONG_MODEL "$smart_router_strong_model"
replace_env_value "$tmp_env" SMART_ROUTER_CODING_MODEL "$smart_router_coding_model"
replace_env_value "$tmp_env" SMART_ROUTER_VISION_MODEL "$smart_router_vision_model"
replace_env_value "$tmp_env" OPENWEBUI_BIND_IP "$openwebui_bind"
replace_env_value "$tmp_env" OPENWEBUI_PORT "$openwebui_port"
replace_env_value "$tmp_env" OPENWEBUI_URL "$(dotenv_quote "$openwebui_url")"
replace_env_value "$tmp_env" OPENWEBUI_SECRET_KEY "$openwebui_secret"
replace_env_value "$tmp_env" OPENWEBUI_OPENAI_BASE_URL "$(dotenv_quote "$openwebui_api_url")"
replace_env_value "$tmp_env" OPENWEBUI_ENABLE_SIGNUP "$openwebui_signup"
replace_env_value "$tmp_env" N8N_MCP_MODE "$n8n_mcp_mode"
replace_env_value "$tmp_env" N8N_BIND_IP "$n8n_bind"
replace_env_value "$tmp_env" N8N_PORT "$n8n_port"
replace_env_value "$tmp_env" N8N_HOSTNAME "$n8n_hostname"
replace_env_value "$tmp_env" N8N_PROTOCOL "$n8n_protocol"
replace_env_value "$tmp_env" N8N_PUBLIC_URL "$(dotenv_quote "$n8n_public_url")"
replace_env_value "$tmp_env" N8N_SECURE_COOKIE "$n8n_secure_cookie"
replace_env_value "$tmp_env" N8N_PROXY_HOPS "$n8n_proxy_hops"
replace_env_value "$tmp_env" N8N_TIMEZONE "$n8n_timezone"
replace_env_value "$tmp_env" N8N_DIAGNOSTICS_ENABLED "$n8n_diagnostics"
replace_env_value "$tmp_env" N8N_VERSION_NOTIFICATIONS_ENABLED "$n8n_version_notifications"
replace_env_value "$tmp_env" N8N_ENCRYPTION_KEY "$n8n_encryption_key"
replace_env_value "$tmp_env" CADDY_BIND_IP "$caddy_bind"
mv "$tmp_env" "$ENV_FILE"

# Generate any v0.5.x placeholder secrets without rotating existing values.
python3 - "$ENV_FILE" <<'PYV052'
import secrets, sys
p=sys.argv[1]
lines=open(p,encoding='utf-8').read().splitlines(); out=[]
force={'SMART_ROUTER_HMAC_SECRET','SMART_ROUTER_ADMIN_API_KEY','SMART_ROUTER_BOOTSTRAP_ADMIN_PASSWORD','SMART_ROUTER_PG_PASSWORD','SMART_ROUTER_CLIENT_API_KEY','OPENWEBUI_SECRET_KEY','N8N_ENCRYPTION_KEY'}
for line in lines:
    if '=' not in line or line.lstrip().startswith('#'):
        out.append(line); continue
    k,v=line.split('=',1)
    raw=v.strip().strip('"')
    if k.endswith('_FILE'):
        out.append(line); continue
    if k == 'SMART_ROUTER_BOOTSTRAP_ADMIN_USER' and raw == 'admin':
        v=secrets.token_urlsafe(16).replace('-','_').replace('_','')[:20]
    elif raw.startswith('CHANGE_ME') or (k in force and not raw):
        n=24 if k.endswith('INITIAL_PASSWORD') or k.endswith('ADMIN_PASSWORD') else 48
        v=secrets.token_urlsafe(n)
    out.append(f'{k}={v}')
open(p,'w',encoding='utf-8').write('\n'.join(out)+'\n')
PYV052
chmod 600 "$ENV_FILE"
client_key="$(existing_env_value SMART_ROUTER_CLIENT_API_KEY)"
if [[ "$install_smart_router" == true ]]; then
  # Trusted local clients authenticate to Smart Router, not directly to 9router.
  replace_env_value "$ENV_FILE" OPENWEBUI_OPENAI_API_KEY "$client_key"
fi

hermes_backend_key="$provider_key"
[[ "$install_smart_router" == true ]] && hermes_backend_key="$client_key"

if [[ "$configure_hermes" == true ]]; then
  config="$(<"$ROOT_DIR/templates/hermes-config.yaml.template")"
  config="${config//__PROVIDER_ID__/$(yaml_quote "custom:$provider_name")}"
  config="${config//__PROVIDER_NAME__/$(yaml_quote "$provider_name")}"
  config="${config//__PROVIDER_BASE_URL__/$(yaml_quote "$provider_url")}"
  config="${config//__MODEL_NAME__/$(yaml_quote "$model_name")}"
  config="${config//__MCP_SERVERS_BLOCK__/$(render_mcp_block)}"
  printf '%s\n' "$config" > "$HERMES_DIR/config.yaml"

  {
    printf 'TELEGRAM_BOT_TOKEN=%s\n' "$(dotenv_quote "$telegram_token")"
    printf 'TELEGRAM_ALLOWED_USERS=%s\n' "$telegram_ids"
    printf 'NINEROUTER_API_KEY=%s\n' "$(dotenv_quote "$hermes_backend_key")"
    if [[ "$install_nine" == true ]]; then
      printf 'NINEROUTER_URL=%s\n' "$(dotenv_quote "http://nine-router:20128")"
      printf 'NINEROUTER_KEY=%s\n' "$(dotenv_quote "$provider_key")"
    fi
    [[ -n "$telegram_home" ]] && printf 'TELEGRAM_HOME_CHANNEL=%s\n' "$telegram_home"
    printf 'API_SERVER_ENABLED=%s\n' "$api_enabled"
    if [[ "$api_enabled" == true ]]; then
      printf 'API_SERVER_HOST=0.0.0.0\n'
      printf 'API_SERVER_KEY=%s\n' "$api_key"
      printf 'API_SERVER_CORS_ORIGINS=[]\n'
    fi
    [[ -n "$n8n_trigger_mcp_token" ]] && printf 'N8N_TRIGGER_MCP_TOKEN=%s\n' "$n8n_trigger_mcp_token"
    [[ -n "$n8n_instance_mcp_token" ]] && printf 'N8N_INSTANCE_MCP_TOKEN=%s\n' "$n8n_instance_mcp_token"
    printf 'N8N_TRIGGER_MCP_URL=%s\n' "$(dotenv_quote "http://n8n:5678/mcp/hermes")"
    printf 'N8N_INSTANCE_MCP_URL=%s\n' "$(dotenv_quote "http://n8n:5678/mcp-server/http")"
  } > "$HERMES_DIR/.env"
  chmod 600 "$HERMES_DIR/.env"
  chmod 640 "$HERMES_DIR/config.yaml"
  chown "$hermes_uid:$hermes_gid" "$HERMES_DIR/.env" "$HERMES_DIR/config.yaml"
fi

# Enabling or disabling n8n on a later run must not force a full Hermes
# reconfiguration, so patch the sentinel-delimited block in place instead.
if [[ "$configure_hermes" != true && -f "$HERMES_DIR/config.yaml" \
  && ( "$configure_n8n" == true \
    || "$n8n_was_enabled" != "$install_n8n" \
    || "$n8n_mcp_was_enabled" != "$install_n8n_mcp" \
    || "$n8n_mcp_previous_mode" != "$n8n_mcp_mode" ) ]]; then
  tmp_config="$(mktemp "$HERMES_DIR/config.yaml.tmp.XXXXXX")"
  top_open_count="$(grep -c '^# >>> hermes-stack n8n mcp (managed) >>>$' "$HERMES_DIR/config.yaml" || true)"
  top_close_count="$(grep -c '^# <<< hermes-stack n8n mcp (managed) <<<$' "$HERMES_DIR/config.yaml" || true)"
  entry_open_count="$(grep -c '^  # >>> hermes-stack n8n mcp (managed) >>>$' "$HERMES_DIR/config.yaml" || true)"
  entry_close_count="$(grep -c '^  # <<< hermes-stack n8n mcp (managed) <<<$' "$HERMES_DIR/config.yaml" || true)"
  if (( top_open_count != top_close_count || entry_open_count != entry_close_count \
    || top_open_count > 1 || entry_open_count > 1 \
    || (top_open_count > 0 && entry_open_count > 0) )); then
    rm -f "$tmp_config"
    die "Hermes config has incomplete or duplicate managed n8n MCP markers; restore its backup before reconfiguring."
  fi
  if (( top_open_count == 1 )); then
    awk -v block="$(render_mcp_block)" '
      BEGIN { skipping = 0 }
      $0 == "# >>> hermes-stack n8n mcp (managed) >>>" { skipping = 1; next }
      $0 == "# <<< hermes-stack n8n mcp (managed) <<<" {
        skipping = 0
        if (block != "") print block
        next
      }
      skipping == 0 { print }
    ' "$HERMES_DIR/config.yaml" > "$tmp_config"
  elif (( entry_open_count == 1 )); then
    # An existing top-level mcp_servers map belongs to the user. Replace only
    # the installer-owned n8n entry inside it, preserving every other server.
    awk -v entry="$(render_mcp_entry)" '
      BEGIN { skipping = 0 }
      $0 == "  # >>> hermes-stack n8n mcp (managed) >>>" { skipping = 1; next }
      $0 == "  # <<< hermes-stack n8n mcp (managed) <<<" {
        skipping = 0
        if (entry != "") print entry
        next
      }
      skipping == 0 { print }
    ' "$HERMES_DIR/config.yaml" > "$tmp_config"
  elif [[ "$install_n8n_mcp" == true ]] && grep -q '^mcp_servers:[[:space:]]*$' "$HERMES_DIR/config.yaml"; then
    awk -v entry="$(render_mcp_entry)" '
      { print }
      !inserted && /^mcp_servers:[[:space:]]*$/ { print entry; inserted = 1 }
    ' "$HERMES_DIR/config.yaml" > "$tmp_config"
  else
    cp "$HERMES_DIR/config.yaml" "$tmp_config"
    if [[ "$install_n8n_mcp" == true ]]; then
      printf '\n%s\n' "$(render_mcp_block)" >> "$tmp_config"
    fi
  fi
  chmod 640 "$tmp_config"
  chown "$hermes_uid:$hermes_gid" "$tmp_config"
  mv "$tmp_config" "$HERMES_DIR/config.yaml"

  if [[ -f "$HERMES_DIR/.env" ]]; then
    tmp_hermes_env="$(mktemp "$HERMES_DIR/.env.tmp.XXXXXX")"
    grep -v -E '^N8N_(MCP_|TRIGGER_MCP_|INSTANCE_MCP_)' "$HERMES_DIR/.env" > "$tmp_hermes_env" || true
    {
      printf 'N8N_TRIGGER_MCP_URL=%s\n' "$(dotenv_quote "http://n8n:5678/mcp/hermes")"
      printf 'N8N_INSTANCE_MCP_URL=%s\n' "$(dotenv_quote "http://n8n:5678/mcp-server/http")"
      [[ -n "$n8n_trigger_mcp_token" ]] && printf 'N8N_TRIGGER_MCP_TOKEN=%s\n' "$n8n_trigger_mcp_token"
      [[ -n "$n8n_instance_mcp_token" ]] && printf 'N8N_INSTANCE_MCP_TOKEN=%s\n' "$n8n_instance_mcp_token"
    } >> "$tmp_hermes_env"
    chmod 600 "$tmp_hermes_env"
    chown "$hermes_uid:$hermes_gid" "$tmp_hermes_env"
    mv "$tmp_hermes_env" "$HERMES_DIR/.env"
  fi
fi

if [[ "$configure_smart_router" == true && "$configure_hermes" != true \
  && -f "$HERMES_DIR/config.yaml" ]]; then
  # Update only the active provider's base_url and the model default so Telegram
  # secrets and any additional custom providers stay untouched.
  tmp_config="$(mktemp "$HERMES_DIR/config.yaml.tmp.XXXXXX")"
  awk -v model="$model_name" -v url="$provider_url" -v provider="$provider_name" '
    BEGIN { in_model = 0; in_providers = 0; active = 0 }
    /^[^[:space:]#]/ {
      in_model = ($0 ~ /^model:/)
      in_providers = ($0 ~ /^custom_providers:/)
      active = 0
    }
    in_model && /^  default:/ { print "  default: '\''" model "'\''"; next }
    in_providers && /^  - name:/ {
      line = $0
      gsub(/^  - name:[[:space:]]*/, "", line)
      gsub(/^'\''|'\''$|^"|"$/, "", line)
      active = (line == provider)
    }
    in_providers && active && /^    base_url:/ {
      print "    base_url: '\''" url "'\''"
      next
    }
    { print }
  ' "$HERMES_DIR/config.yaml" > "$tmp_config"
  chmod 640 "$tmp_config"
  chown "$hermes_uid:$hermes_gid" "$tmp_config"
  mv "$tmp_config" "$HERMES_DIR/config.yaml"
fi

# Normalize ownership even when Hermes itself was not reconfigured. This repairs
# files previously rewritten by a root-run installer and keeps the uid/gid used
# by the gateway process aligned with its bind-mounted configuration.
if [[ "$install_hermes" == true ]]; then
  chown "$hermes_uid:$hermes_gid" "$HERMES_DIR"
  ensure_hermes_policy_config "$HERMES_DIR/config.yaml"
  if [[ -f "$HERMES_DIR/config.yaml" ]]; then
    chmod 640 "$HERMES_DIR/config.yaml"
    chown "$hermes_uid:$hermes_gid" "$HERMES_DIR/config.yaml"
  fi
  if [[ -f "$HERMES_DIR/.env" ]]; then
    chmod 600 "$HERMES_DIR/.env"
    chown "$hermes_uid:$hermes_gid" "$HERMES_DIR/.env"
  fi
  install -d -m 0750 -o "$hermes_uid" -g "$hermes_gid" \
    "$HERMES_DIR/lazy-packages" "$HERMES_DIR/npm-packages"
fi

if [[ "$configure_caddy" == true && "$install_caddy" == true ]]; then
  {
    if [[ -n "$caddy_email" ]]; then
      printf '{\n\temail %s\n}\n\n' "$caddy_email"
    fi
    if [[ -n "$caddy_nine_domain" ]]; then
      printf '%s {\n\tencode zstd gzip\n\treverse_proxy nine-router:20128\n}\n\n' "$caddy_nine_domain"
    fi
    if [[ -n "$caddy_webui_domain" ]]; then
      printf '%s {\n\tencode zstd gzip\n\treverse_proxy open-webui:8080\n}\n\n' "$caddy_webui_domain"
    fi
    if [[ -n "$caddy_hermes_dashboard_domain" ]]; then
      printf '%s {\n\tencode zstd gzip\n\treverse_proxy hermes:9119\n}\n\n' "$caddy_hermes_dashboard_domain"
    fi
    if [[ -n "$caddy_hermes_api_domain" ]]; then
      printf '%s {\n\tencode zstd gzip\n\treverse_proxy hermes:8642\n}\n\n' "$caddy_hermes_api_domain"
    fi
    if [[ -n "$caddy_n8n_domain" ]]; then
      # MCP needs unbuffered streaming, so /mcp* is proxied without compression.
      printf '%s {\n' "$caddy_n8n_domain"
      printf '\t@mcp path /mcp*\n'
      printf '\treverse_proxy @mcp n8n:5678 {\n\t\tflush_interval -1\n\t}\n'
      printf '\tencode zstd gzip\n'
      printf '\treverse_proxy n8n:5678\n'
      printf '}\n\n'
    fi
  } > "$CADDY_DIR/Caddyfile"
  sed -i '${/^$/d;}' "$CADDY_DIR/Caddyfile"
  chmod 644 "$CADDY_DIR/Caddyfile"
fi

ok "Configuration generated."

# Provision the Content Manager workspace. The repository's content/config/ is
# the canonical source of truth; a working copy is seeded once so operator edits
# survive later install runs. The directory is gitignored at runtime.
if [[ "$DRY_RUN" != true ]] && [[ -d "$ROOT_DIR/content/config" ]]; then
  mkdir -p "$ROOT_DIR/data/content-manager/config"
  seeded_content_config=false
  for policy_file in editorial-policy.yaml categories.yaml; do
    if [[ -f "$ROOT_DIR/content/config/$policy_file" \
      && ! -f "$ROOT_DIR/data/content-manager/config/$policy_file" ]]; then
      cp "$ROOT_DIR/content/config/$policy_file" "$ROOT_DIR/data/content-manager/config/$policy_file"
      seeded_content_config=true
    fi
  done
  if [[ "$seeded_content_config" == true ]]; then
    info "Content Manager: seeded policy working copy under data/content-manager/config"
  fi
fi

if [[ "$DRY_RUN" == true ]]; then
  ok "Dry run complete. Docker was not changed."
  exit 0
fi

if [[ "$NO_START" == true ]]; then
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    docker compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" config --quiet \
      && ok "Docker Compose configuration is valid." \
      || warn "Docker Compose validation failed; review the generated configuration before starting."
  else
    warn "Docker is not available; skipped Compose validation because --no-start was requested."
  fi
  if [[ "$install_nine" == true && ( "$install_hermes" == true || "$install_webui" == true ) ]]; then
    warn "Automatic 9router key/model provisioning requires a normal installer run when services are started."
  fi
  ok "Configuration complete. Start later with ./manage.sh start"
  exit 0
fi

detect_docker
"${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" config --quiet
ok "Docker Compose configuration is valid."

info "Pulling selected container images..."
"${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" pull

openwebui_key_status=""
hermes_key_status=""
opencode_combo_status="not-requested"
ai_combo_status="not-requested"
opencode_free_model_count="0"
if [[ "$install_nine" == true && ( "$install_hermes" == true || "$install_webui" == true ) ]]; then
  openwebui_db_preexisting=false
  [[ -f "$OPENWEBUI_DIR/webui.db" ]] && openwebui_db_preexisting=true

  info "Starting 9router to provision service access..."
  "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" up -d --no-deps nine-router
  # 9router answers /api/health before it has created and migrated its SQLite
  # database, so a health probe alone lets the installer race ahead and fail in
  # the provisioning script. Also require the tables that script writes to.
  nine_ready=false
  for _ in {1..90}; do
    if "${DOCKER[@]}" exec nine-router node -e \
      'const Database=require("node:module").createRequire("/app/package.json")("better-sqlite3");
       const db=new Database("/app/data/db/data.sqlite",{readonly:true,fileMustExist:true});
       try{db.prepare("SELECT 1 FROM apiKeys LIMIT 1").get();db.prepare("SELECT 1 FROM combos LIMIT 1").get();}finally{db.close();}
       fetch("http://127.0.0.1:20128/api/health").then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))' \
      >/dev/null 2>&1; then
      nine_ready=true
      break
    fi
    sleep 2
  done
  [[ "$nine_ready" == true ]] || die "9router did not become ready for service provisioning."

  info "Generating/reusing dedicated 9router keys and configuring model combos..."
  bootstrap_output="$("${DOCKER[@]}" exec -i \
    -e PROVISION_HERMES="$install_hermes" \
    -e PROVISION_OPENWEBUI="$install_webui" \
    -e PROVISION_SMART_ROUTER="$install_smart_router" \
    -e SMART_ROUTER_FAST_MODEL="$smart_router_fast_model" \
    -e SMART_ROUTER_STANDARD_MODEL="$smart_router_standard_model" \
    -e SMART_ROUTER_STRONG_MODEL="$smart_router_strong_model" \
    -e HERMES_MODEL_NAME="$model_name" \
    nine-router node --input-type=module < "$ROOT_DIR/scripts/bootstrap-openwebui.mjs")"
  openwebui_api_key="$(sed -n 's/^OPENWEBUI_API_KEY=//p' <<< "$bootstrap_output" | tail -n1)"
  openwebui_key_status="$(sed -n 's/^OPENWEBUI_KEY_STATUS=//p' <<< "$bootstrap_output" | tail -n1)"
  hermes_api_key="$(sed -n 's/^HERMES_API_KEY=//p' <<< "$bootstrap_output" | tail -n1)"
  hermes_key_status="$(sed -n 's/^HERMES_KEY_STATUS=//p' <<< "$bootstrap_output" | tail -n1)"
  opencode_combo_status="$(sed -n 's/^OPENCODE_COMBO_STATUS=//p' <<< "$bootstrap_output" | tail -n1)"
  ai_combo_status="$(sed -n 's/^AI_COMBO_STATUS=//p' <<< "$bootstrap_output" | tail -n1)"
  smart_router_combo_status="$(sed -n 's/^SMART_ROUTER_COMBO_STATUS=//p' <<< "$bootstrap_output" | tail -n1)"
  opencode_free_model_count="$(sed -n 's/^OPENCODE_FREE_MODEL_COUNT=//p' <<< "$bootstrap_output" | tail -n1)"

  if [[ "$install_smart_router" == true ]]; then
    # One backend key is used only by Smart Router upstream. Local clients use the router client key.
    [[ -n "$hermes_api_key" ]] || die "9router did not return an upstream API key for Smart Router."
    replace_env_value "$ENV_FILE" SMART_ROUTER_UPSTREAM_API_KEY "$(dotenv_quote "$hermes_api_key")"
    [[ "$install_webui" == true ]] && replace_env_value "$ENV_FILE" OPENWEBUI_OPENAI_API_KEY "$(dotenv_quote "$client_key")"
    if [[ "$install_hermes" == true ]]; then
      replace_env_value "$HERMES_DIR/.env" NINEROUTER_API_KEY "$(dotenv_quote "$client_key")"
      replace_env_value "$HERMES_DIR/.env" NINEROUTER_URL "$(dotenv_quote "http://smart-router:8080")"
      replace_env_value "$HERMES_DIR/.env" NINEROUTER_KEY "$(dotenv_quote "$client_key")"
    fi
  else
    if [[ "$install_webui" == true ]]; then
      [[ -n "$openwebui_api_key" ]] || die "9router did not return the generated Open WebUI API key."
      replace_env_value "$ENV_FILE" OPENWEBUI_OPENAI_API_KEY "$(dotenv_quote "$openwebui_api_key")"
    fi
    if [[ "$install_hermes" == true ]]; then
      [[ -n "$hermes_api_key" ]] || die "9router did not return the generated Hermes API key."
      replace_env_value "$HERMES_DIR/.env" NINEROUTER_API_KEY "$(dotenv_quote "$hermes_api_key")"
      replace_env_value "$HERMES_DIR/.env" NINEROUTER_URL "$(dotenv_quote "http://nine-router:20128")"
      replace_env_value "$HERMES_DIR/.env" NINEROUTER_KEY "$(dotenv_quote "$hermes_api_key")"
    fi
  fi
fi

if [[ "$configure_caddy" == true && "$install_caddy" == true ]]; then
  info "Validating generated Caddy configuration..."
  "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile
fi
if [[ "$smart_router_was_enabled" == true && "$install_smart_router" != true ]]; then
  info "Stopping the disabled Hermes Smart Router..."
  COMPOSE_PROFILES=smart-router "${DOCKER[@]}" compose \
    -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" \
    rm -sf smart-router smart-router-init
fi

if [[ "$n8n_was_enabled" == true && "$install_n8n" != true ]]; then
  info "Stopping disabled n8n (data/n8n is preserved)..."
  COMPOSE_PROFILES=n8n "${DOCKER[@]}" compose \
    -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" \
    rm -sf n8n n8n-init
fi

info "Starting selected services..."
"${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" up -d --build --remove-orphans

enable_hermes_telegram_policy_toolsets

if [[ "$install_smart_router" == true ]]; then
  info "Waiting for the Hermes Smart Router to become ready..."
  smart_router_ready=false
  for _ in {1..60}; do
    if "${DOCKER[@]}" exec hermes-smart-router python -c \
      'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=5)' \
      >/dev/null 2>&1; then
      smart_router_ready=true
      break
    fi
    sleep 2
  done
  [[ "$smart_router_ready" == true ]] || die "Hermes Smart Router did not become ready."
fi

if [[ "$install_n8n" == true ]]; then
  info "Waiting for n8n to become ready..."
  n8n_ready=false
  for _ in {1..60}; do
    if "${DOCKER[@]}" exec hermes-n8n node -e \
      "fetch('http://127.0.0.1:5678/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))" \
      >/dev/null 2>&1; then
      n8n_ready=true
      break
    fi
    sleep 2
  done
  [[ "$n8n_ready" == true ]] || warn "n8n did not report healthy yet; check ./manage.sh logs n8n."
fi


# The owner/API/MCP credentials are user-bound credentials n8n creates only
# after its first owner/admin account exists. Start n8n first, then keep this
# install session open while the operator creates them in the browser and pastes
# them into hidden validation prompts.
if [[ "$install_n8n" == true && "$install_hermes" == true && "${n8n_ready:-false}" == true \
  && "${n8n_mcp_mode:-off}" != off && ( "$configure_n8n" == true || "$configure_hermes" == true ) ]]; then
  printf '\nn8n post-start provisioning\n'
  printf '%s\n' '---------------------------'
  printf 'n8n is running at: %s\n' "$n8n_public_url"
  printf '%s\n' 'The owner API key and Instance MCP token are created by n8n only after the owner/admin account exists.'
  if confirm "Finish n8n owner/API/MCP provisioning now in this wizard?" y; then
    printf '%s\n' '1. Open the n8n URL above and create/confirm the owner account.'
    printf '%s\n' '2. Create an owner API key in n8n for stack-managed workflow reconciliation.'
    read -r -p 'Press Enter when the owner account and API key are ready... ' _
    if "$ROOT_DIR/manage.sh" set-n8n-api-key; then
      n8n_api_ready=true
    else
      n8n_api_ready=false
      warn "The n8n owner API key was not stored. Retry later with ./manage.sh n8n-menu."
    fi

    if [[ "$n8n_mcp_mode" == instance ]]; then
      printf '%s\n' '3. In n8n: Settings -> Instance-level MCP -> enable MCP access.'
      printf '%s\n' '4. Open Connection details -> Access Token, generate/copy the personal MCP token.'
      read -r -p 'Press Enter when the Instance MCP token is ready... ' _
      if [[ "$n8n_api_ready" == true ]]; then
        if "$ROOT_DIR/manage.sh" set-n8n-instance-mcp-token; then
          ok "n8n Instance MCP token stored; managed n8n objects reconciled and verified."
        else
          warn "Instance MCP setup is incomplete. Retry with ./manage.sh n8n-menu."
        fi
      else
        warn "Skipping Instance token activation until the owner API key is stored, avoiding a half-reconciled managed n8n state."
      fi
    elif [[ "$n8n_mcp_mode" == trigger && "$n8n_api_ready" == true ]]; then
      if "$ROOT_DIR/manage.sh" bootstrap-n8n; then
        ok "Managed n8n hosted chat and Trigger MCP workflow reconciled and verified."
      else
        warn "Managed n8n provisioning is incomplete. Retry with ./manage.sh n8n-menu."
      fi
    fi
  else
    warn "n8n post-start provisioning skipped. Resume later with ./manage.sh n8n-menu."
  fi
fi

if [[ "$install_nine" == true && "$install_webui" == true ]]; then
  if [[ "$openwebui_db_preexisting" == true || "$configure_smart_router" == true ]]; then
    info "Synchronizing the Open WebUI backend connection without deleting its data..."
    webui_db_ready=false
    for _ in {1..60}; do
      if "${DOCKER[@]}" exec open-webui python -c \
        'import sqlite3; db=sqlite3.connect("/app/backend/data/webui.db"); db.execute("select 1 from config limit 1").fetchone(); db.close()' \
        >/dev/null 2>&1; then
        webui_db_ready=true
        break
      fi
      sleep 2
    done
    [[ "$webui_db_ready" == true ]] || die "Open WebUI database did not become ready for connection migration."
    "${DOCKER[@]}" exec -i open-webui python < "$ROOT_DIR/scripts/sync-openwebui-config.py"
    "${DOCKER[@]}" compose -f "$ROOT_DIR/docker-compose.yml" --env-file "$ENV_FILE" restart open-webui
  fi

  if [[ "$opencode_combo_status" != unavailable ]]; then
    info "Verifying Open WebUI can authenticate to 9router and discover OpenCode-Free..."
    "${DOCKER[@]}" exec -i open-webui python < "$ROOT_DIR/scripts/verify-openwebui-backend.py"
  else
    warn "The 9router key is configured, but OpenCode-Free could not be refreshed from the upstream catalog."
  fi
fi

printf '\n'
ok "Installation complete."
[[ "$install_nine" == true ]] && printf '9router dashboard: %s\n' "$nine_public_url"
if [[ -n "$hermes_key_status" ]]; then
  printf 'Hermes 9router key: %s (stored securely; not printed)\n' "$hermes_key_status"
  if [[ "$model_name" == ai || "$install_smart_router" == true ]]; then
    printf 'Hermes ai combo: %s (%s free upstream models)\n' "$ai_combo_status" "$opencode_free_model_count"
  fi
fi
if [[ "$install_smart_router" == true ]]; then
  printf 'Hermes Smart Router: enabled (%s mode, %s policy)\n' "$smart_router_mode" "$smart_router_policy"
  printf 'Smart Router API: http://%s:%s/v1\n' "$(service_url_host "$smart_router_bind")" "$smart_router_port"
  [[ "$smart_router_dashboard_enabled" == true ]] && printf 'Smart Router dashboard: http://%s:%s/dashboard\n' "$(service_url_host "$smart_router_bind")" "$smart_router_port"
  [[ "$smart_router_control_plane_enabled" == true ]] && printf 'Smart Router control plane: http://%s:%s/control/\n' "$(service_url_host "$smart_router_bind")" "$smart_router_port"
  [[ "$smart_router_control_plane_enabled" == true ]] && printf 'Control-plane access: ./manage.sh router-access\n'
  printf 'Smart Router tier combos: %s (initially cloned from ai)\n' "$smart_router_combo_status"
  warn "Customize combo-fast, combo-standard, and combo-strong in 9router before enabling route mode."
fi
if [[ "$install_hermes" == true && -n "$telegram_token" ]]; then
  printf '%s\n' 'Telegram: open your bot and send /start'
fi
if [[ "$hermes_dashboard" == 1 ]]; then
  printf 'Hermes dashboard: http://%s:%s\n' "$(service_url_host "$hermes_bind")" "$hermes_dashboard_port"
  printf 'Hermes dashboard username: %s\n' "$hermes_dashboard_username"
  if [[ "$hermes_dashboard_credentials_created" == true ]]; then
    printf 'Hermes dashboard password (save now): %s\n' "$hermes_dashboard_password"
  else
    printf '%s\n' 'Hermes dashboard password: preserved (not printed again)'
  fi
  printf '%s\n' 'Later access: ./manage.sh dashboard-access (add --show-password on a trusted terminal)'
  if [[ "$hermes_bind" == 127.0.0.1 ]]; then
    printf 'Remote access tunnel: ssh -L %s:127.0.0.1:%s USER@SERVER\n' \
      "$hermes_dashboard_port" "$hermes_dashboard_port"
  else
    warn "Basic Auth protects the dashboard, but plain HTTP does not encrypt credentials. Prefer HTTPS through Caddy or restrict access to a trusted LAN."
  fi
fi
[[ "$configure_hermes" == true && "$api_enabled" == true ]] && printf 'Hermes API key (save now): %s\n' "$api_key"
if [[ "$install_webui" == true ]]; then
  printf 'Open WebUI: %s\n' "$openwebui_url"
  if [[ -n "$openwebui_key_status" ]]; then
    printf 'Open WebUI 9router key: %s (stored securely; not printed)\n' "$openwebui_key_status"
    printf 'OpenCode-Free model: %s (%s free upstream models)\n' "$opencode_combo_status" "$opencode_free_model_count"
  fi
  [[ "$openwebui_signup" == true ]] && printf '%s\n' 'Open WebUI: the first registered account becomes administrator; disable signup afterward.'
fi
if [[ "$install_n8n" == true ]]; then
  printf 'n8n: %s\n' "$n8n_public_url"
  printf '%s\n' 'n8n: the first visitor claims the owner account; create it now.'
  printf '%s\n' 'n8n encryption key: stored in .env as N8N_ENCRYPTION_KEY (back it up with data/n8n).'
  case "$n8n_mcp_mode" in
    instance)
      printf '%s\n' 'Hermes n8n MCP mode: Instance-level (http://n8n:5678/mcp-server/http)'
      if [[ -n "$(sed -n 's/^N8N_INSTANCE_MCP_TOKEN=//p' "$HERMES_DIR/.env" 2>/dev/null | tail -n1)" ]]; then
        printf '%s\n' 'Instance MCP token: configured (secret not printed)'
      else
        printf '%s\n' 'Instance MCP token: pending; use ./manage.sh n8n-menu to finish setup.'
      fi
      ;;
    trigger)
      printf '%s\n' 'Hermes n8n MCP mode: MCP Server Trigger (http://n8n:5678/mcp/hermes)'
      ;;
  esac
  printf '%s\n' 'n8n provisioning manager: ./manage.sh n8n-menu'
fi
if [[ "$install_caddy" == true ]]; then
  [[ -n "$caddy_nine_domain" ]] && printf '9router HTTPS: https://%s\n' "$caddy_nine_domain"
  [[ -n "$caddy_webui_domain" ]] && printf 'Open WebUI HTTPS: https://%s\n' "$caddy_webui_domain"
  [[ -n "$caddy_hermes_dashboard_domain" ]] && printf 'Hermes dashboard HTTPS: https://%s\n' "$caddy_hermes_dashboard_domain"
  [[ -n "$caddy_hermes_api_domain" ]] && printf 'Hermes API HTTPS: https://%s\n' "$caddy_hermes_api_domain"
  [[ -n "$caddy_n8n_domain" ]] && printf 'n8n HTTPS: https://%s\n' "$caddy_n8n_domain"
  printf '%s\n' 'Caddy requires public DNS plus inbound TCP 80/443 and UDP 443.'
fi
if [[ -d "$ROOT_DIR/content/config" ]]; then
  printf '%s\n' 'Content Manager: editorial policy working copy -> data/content-manager/config'
  printf '%s\n' 'Content Manager: pipeline source, policy, and operations notes -> content/README.md'
fi
printf '%s\n' 'Status: ./manage.sh status'
printf '%s\n' 'Logs:   ./manage.sh logs'
