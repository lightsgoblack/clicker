#!/bin/bash
# Cut a GitHub release for the version in server.py: builds the app, tags, uploads the zip.
#   ./release.sh "What changed, one line"
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' server.py)
NOTES="${1:-Clicker $VERSION}"
[ -z "$(git status --porcelain)" ] || { echo "Commit first; the tree is dirty."; exit 1; }
./build-app.sh
git tag -f "v$VERSION" && git push -q origin "v$VERSION" --force
gh release create "v$VERSION" "dist/Clicker-mac-$(uname -m).zip" --title "Clicker $VERSION" --notes "$NOTES

Apple Silicon Macs: download the zip, unzip, drag Clicker to Applications. First launch: System Settings > Privacy & Security > Open Anyway (one time). Existing installs with the built-in updater will offer this version on their own." 2>/dev/null \
  || gh release upload "v$VERSION" "dist/Clicker-mac-$(uname -m).zip" --clobber
echo "Released v$VERSION: https://github.com/lightsgoblack/clicker/releases/latest"
