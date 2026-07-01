#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAPDEP="$REPO_ROOT/.venv/bin/capdep"
LABEL="${CAPDEP_LAUNCHD_LABEL:-local.capabledeputy.daemon}"
DOMAIN="gui/$(id -u)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
OUT_LOG="${CAPDEP_DAEMON_STDOUT_LOG:-/tmp/capdep-launchd.out.log}"
ERR_LOG="${CAPDEP_DAEMON_STDERR_LOG:-/tmp/capdep-launchd.err.log}"
PATH_VALUE="${CAPDEP_DAEMON_PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

usage() {
  cat <<USAGE
usage: $0 [start|stop|restart|status]

Starts the local CapableDeputy daemon as a per-user launchd agent.
Logs:
  stdout: $OUT_LOG
  stderr: $ERR_LOG
USAGE
}

if [[ ! -x "$CAPDEP" ]]; then
  echo "capdep executable not found at $CAPDEP" >&2
  exit 1
fi

command="${1:-start}"
case "$command" in
  start|stop|restart|status) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

write_plist() {
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd '$REPO_ROOT' &amp;&amp; exec env PATH='$PATH_VALUE' CAPDEP_REPO_ROOT='$REPO_ROOT' CAPDEP_IDLE_SHUTDOWN_SECONDS='off' '$CAPDEP' daemon start</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$OUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$ERR_LOG</string>
</dict>
</plist>
PLIST
}

wait_for_daemon() {
  for _ in {1..150}; do
    if (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

stop_agent() {
  launchctl bootout "$DOMAIN" "$PLIST" >/dev/null 2>&1 || true
  (cd "$REPO_ROOT" && "$CAPDEP" daemon stop >/dev/null 2>&1) || true
}

case "$command" in
  stop)
    stop_agent
    echo "capdep launchd daemon stopped"
    ;;
  restart)
    write_plist
    stop_agent
    launchctl bootstrap "$DOMAIN" "$PLIST"
    launchctl kickstart -k "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    wait_for_daemon || {
      echo "capdep launchd daemon failed to start" >&2
      tail -80 "$ERR_LOG" >&2 2>/dev/null || true
      exit 1
    }
    echo "capdep launchd daemon running"
    ;;
  start)
    write_plist
    if (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
      echo "capdep launchd daemon already running"
      exit 0
    fi
    launchctl bootstrap "$DOMAIN" "$PLIST" >/dev/null 2>&1 || true
    launchctl kickstart -k "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
    wait_for_daemon || {
      echo "capdep launchd daemon failed to start" >&2
      tail -80 "$ERR_LOG" >&2 2>/dev/null || true
      exit 1
    }
    echo "capdep launchd daemon running"
    ;;
  status)
    (cd "$REPO_ROOT" && "$CAPDEP" daemon status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null || true
    ;;
esac
