#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
bash -n "$ROOT/install.sh" "$ROOT/manage.sh"
python3 -m unittest -v "$ROOT/tests/test_hermes_telegram_toolsets.py"
python3 -m unittest -v "$ROOT/tests/test_repository_layout.py"
python3 -m compileall -q "$ROOT/smart-router/src" "$ROOT/smart-router/tests"
PYTHONPATH="$ROOT/smart-router/src" pytest -q "$ROOT/smart-router/tests"
python3 - "$ROOT/docker-compose.yml" <<'PY'
import sys, yaml
p=sys.argv[1]
import tomllib
from pathlib import Path
x=yaml.safe_load(open(p,encoding='utf-8'))
s=x['services']['smart-router']
build = s.get('build')

if isinstance(build, dict):
    build_context = build.get('context')
elif isinstance(build, str):
    build_context = build
else:
    build_context = None

image = str(s.get('image', ''))

repo_root = Path(p).resolve().parent
with (repo_root / "smart-router" / "pyproject.toml").open("rb") as fh:
    expected_version = tomllib.load(fh)["project"]["version"]

fixed_release_image = (
    'afsharidevops/hermes-smart-router' in image
    and f':{expected_version}' in image
)

configurable_release_image = (
    'afsharidevops/hermes-smart-router' in image
    and 'SMART_ROUTER_IMAGE_REPOSITORY' in image
    and 'SMART_ROUTER_IMAGE_TAG' in image
)

assert (
    build_context == './smart-router'
    or fixed_release_image
    or configurable_release_image
), f"unexpected Smart Router source: build={build!r}, image={image!r}, expected_version={expected_version!r}"

assert './smart-router/policy:/policy:ro' in s['volumes'], (
    f"Smart Router policy volume missing: {s.get('volumes')!r}"
)

assert 'SMART_ROUTER_POLICY' in s['environment'], (
    f"SMART_ROUTER_POLICY missing: {s.get('environment')!r}"
)

assert 'SMART_ROUTER_OBSERVATION_FILE' in s['environment'], (
    f"SMART_ROUTER_OBSERVATION_FILE missing: {s.get('environment')!r}"
)

assert 'observations-v4.jsonl' in str(
    s['environment']['SMART_ROUTER_OBSERVATION_FILE']
), (
    "SMART_ROUTER_OBSERVATION_FILE must use observations-v4.jsonl (current observation schema)"
)
print('compose YAML/wiring: OK')
PY
# Eval CLI smoke checks.
# Functional eval behavior is covered by the Smart Router pytest suite.
PYTHONPATH="$ROOT/smart-router/src" \
python3 -m smart_router.eval.calibrate --help >/dev/null

PYTHONPATH="$ROOT/smart-router/src" \
python3 -m smart_router.eval.report --help >/dev/null

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose -f "$ROOT/docker-compose.yml" --env-file "$ROOT/.env.example" config --quiet
fi
echo "smoke: OK"
