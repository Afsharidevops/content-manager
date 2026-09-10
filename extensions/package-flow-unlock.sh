#!/usr/bin/env bash
# Build the standalone Locallab Flow Unlock ZIP packages.
#
#   extensions/package-flow-unlock.sh [OUTPUT_DIR]
#
# Two independent packages are produced from the two source folders:
#
#   locallab-flow-unlock-chrome-<version>.zip   extensions/locallab-flow-unlock
#   locallab-flow-unlock-firefox-<version>.zip  extensions/locallab-flow-unlock-firefox
#
# The Chrome package is loaded with "Load unpacked" in chrome://extensions; the
# Firefox package is loaded with "Load Temporary Add-on" in about:debugging.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CHROME_SRC="$ROOT_DIR/extensions/locallab-flow-unlock"
FIREFOX_SRC="$ROOT_DIR/extensions/locallab-flow-unlock-firefox"
OUT_DIR="${1:-$ROOT_DIR/dist}"

for src in "$CHROME_SRC" "$FIREFOX_SRC"; do
  [[ -f "$src/manifest.json" ]] || { printf 'missing manifest: %s\n' "$src" >&2; exit 2; }
done

VERSION="$(python3 - "$FIREFOX_SRC/manifest.json" "$CHROME_SRC/manifest.json" <<'PY'
import json
import sys

versions = {json.load(open(path, encoding="utf-8"))["version"] for path in sys.argv[1:]}
if len(versions) != 1:
    raise SystemExit(f"browser packages disagree on the version: {sorted(versions)}")
print(versions.pop())
PY
)"

mkdir -p "$OUT_DIR"

python3 - "$OUT_DIR" "$VERSION" "$CHROME_SRC" "$FIREFOX_SRC" <<'PY'
import pathlib
import sys
import zipfile

out_dir, version, chrome_src, firefox_src = (pathlib.Path(arg) for arg in sys.argv[1:5])

CHROME_FILES = ["manifest.json", "background.js", "freeze.js", "unlock.js", "rules.json", "ublock-filter.txt"]
FIREFOX_FILES = ["manifest.json", "background.js", "freeze.js", "unlock.js", "ublock-filter.txt"]

CHROME_README = f"""Locallab Flow Unlock {version} - Chrome package
=================================================

Keeps flow.google.com usable when Google marks the signed-in account as an
unsupported country, including multi-account /u/<n>/ URLs.

Install
-------
1. Extract this ZIP to a folder you will keep (Chrome reads the extension from
   that folder on every start, so do not delete it).
2. Open chrome://extensions
3. Turn on "Developer mode" (top-right).
4. Click "Load unpacked" and select the extracted folder.
5. Pin the extension if you want to see that it is active.

Files
-----
manifest.json      Chrome MV3 manifest with the declarativeNetRequest rule set
background.js      service worker that recovers tabs stranded on the block page
rules.json         the /unsupported-country block rules
freeze.js          isolated-world guard that stands down once the patch applies
unlock.js          page-world batchexecute response patch
ublock-filter.txt  optional uBlock Origin filter lines for the same route

Check
-----
Open a Flow tab, open DevTools, and run:

  document.documentElement.getAttribute('data-locallab-flow-config')

"patched" means the response patch applied. Troubleshooting:
docs/FLOW-UNLOCK-STANDALONE.md in the repository.
"""

FIREFOX_README = f"""Locallab Flow Unlock {version} - Firefox package
==================================================

Keeps flow.google.com usable when Google marks the signed-in account as an
unsupported country, including multi-account /u/<n>/ URLs.

Requires Firefox 140 or newer (page-world content scripts).

Install (temporary, works everywhere)
-------------------------------------
1. Extract this ZIP to a folder.
2. Open about:debugging#/runtime/this-firefox
3. Click "Load Temporary Add-on..." and select manifest.json in the extracted
   folder.
A temporary add-on is removed when Firefox closes.

Install (permanent)
-------------------
Firefox only installs signed add-ons. Sign this package at
https://addons.mozilla.org (Submit a New Add-on -> "On your own"), then install
the signed file from about:addons. On Firefox ESR or Developer Edition you can
instead set xpinstall.signatures.required to false in about:config.

Files
-----
manifest.json      Firefox MV3 manifest for an event page (no service worker)
background.js      event page that blocks/redirects the block route and recovers tabs
freeze.js          isolated-world guard that stands down once the patch applies
unlock.js          page-world batchexecute response patch
ublock-filter.txt  optional uBlock Origin filter lines for the same route

Check
-----
Open a Flow tab, open DevTools, and run:

  document.documentElement.getAttribute('data-locallab-flow-config')

"patched" means the response patch applied. Troubleshooting:
docs/FLOW-UNLOCK-STANDALONE.md in the repository.
"""


def build(target: pathlib.Path, src: pathlib.Path, names, readme: str) -> None:
    # Fixed timestamps keep rebuilds of unchanged sources byte-identical.
    stamp = (2024, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (src / name).read_bytes())
        info = zipfile.ZipInfo("README.txt", date_time=stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, readme)
    print(f"built {target}")


build(out_dir / f"locallab-flow-unlock-chrome-{version}.zip", chrome_src, CHROME_FILES, CHROME_README)
build(out_dir / f"locallab-flow-unlock-firefox-{version}.zip", firefox_src, FIREFOX_FILES, FIREFOX_README)
PY
