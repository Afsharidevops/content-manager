#!/usr/bin/env bash
# Guards the Locallab Flow Unlock extension files, including the multi-account
# /u/<n>/unsupported-country URL shape Chrome uses with several Google logins.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
EXT="$ROOT_DIR/extensions/locallab-flow-unlock"

python3 - "$EXT" <<'PY'
import json
import pathlib
import sys

ext = pathlib.Path(sys.argv[1])
manifest = json.loads((ext / "manifest.json").read_text())
rules = json.loads((ext / "rules.json").read_text())

assert manifest["manifest_version"] == 3, manifest
scripts = manifest.get("content_scripts") or []
assert scripts, "content script missing"
matches = scripts[0]["matches"]
assert "https://flow.google.com/*" in matches, matches

filters = [rule["condition"]["urlFilter"] for rule in rules]
assert all("unsupported-country" in value for value in filters), filters
assert any(
    value.startswith("||flow.google.com/") and "*" in value for value in filters
), "a wildcard filter is required for multi-account /u/<n>/ URLs"
for rule in rules:
    assert rule["action"]["type"] == "block", rule
    assert "main_frame" in rule["condition"]["resourceTypes"], rule

script = (ext / "freeze.js").read_text()
assert "unsupported-country" in script
assert "MAX_BOUNCES" in script, "client-side route guard missing"
assert "https://flow.google.com/" in script
assert "stopFreeze" in script, "recovery navigation must not be cancelled by the freeze"
assert "accountRoot" in script, "recovery must keep the /u/<n>/ account prefix"
assert "u/${match[1]}" in script

manifest_background = manifest.get("background") or {}
assert manifest_background.get("service_worker") == "background.js", manifest_background
worker = (ext / "background.js").read_text()
assert "webNavigation.onErrorOccurred" in worker
assert "ERR_BLOCKED_BY_CLIENT" in worker
assert "chrome.tabs.update" in worker
assert "MAX_RETRIES" in worker
assert "accountRoot" in worker, "recovery must keep the /u/<n>/ account prefix"

filters_file = (ext / "ublock-filter.txt").read_text()
assert "*unsupported-country" in filters_file
PY

if command -v node >/dev/null 2>&1; then
  node --check "$EXT/freeze.js"
fi
printf 'flow unlock rules tests passed.\n'
