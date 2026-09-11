#!/usr/bin/env bash
# Package the Content Manager Helm chart and push it to a registry.
#
# OCI registry (GitHub Container Registry):
#   scripts/helm-publish.sh --registry oci://ghcr.io/afsharidevops/charts
#
# ChartMuseum or another plain Helm repository endpoint:
#   scripts/helm-publish.sh --chartmuseum https://charts.example.com
#
# Credentials are never stored here: log in first with `helm registry login` or
# set HELM_REGISTRY_USERNAME/HELM_REGISTRY_PASSWORD to let this script log in.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="$ROOT_DIR/deploy/helm/hermes-linux-stack"
OVERLAY="$ROOT_DIR/examples/helm/content-stack-values.yaml"

log() { printf '[helm-publish] %s\n' "$*" >&2; }
die() { printf '[helm-publish] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: scripts/helm-publish.sh [OPTIONS]

Packages deploy/helm/hermes-linux-stack after linting and rendering it, then
pushes the archive to the requested destination.

Options:
  --registry OCI_REF     OCI destination, for example oci://ghcr.io/OWNER/charts
  --chartmuseum URL      ChartMuseum (or compatible) endpoint that accepts uploads
  --package-dir DIR      Directory for the packaged .tgz (default: dist/)
  --login                Log in with HELM_REGISTRY_USERNAME/HELM_REGISTRY_PASSWORD
  --dry-run              Print the commands without running push/login
  -h, --help             Show this help

Exactly one of --registry or --chartmuseum is required unless --dry-run is used.
USAGE
}

registry=""
chartmuseum=""
package_dir="$ROOT_DIR/dist"
do_login=false
dry_run=false

while (($#)); do
  case "$1" in
    --registry) [[ $# -ge 2 ]] || die "--registry requires a value"; registry="$2"; shift 2 ;;
    --chartmuseum) [[ $# -ge 2 ]] || die "--chartmuseum requires a value"; chartmuseum="$2"; shift 2 ;;
    --package-dir) [[ $# -ge 2 ]] || die "--package-dir requires a directory"; package_dir="$2"; shift 2 ;;
    --login) do_login=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n "$registry" || -n "$chartmuseum" || "$dry_run" == true ]] \
  || die "choose --registry or --chartmuseum (see --help)"
[[ -n "$registry" && -n "$chartmuseum" ]] && die "choose one destination at a time"
command -v helm >/dev/null 2>&1 || die "helm is required"
[[ -f "$CHART_DIR/Chart.yaml" ]] || die "chart not found at $CHART_DIR"

log "linting the chart"
helm lint "$CHART_DIR" >/dev/null
if [[ -f "$OVERLAY" ]]; then
  log "linting the full-stack overlay"
  helm lint "$CHART_DIR" -f "$OVERLAY" >/dev/null
  helm template content-manager "$CHART_DIR" -f "$OVERLAY" --namespace content-manager >/dev/null
fi

mkdir -p "$package_dir"
log "packaging into $package_dir"
archive="$(helm package "$CHART_DIR" --destination "$package_dir" | awk '{print $NF}')"
[[ -f "$archive" ]] || die "packaging did not produce an archive"
log "packaged: $archive"

if [[ "$do_login" == true ]]; then
  [[ -n "$registry" ]] || die "--login requires --registry"
  host="${registry#oci://}"
  host="${host%%/*}"
  [[ -n "${HELM_REGISTRY_USERNAME:-}" && -n "${HELM_REGISTRY_PASSWORD:-}" ]] \
    || die "--login needs HELM_REGISTRY_USERNAME and HELM_REGISTRY_PASSWORD"
  if [[ "$dry_run" == true ]]; then
    log "would log in to $host as $HELM_REGISTRY_USERNAME"
  else
    printf '%s' "$HELM_REGISTRY_PASSWORD" \
      | helm registry login "$host" --username "$HELM_REGISTRY_USERNAME" --password-stdin
  fi
fi

if [[ -n "$registry" ]]; then
  if [[ "$dry_run" == true ]]; then
    log "would push: helm push $archive $registry"
  else
    log "pushing to $registry"
    helm push "$archive" "$registry"
  fi
elif [[ -n "$chartmuseum" ]]; then
  command -v curl >/dev/null 2>&1 || die "curl is required to upload to ChartMuseum"
  if [[ "$dry_run" == true ]]; then
    log "would upload: curl --data-binary @$archive ${chartmuseum%/}/api/charts"
  else
    log "uploading to ${chartmuseum%/}/api/charts"
    curl --fail --silent --show-error --data-binary "@$archive" "${chartmuseum%/}/api/charts"
    printf '\n' >&2
  fi
fi

log "done"
