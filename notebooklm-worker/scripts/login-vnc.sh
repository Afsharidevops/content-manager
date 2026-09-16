#!/bin/bash
# Interactive NotebookLM login via VNC/noVNC.
set -euo pipefail

DATA_DIR="${1:-/data}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-8861}"
PROFILE_DIR="${NOTEBOOKLM_BROWSER_PROFILE:-$DATA_DIR/notebooklm-browser-profile}"
SIGNAL_FILE="$DATA_DIR/.login-done"
mkdir -p "$PROFILE_DIR"
rm -f "$SIGNAL_FILE"

cleanup() { kill "$CHROMIUM_PID" "$WS_PID" "$X11VNC_PID" "$XVFB_PID" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT

echo "Starting Xvfb :$DISPLAY_NUM ..."
Xvfb ":$DISPLAY_NUM" -screen 0 1280x1024x24 & XVFB_PID=$!; sleep 1

echo "Starting x11vnc :$VNC_PORT ..."
x11vnc -display ":$DISPLAY_NUM" -forever -nopw -quiet -rfbport "$VNC_PORT" & X11VNC_PID=$!; sleep 1

echo "Starting Chromium..."
CHROME_BIN=$(python3 -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()" 2>/dev/null || which chromium || true) \
[ -z "$CHROME_BIN" ] && { echo "ERROR chromium not found"; exit 1; } \
echo "CHROME $CHROME_BIN" \
DISPLAY=":$DISPLAY_NUM" "$CHROME_BIN" --no-sandbox --disable-blink-features=AutomationControlled \
    --disable-dev-shm-usage --disable-extensions --window-size=1280,1024 \
    --user-data-dir="$PROFILE_DIR" "https://notebooklm.google.com/" & CHROMIUM_PID=$!

echo "Starting websockify :$NOVNC_PORT -> :$VNC_PORT ..."
websockify --web /opt/novnc "$NOVNC_PORT" "localhost:$VNC_PORT" & WS_PID=$!; sleep 2

echo "LISTENING http://0.0.0.0:$NOVNC_PORT/vnc.html"
echo "SIGNAL $SIGNAL_FILE"

# Poll for completion signal
while [ ! -f "$SIGNAL_FILE" ]; do sleep 2; done
rm -f "$SIGNAL_FILE"
echo "Login session done."
