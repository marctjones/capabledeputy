#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/.build/CapDepMac.app"
EXEC="$ROOT/.build/arm64-apple-macosx/debug/CapDepMac"
REPO_ROOT="$(cd "$ROOT/../../.." && pwd)"
CAPDEP="$REPO_ROOT/.venv/bin/capdep"
TMUX_DAEMON="$REPO_ROOT/scripts/run-local-daemon-tmux.sh"
LAUNCHD_DAEMON="$REPO_ROOT/scripts/run-local-daemon-launchd.sh"
DAEMON_LOG="${TMPDIR:-/tmp}/capdep-gui-daemon.log"
# Default to a clean Swift rebuild and a fresh daemon so the GUI matches
# the current repo Python + Swift sources.
CLEAN_BUILD="${CLEAN_BUILD:-1}"
FORCE_DAEMON_RESTART="${FORCE_DAEMON_RESTART:-1}"
CAPDEP_DAEMON_MODE="${CAPDEP_DAEMON_MODE:-tmux}"

cd "$ROOT"

DEMO_DIR="${HOME}/Library/Application Support/CapDep/media"
DEMO_DEST="$DEMO_DIR/demo-cat.jpg"
DEMO_SOURCE="${CAPDEP_DEMO_IMAGE_SOURCE:-$ROOT/.build/demo-cat.jpg}"
if [[ -f "$DEMO_SOURCE" ]]; then
  mkdir -p "$DEMO_DIR"
  cp -f "$DEMO_SOURCE" "$DEMO_DEST"
fi

if [[ "$CLEAN_BUILD" == "1" ]]; then
  echo "[capdep-gui] swift package clean"
  swift package clean
fi
echo "[capdep-gui] swift build"
swift build

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$EXEC" "$APP/Contents/MacOS/CapDepMac.bin"
# Demo cat is opt-in (CAPDEP_ENABLE_DEMO_IMAGE=1). Leaving it on leaks a
# fixed path into the system prompt and encourages the model to reuse it.
if [[ "${CAPDEP_ENABLE_DEMO_IMAGE:-0}" == "1" && -f "$DEMO_DEST" ]]; then
  chmod 644 "$DEMO_DEST" 2>/dev/null || true
  echo "[capdep-gui] demo image: $DEMO_DEST"
  DEMO_EXPORT="export CAPDEP_DEMO_IMAGE=\"$DEMO_DEST\""
else
  DEMO_EXPORT=""
fi

cat > "$APP/Contents/MacOS/CapDepMac" <<SCRIPT
#!/usr/bin/env bash
export CAPDEP_REPO_ROOT="$REPO_ROOT"
export CAPDEP_GUI_DAEMON_COMMAND="$CAPDEP"
export CAPDEP_GUI_OWNS_DAEMON="\${CAPDEP_GUI_OWNS_DAEMON:-0}"
export CAPDEP_IDLE_SHUTDOWN_SECONDS="\${CAPDEP_IDLE_SHUTDOWN_SECONDS:-off}"
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
  start_gui_daemon() {
    if [[ "$CAPDEP_DAEMON_MODE" == "tmux" && -x "$TMUX_DAEMON" ]]; then
      "$TMUX_DAEMON" start
      return $?
    fi
    if [[ "$CAPDEP_DAEMON_MODE" == "launchd" && -x "$LAUNCHD_DAEMON" ]]; then
      "$LAUNCHD_DAEMON" start
      return $?
    fi
    if [[ -n "${DEMO_EXPORT:-}" ]]; then
      eval "$DEMO_EXPORT"
    fi
    (
      cd "$REPO_ROOT"
      exec nohup env CAPDEP_IDLE_SHUTDOWN_SECONDS="${CAPDEP_IDLE_SHUTDOWN_SECONDS:-off}" "$CAPDEP" daemon start >"$DAEMON_LOG" 2>&1 </dev/null
    ) &
    daemon_pid=$!
    disown "$daemon_pid" 2>/dev/null || true
    for _ in {1..150}; do
      if (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
        return 0
      fi
      sleep 0.2
    done
    return 1
  }
  if [[ "$FORCE_DAEMON_RESTART" == "1" ]]; then
    echo "[capdep-gui] restarting daemon from $CAPDEP"
    if [[ "$CAPDEP_DAEMON_MODE" == "tmux" && -x "$TMUX_DAEMON" ]]; then
      "$TMUX_DAEMON" restart
    elif [[ "$CAPDEP_DAEMON_MODE" == "launchd" && -x "$LAUNCHD_DAEMON" ]]; then
      "$LAUNCHD_DAEMON" restart
    else
      (cd "$REPO_ROOT" && "$CAPDEP" daemon stop >/dev/null 2>&1) || true
      sleep 0.5
      start_gui_daemon || true
    fi
  elif ! (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
    start_gui_daemon || true
  fi
  if [[ -f "$REPO_ROOT/scripts/verify-gui-parity.py" ]]; then
    echo "[capdep-gui] verifying daemon RPC parity"
    (cd "$REPO_ROOT" && "$REPO_ROOT/.venv/bin/python" scripts/verify-gui-parity.py) || {
      echo "[capdep-gui] parity check failed; see output above" >&2
      exit 1
    }
  fi
  if [[ -n "${DEMO_EXPORT:-}" && -f "$REPO_ROOT/scripts/test-gui-inline-image.py" ]]; then
    echo "[capdep-gui] verifying inline image daemon→client contract"
    eval "$DEMO_EXPORT"
    (cd "$REPO_ROOT" && "$REPO_ROOT/.venv/bin/python" scripts/test-gui-inline-image.py) || {
      echo "[capdep-gui] inline image test failed; see output above" >&2
      exit 1
    }
  fi
fi

echo "[capdep-gui] opening $APP"
open -n "$APP"

if [[ "${VERIFY_APP_CONNECTION:-1}" == "1" ]]; then
  echo "[capdep-gui] verifying opened app remains connected"
  for _ in {1..40}; do
    if pgrep -f "CapDepMac.app/Contents/MacOS/CapDepMac.bin" >/dev/null 2>&1 \
      && (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
      if (cd "$REPO_ROOT" && "$REPO_ROOT/.venv/bin/python" scripts/verify-gui-parity.py >/dev/null 2>&1); then
        echo "[capdep-gui] app connected to daemon"
        exit 0
      fi
    fi
    sleep 0.25
  done
  echo "[capdep-gui] app did not stay connected to the daemon" >&2
  echo "[capdep-gui] process snapshot:" >&2
  ps -axo pid,ppid,stat,etime,command | grep -E 'CapDepMac|capdep daemon|capdep-gui-daemon' | grep -v grep >&2 || true
  echo "[capdep-gui] daemon status:" >&2
  (cd "$REPO_ROOT" && "$CAPDEP" daemon status) >&2 || true
  GUI_DAEMON_LOG="${TMPDIR:-/tmp}/capdep-gui-daemon.log"
  if [[ -f "$GUI_DAEMON_LOG" ]]; then
    echo "[capdep-gui] tail $GUI_DAEMON_LOG:" >&2
    tail -40 "$GUI_DAEMON_LOG" >&2 || true
  fi
  exit 1
fi
