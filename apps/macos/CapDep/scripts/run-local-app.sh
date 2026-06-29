#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/.build/CapDepMac.app"
EXEC="$ROOT/.build/arm64-apple-macosx/debug/CapDepMac"
REPO_ROOT="$(cd "$ROOT/../../.." && pwd)"
CAPDEP="$REPO_ROOT/.venv/bin/capdep"
DAEMON_LOG="${TMPDIR:-/tmp}/capdep-gui-daemon.log"
# Default to a clean Swift rebuild and a fresh daemon so the GUI matches
# the current repo Python + Swift sources.
CLEAN_BUILD="${CLEAN_BUILD:-1}"
FORCE_DAEMON_RESTART="${FORCE_DAEMON_RESTART:-1}"

cd "$ROOT"
if [[ "$CLEAN_BUILD" == "1" ]]; then
  echo "[capdep-gui] swift package clean"
  swift package clean
fi
echo "[capdep-gui] swift build"
swift build

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$EXEC" "$APP/Contents/MacOS/CapDepMac.bin"
DEMO_IMAGE="${CAPDEP_DEMO_IMAGE:-$ROOT/.build/demo-cat.jpg}"
if [[ "${CAPDEP_DEMO_IMAGE:-1}" != "0" && -f "$DEMO_IMAGE" ]]; then
  chmod 644 "$DEMO_IMAGE" 2>/dev/null || true
  echo "[capdep-gui] demo image: $DEMO_IMAGE"
  DEMO_EXPORT="export CAPDEP_DEMO_IMAGE=\"$DEMO_IMAGE\""
else
  DEMO_EXPORT=""
fi

cat > "$APP/Contents/MacOS/CapDepMac" <<SCRIPT
#!/usr/bin/env bash
export CAPDEP_REPO_ROOT="$REPO_ROOT"
$DEMO_EXPORT
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

pkill -f "CapDepMac.app/Contents/MacOS/CapDepMac" 2>/dev/null || true
sleep 0.5

if [[ -x "$CAPDEP" ]]; then
  if [[ "$FORCE_DAEMON_RESTART" == "1" ]]; then
    echo "[capdep-gui] restarting daemon from $CAPDEP"
    (cd "$REPO_ROOT" && "$CAPDEP" daemon stop >/dev/null 2>&1) || true
    sleep 0.5
  fi
  if ! (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
    (cd "$REPO_ROOT" && "$CAPDEP" daemon start >"$DAEMON_LOG" 2>&1) &
    for _ in {1..150}; do
      if (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
        break
      fi
      sleep 0.2
    done
  fi
  if [[ -f "$REPO_ROOT/scripts/verify-gui-parity.py" ]]; then
    echo "[capdep-gui] verifying daemon RPC parity"
    (cd "$REPO_ROOT" && "$REPO_ROOT/.venv/bin/python" scripts/verify-gui-parity.py) || {
      echo "[capdep-gui] parity check failed; see output above" >&2
      exit 1
    }
  fi
fi

echo "[capdep-gui] opening $APP"
open -n "$APP"
