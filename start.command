#!/bin/bash
# Double-click this in Finder (or run it in Terminal) to start Clicker.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (about a minute)…"
  python3 -m venv .venv && .venv/bin/pip install --quiet pyatv anthropic || { echo "Setup failed. Is python3 installed? (xcode-select --install or brew install python)"; read -r -p "Press Enter to close"; exit 1; }
fi
exec .venv/bin/python server.py "$@"
