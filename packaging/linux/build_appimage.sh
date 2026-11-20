#!/usr/bin/env bash
# Wraps the PyInstaller output (dist/Plagiarism) into an AppImage.
#   packaging/linux/build_appimage.sh <version> <output dir>
set -euo pipefail
VERSION="$1"
OUT="${2:-release}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPDIR="$ROOT/build/Plagiarism.AppDir"
ARCH="$(uname -m)"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$OUT"
cp -a "$ROOT/dist/Plagiarism/." "$APPDIR/usr/bin/"
cp "$ROOT/packaging/linux/plagcheck.desktop" "$APPDIR/plagcheck.desktop"
cp "$ROOT/packaging/linux/plagcheck.desktop" "$APPDIR/usr/share/applications/"
cp "$ROOT/app/ui/icon.png" "$APPDIR/plagcheck.png"
cp "$ROOT/app/ui/icon.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/plagcheck.png"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
# the Chromium sandbox of Qt WebEngine cannot start from a read-only AppImage mount on many distributions
export QTWEBENGINE_DISABLE_SANDBOX=1
exec "$HERE/usr/bin/Plagiarism" "$@"
EOF
chmod +x "$APPDIR/AppRun"
# appimagetool refuses files that other users cannot read (e.g. a checkout with a restrictive umask)
chmod -R a+rX "$APPDIR"

TOOL="$ROOT/build/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
  chmod +x "$TOOL"
fi
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT/Plagiarism-$VERSION-linux-$ARCH.AppImage"
