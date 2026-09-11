#!/usr/bin/env bash
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$HERE/.." && pwd)"
CHART="$ROOT/deploy/helm/hermes-linux-stack"
OVERLAY="$ROOT/examples/helm/content-stack-values.yaml"
TMP="$(mktemp -d)"
trap 'rm -rf -- "$TMP"' EXIT

pass=0
fail=0
ok() { printf 'ok - %s\n' "$1"; pass=$((pass+1)); }
not_ok() { printf 'not ok - %s\n' "$1" >&2; fail=$((fail+1)); }

if ! command -v helm >/dev/null 2>&1; then
  printf 'skip - helm is not installed; chart rendering was not verified\n'
  exit 0
fi

if helm lint "$CHART" >/dev/null 2>&1; then
  ok "helm lint accepts the chart defaults"
else
  not_ok "helm lint accepts the chart defaults"
fi

if helm lint "$CHART" -f "$OVERLAY" >/dev/null 2>&1; then
  ok "helm lint accepts the content stack overlay"
else
  not_ok "helm lint accepts the content stack overlay"
fi

if helm template default "$CHART" 2>/dev/null | grep -q 'kind: Deployment'; then
  ok "the default render deploys the Smart Router"
else
  not_ok "the default render deploys the Smart Router"
fi

rendered="$(helm template content-manager "$CHART" -f "$OVERLAY" --namespace content-manager 2>/dev/null || true)"
if [[ -z "$rendered" ]]; then
  not_ok "the full stack overlay renders"
else
  ok "the full stack overlay renders"
  for resource in hermes-smart-router-content-bot hermes-smart-router-panel \
      hermes-smart-router-media-studio hermes-smart-router-media-files \
      hermes-smart-router-upstream; do
    if grep -q "name: $resource$" <<<"$rendered"; then
      ok "the overlay renders $resource"
    else
      not_ok "the overlay renders $resource"
    fi
  done
  if grep -q 'value: "http://hermes-smart-router-upstream:20128/v1"' <<<"$rendered"; then
    ok "the router points at the in-cluster upstream"
  else
    not_ok "the router points at the in-cluster upstream"
  fi
  if grep -q 'postgresql+psycopg://' <<<"$rendered" && grep -q 'redis://:' <<<"$rendered"; then
    ok "the router receives PostgreSQL and Redis URLs"
  else
    not_ok "the router receives PostgreSQL and Redis URLs"
  fi
  if grep -q 'path: /media' <<<"$rendered"; then
    ok "the ingress publishes the media file server"
  else
    not_ok "the ingress publishes the media file server"
  fi
fi

# TLS can be switched off when an external reverse proxy terminates HTTPS.
cat > "$TMP/proxy-values.yaml" <<'YAML'
ingress:
  tls: false
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "false"
YAML
proxy_render=""
if proxy_render="$(helm template content-manager "$CHART" -f "$OVERLAY" -f "$TMP/proxy-values.yaml" \
      --namespace content-manager 2>/dev/null)" \
   && grep -q 'nginx.ingress.kubernetes.io/ssl-redirect: "false"' <<<"$proxy_render" \
   && ! grep -q 'secretName: panel-tls' <<<"$proxy_render" \
   && ! grep -qE '^  tls:$' <<<"$proxy_render"; then
  ok "ingress tls can be disabled behind an external reverse proxy"
else
  not_ok "ingress tls can be disabled behind an external reverse proxy"
fi

if empty_tls="$(helm template content-manager "$CHART" -f "$OVERLAY" --set-json 'ingress.tls=[]' \
      --namespace content-manager 2>/dev/null)" \
   && ! grep -q 'secretName: panel-tls' <<<"$empty_tls"; then
  ok "an empty ingress tls list renders without a TLS block"
else
  not_ok "an empty ingress tls list renders without a TLS block"
fi

chart_app_version="$(awk -F'"' '/^appVersion:/ {print $2}' "$CHART/Chart.yaml")"
stack_version="$(tr -d '[:space:]' < "$ROOT/VERSION")"
if [[ "$stack_version" == "v$chart_app_version" || "$stack_version" == "$chart_app_version" ]]; then
  ok "Chart.yaml appVersion matches the stack VERSION file"
else
  not_ok "Chart.yaml appVersion matches the stack VERSION file ($chart_app_version != $stack_version)"
fi

if python3 - "$CHART/Chart.yaml" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
version = re.search(r"^version:\s*(\S+)", text, re.M).group(1)
assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
PY
then
  ok "Chart.yaml carries a semver chart version"
else
  not_ok "Chart.yaml carries a semver chart version"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
(( fail == 0 ))
