"""Daemon PID-file helpers.

Issue #1: `daemon stop` reliability. The RPC shutdown path is correct
when the daemon is responsive, but a daemon that's hung on an upstream
MCP subprocess teardown — or got orphaned by a parent shell — won't
exit promptly. The pidfile + signal-fallback adds a reliable
escalation path: try RPC first, then SIGTERM, then SIGKILL.

Lives next to the socket in `$XDG_RUNTIME_DIR` (or `/tmp/`).
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def default_pidfile_path() -> Path:
    """Mirrors `default_socket_path()`'s resolution so the pidfile sits
    in the same dir as the socket — operators can find both at once."""
    override = os.environ.get("CAPDEP_PIDFILE")
    if override:
        return Path(override)
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "capdep-daemon.pid"
    return Path("/tmp") / f"capdep-daemon-{os.getuid()}.pid"


def write_pidfile(pid: int | None = None) -> Path:
    """Record `pid` (defaults to current process) to the pidfile.
    Returns the path written."""
    p = default_pidfile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{pid or os.getpid()}\n", encoding="utf-8")
    return p


def read_pidfile() -> int | None:
    """Return the PID recorded in the pidfile, or None if the file is
    missing, unparseable, or refers to a process that no longer exists
    (stale)."""
    p = default_pidfile_path()
    if not p.is_file():
        return None
    try:
        pid = int(p.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    if not is_process_alive(pid):
        # Stale — clean it up so we don't keep returning it.
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return pid


def remove_pidfile() -> None:
    try:
        default_pidfile_path().unlink(missing_ok=True)
    except OSError:
        pass


def is_process_alive(pid: int) -> bool:
    """True iff the process is still alive (not a zombie).

    On Linux, reads `/proc/<pid>/status` first to detect zombie state
    (`State: Z`). A zombie process still has a PID slot, so naive
    `os.kill(pid, 0)` reports it as alive — but from the daemon-stop
    perspective, the process is dead. The parent will reap it
    eventually; we shouldn't loop waiting for that.

    On non-Linux platforms or when `/proc` is unavailable, falls
    through to the POSIX `os.kill(pid, 0)` check.
    """
    if pid <= 0:
        return False

    # Linux-specific: check /proc for zombie state.
    proc_status = Path(f"/proc/{pid}/status")
    if proc_status.is_file():
        try:
            text = proc_status.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("State:"):
                    state = line.split(":", 1)[1].strip()
                    # Z = zombie (dead, not reaped). X = dead-dying.
                    # Either means: don't wait for it.
                    if state.startswith(("Z", "X")):
                        return False
                    break
        except (FileNotFoundError, PermissionError, OSError):
            pass  # fall through to POSIX check

    # POSIX fallback.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as "alive"
        # since the operator is presumably running stop as the same
        # user that ran start.
        return True
    except OSError:
        return False
    return True


def wait_for_exit(pid: int, timeout_seconds: float = 5.0, poll_interval: float = 0.1) -> bool:
    """Block up to `timeout_seconds` waiting for `pid` to exit.
    Returns True if the process exited, False on timeout."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            return True
        time.sleep(poll_interval)
    return not is_process_alive(pid)


def terminate_with_escalation(
    pid: int,
    *,
    graceful_timeout_seconds: float = 5.0,
    force_timeout_seconds: float = 2.0,
) -> str:
    """Send SIGTERM, wait, escalate to SIGKILL if needed.

    Returns one of:
      - "already_gone"  — process wasn't alive when called
      - "term"          — SIGTERM succeeded; process exited within timeout
      - "kill"          — SIGKILL was needed after SIGTERM timed out
      - "stuck"         — process still alive after SIGKILL (unusual)
    """
    import signal

    if not is_process_alive(pid):
        return "already_gone"
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return "already_gone"
    if wait_for_exit(pid, timeout_seconds=graceful_timeout_seconds):
        return "term"
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "already_gone"
    if wait_for_exit(pid, timeout_seconds=force_timeout_seconds):
        return "kill"
    return "stuck"
