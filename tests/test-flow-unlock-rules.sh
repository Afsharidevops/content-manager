#!/usr/bin/env bash
# Guards the Locallab Flow Unlock extension files for both browsers, including
# the multi-account
# /u/<n>/unsupported-country URL shape Chrome uses with several Google logins
# and the app-config response patch that keeps the dashboard for an account the
# Flow backend marks as an unsupported country.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
EXT="$ROOT_DIR/extensions/locallab-flow-unlock"
FIREFOX_EXT="$ROOT_DIR/extensions/locallab-flow-unlock-firefox"

python3 - "$EXT" "$FIREFOX_EXT" <<'PY'
import json
import pathlib
import sys

ext = pathlib.Path(sys.argv[1])
firefox_ext = pathlib.Path(sys.argv[2])
manifest = json.loads((ext / "manifest.json").read_text())
rules = json.loads((ext / "rules.json").read_text())

assert manifest["manifest_version"] == 3, manifest
scripts = manifest.get("content_scripts") or []
assert len(scripts) == 2, scripts
isolated, page_world = scripts
assert "https://flow.google.com/*" in isolated["matches"], isolated
assert "https://flow.google.com/*" in page_world["matches"], page_world
assert isolated["js"] == ["freeze.js"], isolated
assert page_world["js"] == ["unlock.js"], page_world
assert page_world["world"] == "MAIN", "the response patch must run in the page world"
assert all(script["run_at"] == "document_start" for script in scripts), scripts

filters = [rule["condition"]["regexFilter"] for rule in rules]
assert all("unsupported-country" in value for value in filters), filters
flow_rule = next(value for value in filters if value.startswith("^https://flow"))
assert "/unsupported-country" in flow_rule, flow_rule
assert "(/u/[0-9]+)?" in flow_rule, "path filters must cover multi-account /u/<n>/ URLs"
assert all(value.endswith("unsupported-country([?#]|$)") for value in filters), filters
for rule in rules:
    assert rule["action"]["type"] == "block", rule
    assert "main_frame" in rule["condition"]["resourceTypes"], rule
    assert "urlFilter" not in rule["condition"], "path filters replaced the wildcard filters"

script = (ext / "freeze.js").read_text()
assert "unsupported-country" in script
assert "MAX_BOUNCES" in script, "client-side route guard missing"
assert "https://flow.google.com/" in script
assert "stopFreeze" in script, "recovery navigation must not be cancelled by the freeze"
assert "accountRoot" in script, "recovery must keep the /u/<n>/ account prefix"
assert "u/${match[1]}" in script
assert "data-locallab-flow-config" in script, "the freeze must stand down once the config is patched"
assert "data-locallab-flow-freeze" in script, "the freeze script must leave its own marker"

patch = (ext / "unlock.js").read_text()
assert "cPZSdc" in patch, "the app-config rpc must be patched"
assert "KV2T2d" in patch, "the tool-availability rpc must be patched"
assert "data-locallab-flow-config" in patch, "the patch must mark the document"
assert "batchexecute" in patch
assert "field 31" in patch and "field 1 " in patch, "the patched fields must stay documented"

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

# --- Firefox package -------------------------------------------------------
#
# Firefox cannot run a Chrome service worker and does not load the packaged
# Chrome rule set: its manifest declares an event page and the block policy
# lives in the event page itself, so both shapes are verified here.
firefox_manifest = json.loads((firefox_ext / "manifest.json").read_text())
rules_path = firefox_ext / "rules.json"
assert firefox_manifest["manifest_version"] == 3, firefox_manifest
assert firefox_manifest["version"] == manifest["version"], "browser packages must share a version"
assert firefox_manifest["name"] == manifest["name"], firefox_manifest
assert "declarative_net_request" not in firefox_manifest, "Firefox uses the webRequest policy"
assert not rules_path.exists(), "the Chrome rule set has no place in the Firefox package"
firefox_scripts = firefox_manifest["content_scripts"]
assert firefox_scripts == scripts, "content scripts must match across browsers"
gecko = firefox_manifest["browser_specific_settings"]["gecko"]
assert gecko["id"], gecko
assert int(str(gecko["strict_min_version"]).split(".")[0]) >= 128, (
    "page-world content scripts need Firefox 128 or newer"
)
assert "none" in gecko["data_collection_permissions"]["required"], gecko
permissions = firefox_manifest["permissions"]
assert "webRequestBlocking" in permissions, permissions
assert "webRequest" in permissions, permissions
assert "webNavigation" in permissions, permissions
assert firefox_manifest["background"].get("scripts") == ["background.js"], (
    "Firefox MV3 needs an event page, not a service worker"
)
assert "service_worker" not in firefox_manifest["background"], firefox_manifest
hosts = firefox_manifest["host_permissions"]
assert "https://*.google.com/*" in hosts, hosts
assert "https://labs.google/*" in hosts, hosts

firefox_worker = (firefox_ext / "background.js").read_text()
assert "webRequest.onBeforeRequest" in firefox_worker, firefox_worker[:200]
assert "redirectUrl" in firefox_worker, "the block route must be redirected on Firefox"
assert "cancel: true" in firefox_worker, "non-navigation requests must be cancelled"
assert "accountRoot" in firefox_worker, "recovery must keep the /u/<n>/ account prefix"
assert "MAX_ATTEMPTS" in firefox_worker, "redirects need a loop budget"
assert "webNavigation.onErrorOccurred" in firefox_worker
assert "webNavigation.onCommitted" in firefox_worker
assert "NS_ERROR_ABORT" in firefox_worker, "Firefox reports cancelled requests as NS_ERROR_*"

# The response patch and the freeze guard are the same code on both browsers;
# keep the copies byte-identical so a fix cannot land in one package only.
for shared in ("freeze.js", "unlock.js", "ublock-filter.txt"):
    assert (ext / shared).read_bytes() == (firefox_ext / shared).read_bytes(), (
        f"{shared} differs between the Chrome and Firefox packages"
    )
PY

if command -v node >/dev/null 2>&1; then
  node --check "$EXT/freeze.js"
  node --check "$EXT/unlock.js"
  node --check "$FIREFOX_EXT/background.js"
fi
printf 'flow unlock rules tests passed.\n'
