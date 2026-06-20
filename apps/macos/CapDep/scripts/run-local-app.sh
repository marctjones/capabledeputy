#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/.build/CapDepMac.app"
EXEC="$ROOT/.build/arm64-apple-macosx/debug/CapDepMac"
REPO_ROOT="$(cd "$ROOT/../../.." && pwd)"

cd "$ROOT"
swift build

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$EXEC" "$APP/Contents/MacOS/CapDepMac.bin"
cat > "$APP/Contents/MacOS/CapDepMac" <<SCRIPT
#!/usr/bin/env bash
export CAPDEP_REPO_ROOT="$REPO_ROOT"
exec "\$(dirname "\$0")/CapDepMac.bin"
SCRIPT
chmod +x "$APP/Contents/MacOS/CapDepMac"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>CapDepMac</string>
  <key>CFBundleIdentifier</key>
  <string>local.capabledeputy.CapDepMac</string>
  <key>CFBundleName</key>
  <string>CapDep</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

open -n -W "$APP"
