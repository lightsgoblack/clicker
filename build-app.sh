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
  --collect-submodules pyatv --collect-submodules anthropic --collect-submodules segno \
  server.py >/dev/null
PL=dist/Clicker.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Clicker" "$PL" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Clicker" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PL" 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PL"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PL" 2>/dev/null || true
rm -rf build Clicker.spec
ARCH=$(uname -m)
# Signing: a "Developer ID Application" identity in the keychain means a real signature +
# notarization (no "Open Anyway" for anyone). Otherwise ad-hoc, which still runs on Apple Silicon.
SIGN_ID="${CLICKER_SIGN_ID:-$(security find-identity -v -p codesigning 2>/dev/null | grep -o '"Developer ID Application: [^"]*"' | head -1 | tr -d '"')}"
NOTARY_PROFILE="${CLICKER_NOTARY_PROFILE:-clicker-notary}"
if [ -n "$SIGN_ID" ]; then
  echo "Signing with: $SIGN_ID"
  codesign --force --deep --options runtime --timestamp --entitlements mac/entitlements.plist --sign "$SIGN_ID" dist/Clicker.app
  codesign --verify --deep --strict dist/Clicker.app && echo "signature ok"
  ditto -c -k --keepParent dist/Clicker.app "dist/Clicker-mac-$ARCH.zip"
  if xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
    echo "Notarizing (this takes a few minutes)…"
    xcrun notarytool submit "dist/Clicker-mac-$ARCH.zip" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple dist/Clicker.app
    rm -f "dist/Clicker-mac-$ARCH.zip"; ditto -c -k --keepParent dist/Clicker.app "dist/Clicker-mac-$ARCH.zip"
    echo "Notarized and stapled."
  else
    echo "No notary credentials (keychain profile '$NOTARY_PROFILE'); signed but not notarized."
  fi
else
  codesign --force --deep --sign - dist/Clicker.app
  ditto -c -k --keepParent dist/Clicker.app "dist/Clicker-mac-$ARCH.zip"
  echo "Ad-hoc signed (no Developer ID identity found)."
fi
echo "Built Clicker $VERSION: dist/Clicker.app and dist/Clicker-mac-$ARCH.zip ($(du -h "dist/Clicker-mac-$ARCH.zip" | cut -f1))"
