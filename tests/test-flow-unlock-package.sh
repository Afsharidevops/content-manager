#!/usr/bin/env bash
# Builds both Locallab Flow Unlock ZIP packages and checks their layout, so a
# broken manifest or a missing file cannot reach a browser again.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$ROOT_DIR" <<'PY'
import json
import pathlib
import subprocess
import sys
import tempfile
import zipfile

root = pathlib.Path(sys.argv[1])
chrome_src = root / "extensions" / "locallab-flow-unlock"
firefox_src = root / "extensions" / "locallab-flow-unlock-firefox"
manifest_chrome = json.loads((chrome_src / "manifest.json").read_text())
manifest_firefox = json.loads((firefox_src / "manifest.json").read_text())
assert manifest_chrome["version"] == manifest_firefox["version"], "packages must share a version"
version = manifest_chrome["version"]

CHROME_FILES = {
    "manifest.json",
    "background.js",
    "freeze.js",
    "unlock.js",
    "rules.json",
    "ublock-filter.txt",
    "README.txt",
}
FIREFOX_FILES = {
    "manifest.json",
    "background.js",
    "freeze.js",
    "unlock.js",
    "ublock-filter.txt",
    "README.txt",
}

with tempfile.TemporaryDirectory() as tmp:
    subprocess.run(
        [str(root / "extensions" / "package-flow-unlock.sh"), tmp],
        check=True,
        cwd=root,
    )
    chrome_zip = pathlib.Path(tmp) / f"locallab-flow-unlock-chrome-{version}.zip"
    firefox_zip = pathlib.Path(tmp) / f"locallab-flow-unlock-firefox-{version}.zip"
    for archive in (chrome_zip, firefox_zip):
        assert archive.is_file(), f"package missing: {archive}"

    with zipfile.ZipFile(chrome_zip) as archive:
        assert archive.testzip() is None, "corrupt zip"
        assert set(archive.namelist()) == CHROME_FILES, archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["background"]["service_worker"] == "background.js", manifest
        assert manifest["declarative_net_request"]["rule_resources"], manifest
        assert version in archive.read("README.txt").decode()

    with zipfile.ZipFile(firefox_zip) as archive:
        assert archive.testzip() is None, "corrupt zip"
        assert set(archive.namelist()) == FIREFOX_FILES, archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["background"].get("scripts") == ["background.js"], manifest
        assert "declarative_net_request" not in manifest, manifest
        assert manifest["browser_specific_settings"]["gecko"]["id"], manifest
        assert "webRequestBlocking" in manifest["permissions"], manifest
        assert version in archive.read("README.txt").decode()

    # The shared scripts must stay identical across the two packages.
    with zipfile.ZipFile(chrome_zip) as chrome, zipfile.ZipFile(firefox_zip) as firefox:
        for name in ("freeze.js", "unlock.js", "ublock-filter.txt"):
            assert chrome.read(name) == firefox.read(name), f"{name} differs across packages"

print("flow unlock package tests passed.")
PY
