#!/bin/bash
# Clicker.app launcher. Lives at Clicker.app/Contents/MacOS/Clicker.
# Starts the server in the background (no Terminal window) and opens the browser.
# If Clicker is already running, it just opens the browser to it.
APP_DIR="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
LOG="$HOME/Library/Logs/Clicker.log"
PORT="${PORT:-8765}"
cd "$APP_DIR" || exit 1
alert() { osascript -e "display alert \"Clicker\" message \"$1\"" >/dev/null 2>&1; }
if curl -fs --max-time 1 "http://localhost:$PORT/api/status" >/dev/null 2>&1; then
  open "http://localhost:$PORT/"; exit 0
fi
PY="$(command -v python3 || echo /usr/bin/python3)"
if [ ! -x .venv/bin/python ]; then
  if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    alert "Clicker needs Python 3. macOS will offer to install its Command Line Tools. Click Install, wait for it to finish, then open Clicker again."
    xcode-select --install >/dev/null 2>&1; exit 1
  fi
  "$PY" -m venv .venv >>"$LOG" 2>&1 && .venv/bin/pip install --quiet pyatv anthropic >>"$LOG" 2>&1 || { alert "First-time setup failed. Details are in ~/Library/Logs/Clicker.log"; exit 1; }
fi
nohup .venv/bin/python server.py --port "$PORT" >>"$LOG" 2>&1 &
