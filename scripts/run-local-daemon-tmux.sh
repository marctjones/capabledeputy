#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAPDEP="$REPO_ROOT/.venv/bin/capdep"
DAILY_MODEL_WRAPPER="$REPO_ROOT/scripts/with-daily-local-8b.sh"
DEV_BIN="$REPO_ROOT/.venv/bin"
SOCKET="${CAPDEP_TMUX_SOCKET:-capdep-daemon-debug}"
SESSION="${CAPDEP_TMUX_SESSION:-capdep-daemon}"
LOG="${CAPDEP_DAEMON_TMUX_LOG:-/tmp/capdep-daemon-tmux-debug.log}"
PATH_VALUE="$DEV_BIN:${CAPDEP_DAEMON_PATH:-$PATH}"
# Prefer the standard local model directory when it is present.  The wrapper
# retains the repository-id fallback for machines that use the Hugging Face
# cache instead.  Resolving this before creating tmux makes a restart behave
# exactly like the documented daily local launch, rather than accidentally
# selecting an unavailable offline repository entry.
DEFAULT_LOCAL_DAILY_MODEL="$HOME/Models/Qwen3-8B-4bit-mlx"
if [[ -n "${CAPDEP_DAILY_MLX_MODEL:-}" ]]; then
  DAILY_MODEL="${CAPDEP_DAILY_MLX_MODEL}"
elif [[ -d "$DEFAULT_LOCAL_DAILY_MODEL" ]]; then
  DAILY_MODEL="$DEFAULT_LOCAL_DAILY_MODEL"
else
  DAILY_MODEL=""
fi

usage() {
  cat <<USAGE
usage: $0 [start|stop|restart|status|logs]

Starts the local CapableDeputy daemon in a dedicated tmux server.
This is the reliable Codex/debug launch path because it keeps daemon
stdout/stderr visible and avoids GUI-owned daemon startup.

The daemon uses the pinned offline daily-local 8B model profile. Set
CAPDEP_DAILY_MLX_MODEL to an already-cached absolute model path when the
Hugging Face cache does not contain the baseline asset.

tmux: tmux -L $SOCKET attach -t $SESSION
log:  $LOG
USAGE
}

if [[ ! -x "$CAPDEP" ]]; then
  echo "capdep executable not found at $CAPDEP" >&2
  exit 1
fi
if [[ ! -x "$DAILY_MODEL_WRAPPER" ]]; then
  echo "daily local model wrapper not found at $DAILY_MODEL_WRAPPER" >&2
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
    "PATH='$PATH_VALUE' CAPDEP_REPO_ROOT='$REPO_ROOT' CAPDEP_IDLE_SHUTDOWN_SECONDS=off CAPDEP_DAILY_MLX_MODEL='$DAILY_MODEL' '$DAILY_MODEL_WRAPPER' '$CAPDEP' daemon start 2>&1 | tee '$LOG'; printf '\nexit=%s\n' \"\$?\" | tee -a '$LOG'; exec zsh"
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
