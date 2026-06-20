#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/.build/CapDepMac.app"
EXEC="$ROOT/.build/arm64-apple-macosx/debug/CapDepMac"
REPO_ROOT="$(cd "$ROOT/../../.." && pwd)"
CAPDEP="$REPO_ROOT/.venv/bin/capdep"
DAEMON_LOG="${TMPDIR:-/tmp}/capdep-gui-daemon.log"

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

if [[ -x "$CAPDEP" ]] && ! (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
  (cd "$REPO_ROOT" && "$CAPDEP" daemon stop >/dev/null 2>&1) || true
  (cd "$REPO_ROOT" && "$CAPDEP" daemon start >"$DAEMON_LOG" 2>&1) &
  for _ in {1..150}; do
    if (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
      break
    fi
    sleep 0.2
  done
fi

open -n -W "$APP"
