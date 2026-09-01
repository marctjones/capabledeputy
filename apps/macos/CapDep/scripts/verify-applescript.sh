#!/usr/bin/env bash
set -euo pipefail

# GUI-sensitive smoke for the AppleScript scripting surface
# (docs/adr-0001-applescript-ui-scripting.md). Needs a RUNNING CapDep app
# (scripts/run-local-app.sh or the App/CapDep.xcodeproj build) and an
# Automation/TCC grant for the terminal driving it — run it on a permissioned
# machine, alongside verify-gui-flows.sh; it cannot run headless in CI.
#
# Read-only by default. Pass --send "some prompt" to also exercise the
# send-prompt command against the current chat session.

APP_NAME="${CAPDEP_APP_NAME:-CapDep}"

run() {
  osascript -e "tell application \"$APP_NAME\" to $1"
}

echo "[applescript-smoke] daemon connected:"
run "get daemon connected"

echo "[applescript-smoke] current session id:"
run "get current session id"

echo "[applescript-smoke] refresh state:"
run "refresh state"

echo "[applescript-smoke] pending approval ids:"
run "get pending approval ids"

if [[ "${1:-}" == "--send" && -n "${2:-}" ]]; then
  echo "[applescript-smoke] send prompt:"
  run "send prompt \"$2\""
fi

echo "[applescript-smoke] OK"
