#!/usr/bin/env bash
# One-shot launcher: starts the lc-coach service and opens a Chrome window with
# the extension auto-loaded into a dedicated profile. Ctrl-C in this terminal
# tears the server back down.
#
# First run: you'll need to sign in to leetcode.com in the launched Chrome
# window — that profile is separate from your main Chrome profile, by design,
# because Chrome blocks --load-extension on regular profiles for security. The
# session sticks across launches, so you only do it once.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$ROOT/server"
EXTENSION_DIR="$ROOT/extension"
CHROME_PROFILE="$HOME/.lc-coach/chrome-profile"
PYTHON="$SERVER_DIR/.venv/bin/python"
HEALTH_URL="http://127.0.0.1:8765/health"

if [ ! -x "$PYTHON" ]; then
  echo "[lc-coach] venv not found at $PYTHON"
  echo "[lc-coach] run this once first:"
  echo "  cd $SERVER_DIR && python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\""
  exit 1
fi

mkdir -p "$CHROME_PROFILE"

start_server=1
if curl -sSf "$HEALTH_URL" >/dev/null 2>&1; then
  echo "[lc-coach] service already running on 127.0.0.1:8765 — reusing"
  start_server=0
fi

server_pid=""
if [ "$start_server" -eq 1 ]; then
  echo "[lc-coach] starting service on 127.0.0.1:8765..."
  cd "$SERVER_DIR"
  "$PYTHON" -m lc_coach >"$HOME/.lc-coach/server.log" 2>&1 &
  server_pid=$!
  cd "$ROOT"
  for i in {1..30}; do
    if curl -sSf "$HEALTH_URL" >/dev/null 2>&1; then
      echo "[lc-coach] service ready"
      break
    fi
    sleep 0.3
    if [ "$i" -eq 30 ]; then
      echo "[lc-coach] service failed to start. tail -n 50 $HOME/.lc-coach/server.log:"
      tail -n 50 "$HOME/.lc-coach/server.log" || true
      exit 1
    fi
  done
fi

cleanup() {
  if [ -n "$server_pid" ] && kill -0 "$server_pid" 2>/dev/null; then
    echo "[lc-coach] stopping service (pid $server_pid)"
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}
trap cleanup INT TERM EXIT

CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
START_URL="${1:-https://leetcode.com/problemset/all/}"

if [ -x "$CHROME_BIN" ]; then
  echo "[lc-coach] launching Chrome with extension at $EXTENSION_DIR"
  echo "[lc-coach] dedicated profile: $CHROME_PROFILE"
  "$CHROME_BIN" \
    --user-data-dir="$CHROME_PROFILE" \
    --load-extension="$EXTENSION_DIR" \
    --no-first-run \
    --no-default-browser-check \
    "$START_URL" &
  chrome_pid=$!
else
  echo "[lc-coach] Google Chrome not found at $CHROME_BIN — opening in default browser."
  echo "[lc-coach] You'll need to load $EXTENSION_DIR via chrome://extensions manually."
  open "$START_URL"
  chrome_pid=""
fi

if [ "$start_server" -eq 1 ]; then
  echo "[lc-coach] service running. Ctrl-C here to stop. (Closing Chrome leaves the service up.)"
  wait "$server_pid"
else
  echo "[lc-coach] service was already up; this terminal will exit. Service stays running."
fi
