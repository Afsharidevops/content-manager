#!/usr/bin/env python3
"""Export a NotebookLM browser session for use on a headless server.

Works on Linux, macOS, and Windows.

Prerequisites (one-time):
    pip install playwright
    playwright install chromium

Usage:
    python export_session.py [output.json]

Steps:
    1. A Chromium browser window opens at notebook.google.com.
    2. Sign in to your Google account (use an existing session if prompted).
    3. Return to this terminal and press Enter.
    4. A JSON file is saved with the session cookies and storage.
    5. Upload this file in the Content Console (NotebookLM view).

If you prefer not to install Playwright locally, you can also:
    - Open notebook.google.com in Chrome, press F12 → Application → Cookies,
      export cookies + localStorage manually.
    - Or run the script inside Docker: docker run --rm -it -v $PWD:/out
      -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix
      --entrypoint python afsharidevops/notebooklm-worker
      /app/../scripts/export_session.py /out/session.json
"""

import json
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Error: playwright is not installed.", file=sys.stderr)
    print("  pip install playwright", file=sys.stderr)
    print("  playwright install chromium", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    output = sys.argv[1] if len(sys.argv) > 1 else "notebooklm-session.json"

    print("=" * 60)
    print("NotebookLM Session Exporter")
    print("=" * 60)
    print()
    print("A Chromium browser will open at notebook.google.com")
    print("Sign in to your Google account in that browser window.")
    print("After signing in, return to THIS terminal and press Enter.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://notebook.google.com/")
        
        input("Press Enter after signing in... ")
        
        state = context.storage_state()
        
        with open(output, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        
        cookie_count = len(state.get("cookies", []))
        origin_count = len(state.get("origins", []))
        
        print()
        print(f"Exported {cookie_count} cookies from {origin_count} origins")
        print(f"Saved to: {os.path.abspath(output)}")
        print()
        print("Now upload this file in the Content Console:")
        print("  Panel → NotebookLM → Upload session file")
        
        browser.close()


if __name__ == "__main__":
    main()
