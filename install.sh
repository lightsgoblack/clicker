#!/bin/bash
# One-line installer for Colin's Cool Crazy Couch Computer Clicker (macOS).
#   curl -fsSL https://raw.githubusercontent.com/lightsgoblack/clicker/main/install.sh | bash
# Builds ~/Applications/Clicker.app on this Mac (so there is no Gatekeeper warning),
# installs the one Python dependency inside it, and opens it.
set -e
REPO_TGZ="https://github.com/lightsgoblack/clicker/archive/refs/heads/main.tar.gz"
APP="$HOME/Applications/Clicker.app"
say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

say "Installing Colin's Cool Crazy Couch Computer Clicker…"

# 1) Python 3 (macOS provides it through the Command Line Tools)
if ! /usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1 \
   && ! command -v python3 >/dev/null 2>&1; then
  say "macOS needs its Command Line Tools for Python. A dialog is about to appear: click Install and wait for it to finish."
  xcode-select --install >/dev/null 2>&1 || true
  until /usr/bin/python3 -c 'import sys' >/dev/null 2>&1; do sleep 5; done
  say "Tools installed."
fi
PY="$(command -v python3 || echo /usr/bin/python3)"

# 2) Get the code
TMP="$(mktemp -d)"
if [ -n "$CLICKER_SRC" ]; then
  SRC="$CLICKER_SRC"
else
  say "Downloading…"
  curl -fsSL "$REPO_TGZ" | tar -xz -C "$TMP"
  SRC="$TMP/clicker-main"
fi

# 3) Build the app bundle locally (keep an existing .venv so reinstalls are instant)
say "Building Clicker.app…"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
cp "$SRC/mac/Info.plist" "$APP/Contents/Info.plist"
cp "$SRC/mac/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp "$SRC/mac/launcher.sh" "$APP/Contents/MacOS/Clicker"; chmod +x "$APP/Contents/MacOS/Clicker"
cp "$SRC/server.py" "$SRC/index.html" "$SRC/start.command" "$SRC/apple-touch-icon.png" "$SRC/icon-512.png" "$SRC/manifest.webmanifest" "$APP/Contents/Resources/app/"
chmod +x "$APP/Contents/Resources/app/start.command"
touch "$APP"  # nudge Finder to refresh the icon

# 4) Python dependency, inside the app
cd "$APP/Contents/Resources/app"
if [ ! -x .venv/bin/python ]; then
  say "Setting up (about a minute)…"
  "$PY" -m venv .venv
  .venv/bin/pip install --quiet --disable-pip-version-check pyatv
fi
[ -z "$CLICKER_SRC" ] && rm -rf "$TMP"

say "Done. Clicker is in your Applications folder (open it from Launchpad or Spotlight any time)."
say "Opening it now…"
open "$APP"
