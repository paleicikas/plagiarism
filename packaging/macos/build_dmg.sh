#!/usr/bin/env bash
# Packs dist/Plagiarism.app into a compressed disk image with an /Applications shortcut.
#   packaging/macos/build_dmg.sh <version> <arch> <output dir>
set -euo pipefail
VERSION="$1"
ARCH="$2"
OUT="${3:-release}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$ROOT/build/dmg"

rm -rf "$STAGE"
mkdir -p "$STAGE" "$OUT"
cp -R "$ROOT/dist/Plagiarism.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
# PyInstaller signs the bundle ad hoc (required on Apple silicon); this is not a Developer ID signature
codesign --verify --deep "$STAGE/Plagiarism.app" || echo "warning: app bundle signature does not verify"
hdiutil create -volname "Plagiarism $VERSION" -srcfolder "$STAGE" -ov -format UDZO \
  "$OUT/Plagiarism-$VERSION-macos-$ARCH.dmg"
