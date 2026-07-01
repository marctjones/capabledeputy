#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAPDEP="$REPO_ROOT/.venv/bin/capdep"
SOCKET="${CAPDEP_TMUX_SOCKET:-capdep-daemon-debug}"
SESSION="${CAPDEP_TMUX_SESSION:-capdep-daemon}"
LOG="${CAPDEP_DAEMON_TMUX_LOG:-/tmp/capdep-daemon-tmux-debug.log}"

usage() {
  cat <<USAGE
usage: $0 [start|stop|restart|status|logs]

Starts the local CapableDeputy daemon in a dedicated tmux server.
This is the reliable Codex/debug launch path because it keeps daemon
stdout/stderr visible and avoids GUI-owned daemon startup.

tmux: tmux -L $SOCKET attach -t $SESSION
log:  $LOG
USAGE
}

if [[ ! -x "$CAPDEP" ]]; then
  echo "capdep executable not found at $CAPDEP" >&2
  exit 1
fi

command="${1:-start}"
case "$command" in
  start|stop|restart|status|logs) ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

wait_for_daemon() {
  for _ in {1..150}; do
    if (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

start_daemon() {
  if (cd "$REPO_ROOT" && "$CAPDEP" daemon status >/dev/null 2>&1); then
    echo "capdep tmux daemon already running"
    return 0
  fi
  if tmux -L "$SOCKET" has-session -t "$SESSION" >/dev/null 2>&1; then
    tmux -L "$SOCKET" kill-session -t "$SESSION" >/dev/null 2>&1 || true
  fi
  : > "$LOG"
  tmux -L "$SOCKET" new-session -d -s "$SESSION" -c "$REPO_ROOT" \
    "CAPDEP_IDLE_SHUTDOWN_SECONDS=off '$CAPDEP' daemon start 2>&1 | tee '$LOG'; printf '\nexit=%s\n' \"\$?\" | tee -a '$LOG'; exec zsh"
  wait_for_daemon || {
    echo "capdep tmux daemon failed to start" >&2
    tail -120 "$LOG" >&2 2>/dev/null || true
    exit 1
  }
  echo "capdep tmux daemon running"
}

case "$command" in
  start)
    start_daemon
    ;;
  restart)
    (cd "$REPO_ROOT" && "$CAPDEP" daemon stop >/dev/null 2>&1) || true
    if tmux -L "$SOCKET" has-session -t "$SESSION" >/dev/null 2>&1; then
      tmux -L "$SOCKET" kill-session -t "$SESSION" >/dev/null 2>&1 || true
    fi
    start_daemon
    ;;
  stop)
    (cd "$REPO_ROOT" && "$CAPDEP" daemon stop >/dev/null 2>&1) || true
    if tmux -L "$SOCKET" has-session -t "$SESSION" >/dev/null 2>&1; then
      tmux -L "$SOCKET" kill-session -t "$SESSION" >/dev/null 2>&1 || true
    fi
    echo "capdep tmux daemon stopped"
    ;;
  status)
    (cd "$REPO_ROOT" && "$CAPDEP" daemon status)
    tmux -L "$SOCKET" has-session -t "$SESSION" >/dev/null 2>&1 \
      && echo "tmux session: $SESSION on socket $SOCKET" \
      || echo "tmux session: not running"
    ;;
  logs)
    if tmux -L "$SOCKET" has-session -t "$SESSION" >/dev/null 2>&1; then
      tmux -L "$SOCKET" capture-pane -pt "$SESSION" -S -120
    elif [[ -f "$LOG" ]]; then
      tail -120 "$LOG"
    else
      echo "no daemon log found at $LOG" >&2
      exit 1
    fi
    ;;
esac
