#!/usr/bin/env bash
set -euo pipefail
COMPOSE_FILE="${1:-smart-router/compose-ha-v0.5.9.example.yml}"
PORT="${SMART_ROUTER_HA_PORT:-8787}"
: "${SMART_ROUTER_PG_PASSWORD:?set SMART_ROUTER_PG_PASSWORD}"
: "${SMART_ROUTER_REDIS_PASSWORD:?set SMART_ROUTER_REDIS_PASSWORD}"

compose=(docker compose -f "$COMPOSE_FILE")
echo "== Hermes v0.5.9 HA smoke test =="
"${compose[@]}" ps

echo "-- load balancer health"
curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null
echo "OK"

echo "-- PostgreSQL + pgvector"
"${compose[@]}" exec -T postgres psql \
  -U "${SMART_ROUTER_PG_USER:-hermes_router}" \
  -d "${SMART_ROUTER_PG_DB:-hermes_router}" \
  -Atc "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname='vector';"

echo "-- Redis"
"${compose[@]}" exec -T redis sh -lc 'redis-cli -a "$SMART_ROUTER_REDIS_PASSWORD" ping' | grep -q PONG
echo "OK"

echo "-- two independent router replicas"
for svc in router-a router-b; do
  "${compose[@]}" exec -T "$svc" python - <<'PY'
import urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8080/ready", timeout=5).read().decode())
PY
done

echo "PASS: PostgreSQL/pgvector, Redis, load balancer and both router replicas are ready."
