#!/bin/bash
# Builds a self-contained Clicker.app (Python included) with PyInstaller, then zips it
# for GitHub Releases. Output: dist/Clicker.app and dist/Clicker-mac-$(uname -m).zip
# The build is for the architecture of the Mac that runs it (arm64 on Apple Silicon).
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/pyinstaller ] || .venv/bin/pip install --quiet pyinstaller
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' server.py)
rm -rf build dist
.venv/bin/pyinstaller --noconfirm --clean --windowed --name Clicker \
  --icon mac/AppIcon.icns --osx-bundle-identifier com.colin.clicker \
  --add-data "index.html:." --add-data "apple-touch-icon.png:." --add-data "icon-512.png:." --add-data "manifest.webmanifest:." \
  --collect-submodules pyatv --collect-submodules anthropic \
  server.py >/dev/null
PL=dist/Clicker.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Clicker" "$PL" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Clicker" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PL" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PL" 2>/dev/null || true
# Ad-hoc signature (required for the binary to run on Apple Silicon at all).
codesign --force --deep --sign - dist/Clicker.app
rm -rf build Clicker.spec
ARCH=$(uname -m)
ditto -c -k --keepParent dist/Clicker.app "dist/Clicker-mac-$ARCH.zip"
echo "Built Clicker $VERSION: dist/Clicker.app and dist/Clicker-mac-$ARCH.zip ($(du -h "dist/Clicker-mac-$ARCH.zip" | cut -f1))"
