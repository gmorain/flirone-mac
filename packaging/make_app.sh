#!/bin/bash
# Build a double-clickable FLIR One.app around the project venv.
# Deliberately a launcher bundle rather than a py2app freeze: PySide6 freezing
# is fragile, and this runs the same interpreter that was tested.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Applications/FLIR One.app}"

# pyproject is the one place the version lives. The plist used to carry its own
# copy and had already drifted two releases behind.
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"
if [ -z "$VERSION" ]; then
  echo "could not read version from $ROOT/pyproject.toml" >&2
  exit 1
fi

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>FLIR One</string>
  <key>CFBundleDisplayName</key><string>FLIR One</string>
  <key>CFBundleIdentifier</key><string>local.flirone.viewer</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>flirone</string>
  <key>CFBundleIconFile</key><string>FLIROne</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>CFBundleDocumentTypes</key>
  <array>
    <dict>
      <key>CFBundleTypeName</key><string>Radiometric image</string>
      <key>LSItemContentTypes</key><array><string>public.jpeg</string></array>
      <key>CFBundleTypeRole</key><string>Viewer</string>
      <!-- Alternate, not Default: Preview stays the handler for ordinary JPEGs. -->
      <key>LSHandlerRank</key><string>Alternate</string>
    </dict>
    <dict>
      <key>CFBundleTypeName</key><string>Capture folder</string>
      <key>LSItemContentTypes</key><array><string>public.folder</string></array>
      <key>CFBundleTypeRole</key><string>Viewer</string>
      <key>LSHandlerRank</key><string>None</string>
    </dict>
  </array>
</dict>
</plist>
PLIST

ICON="$HERE/FLIROne.icns"
if [ -f "$ICON" ]; then
  cp "$ICON" "$APP/Contents/Resources/FLIROne.icns"
else
  echo "note: no $ICON yet, the bundle will use the generic icon."
  echo "      build one with: uv run python packaging/make_icon.py <image.png>"
fi

cat > "$APP/Contents/MacOS/flirone" <<LAUNCH
#!/bin/bash
# Finder gives an app bundle a minimal PATH, so Homebrew tools are invisible.
# The code resolves them by absolute path anyway; this is belt and braces.
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:\$PATH"
exec "$ROOT/.venv/bin/python" -c "import sys; sys.path.insert(0, '$ROOT/src'); from flirone.ui.app import main; sys.exit(main())" "\$@"
LAUNCH
chmod +x "$APP/Contents/MacOS/flirone"

# Finder caches icons aggressively; nudge it so a new one shows up.
touch "$APP"

echo "built $APP ($VERSION)"
