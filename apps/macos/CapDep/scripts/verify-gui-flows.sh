#!/usr/bin/env bash
# Headless verification of CapDepMac GUI flows (#331 override, #332 approval,
# #333 onboarding) via the CAPDEP_GUI_TEST_COMMAND_FILE hook — no human clicks.
#
# Launches the app with the test-command hook, feeds JSON commands that drive
# the exact model paths the GUI controls use, and asserts the outcomes the hook
# logs to ~/Library/Logs/CapDep/chat-trace.log. Exits non-zero on any failure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT/../../.." && pwd)"
CAPDEP="$REPO_ROOT/.venv/bin/capdep"
CMD_FILE="${TMPDIR:-/tmp}/capdep-gui-flow-cmds.txt"
TRACE="$HOME/Library/Logs/CapDep/chat-trace.log"
: > "$CMD_FILE"
TRACE_START=$(wc -l < "$TRACE" 2>/dev/null || echo 0)

echo "[verify-gui] launching CapDepMac with the test-command hook…"
CLEAN_BUILD=0 FORCE_DAEMON_RESTART=1 CAPDEP_GUI_BACKGROUND_OPEN=1 \
  CAPDEP_GUI_TEST_COMMAND_FILE="$CMD_FILE" VERIFY_APP_CONNECTION=1 \
  "$ROOT/scripts/run-local-app.sh"

send() { echo "$1" >> "$CMD_FILE"; sleep 2; }

# Visual capture: raise the named window and screenshot it to SHOT_DIR, right
# after it's driven open (the windows are transient, so capture immediately).
# Requires Accessibility + Screen Recording permission for the terminal.
SHOT_DIR="${CAPDEP_GUI_SHOT_DIR:-${TMPDIR:-/tmp}/capdep-gui-shots}"
mkdir -p "$SHOT_DIR"
snap() {
  local win="$1" label="$2"
  local bounds
  bounds=$(osascript -e "tell application \"System Events\" to tell process \"CapDepMac.bin\" to return (position of (first window whose name is \"${win}\")) & (size of (first window whose name is \"${win}\"))" 2>/dev/null | tr ',' ' ' || true)
  if [ -z "$bounds" ]; then echo "  ⚠️  ${label}: window '${win}' not found for screenshot" >&2; return 0; fi
  osascript -e "tell application \"System Events\" to tell process \"CapDepMac.bin\" to perform action \"AXRaise\" of (first window whose name is \"${win}\")" 2>/dev/null || true
  read -r wx wy ww wh <<< "$bounds"
  screencapture -x -R"${wx},${wy},${ww},${wh}" "$SHOT_DIR/${label}.png" 2>/dev/null \
    && echo "  📷 ${label}: $SHOT_DIR/${label}.png" || echo "  ⚠️  ${label}: screenshot failed" >&2
}

# Wait for a `gui_test_hook_<name>` line to appear after TRACE_START; print it.
expect() {
  local name="$1" desc="$2"
  for _ in $(seq 1 15); do
    local line
    line=$(tail -n +"$((TRACE_START + 1))" "$TRACE" 2>/dev/null | grep "gui_test_hook_${name}" | tail -1 || true)
    if [ -n "$line" ]; then echo "  ✅ ${desc}: ${line#*] }"; return 0; fi
    sleep 1
  done
  echo "  ❌ ${desc}: no gui_test_hook_${name} in the trace" >&2
  return 1
}

fail=0

echo "[verify-gui] #331 override control…"
send '{"command":"open_override"}'
snap "Override" "331-override-card"
expect open_override "override card opens" || fail=1
# Drive the request against a real session so it exercises the full dual-control
# path (a valid session yields a policy decision, not a UUID parse error). Use a
# freshly created session — `session list` prints truncated 8-char ids.
SID=$("$CAPDEP" session new --intent "gui override verification" 2>/dev/null \
  | grep -oiE "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}" | head -1 || true)
send "{\"command\":\"request_override\",\"session_id\":\"${SID}\",\"action_kind\":\"SEND_EMAIL\",\"target\":\"x@example.com\",\"floor\":\"prohibited\",\"invoker\":\"marc\"}"
expect request_override "override request reaches the daemon (faithful outcome)" || fail=1

echo "[verify-gui] #333 onboarding wizard…"
send '{"command":"open_onboarding"}'
snap "Set Up CapDep" "333-onboarding-wizard"
expect open_onboarding "onboarding wizard opens + reports readiness" || fail=1

echo "[verify-gui] #332 approval surfacing…"
send '{"command":"present_approval","approval_id":1}'
snap "Approval" "332-approval-card"
expect present_approval "approval surfacing routes to the card" || fail=1

if [ "$fail" -ne 0 ]; then
  echo "[verify-gui] FAILED — see the ❌ lines above" >&2
  exit 1
fi
echo "[verify-gui] all GUI flows drove + logged their outcomes ✅"
echo "[verify-gui] window screenshots (visual check) in: $SHOT_DIR"
