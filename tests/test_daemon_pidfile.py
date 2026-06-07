"""Tests for the pidfile-based daemon-stop escalation (Issue #1)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from capabledeputy.ipc.pidfile import (
    is_process_alive,
    read_pidfile,
    remove_pidfile,
    terminate_with_escalation,
    wait_for_exit,
    write_pidfile,
)


@pytest.fixture(autouse=True)
def isolate_pidfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CAPDEP_PIDFILE so tests never touch the real pidfile."""
    target = tmp_path / "test-capdep-daemon.pid"
    monkeypatch.setenv("CAPDEP_PIDFILE", str(target))
    yield target
    # Cleanup
    if target.exists():
        target.unlink()


def test_write_and_read_pidfile(isolate_pidfile: Path) -> None:
    written = write_pidfile()
    assert written == isolate_pidfile
    assert isolate_pidfile.is_file()
    read_back = read_pidfile()
    assert read_back == os.getpid()


def test_read_pidfile_missing() -> None:
    assert read_pidfile() is None


def test_read_pidfile_stale_is_cleaned(isolate_pidfile: Path) -> None:
    """A pidfile pointing at a non-existent PID should be auto-cleaned
    and report as missing on subsequent reads."""
    # Use a deliberately-not-real PID (Linux PID_MAX_LIMIT is typically
    # 4194304; 4000000 is almost certainly not an active process).
    fake_pid = 4_000_000
    isolate_pidfile.write_text(f"{fake_pid}\n", encoding="utf-8")
    assert read_pidfile() is None  # auto-clean reports missing
    assert not isolate_pidfile.exists()  # and removes the stale file


def test_remove_pidfile_idempotent(isolate_pidfile: Path) -> None:
    write_pidfile()
    remove_pidfile()
    remove_pidfile()  # second call must not raise
    assert not isolate_pidfile.exists()


def test_is_process_alive_current() -> None:
    assert is_process_alive(os.getpid()) is True


def test_is_process_alive_nonexistent() -> None:
    assert is_process_alive(4_000_000) is False


def test_is_process_alive_zero_pid_rejected() -> None:
    """PID 0 is the kernel scheduler; signaling it has special
    semantics. Treat as non-alive."""
    assert is_process_alive(0) is False


def test_wait_for_exit_already_gone() -> None:
    """If the process is already dead, wait_for_exit returns True immediately."""
    start = time.monotonic()
    assert wait_for_exit(4_000_000, timeout_seconds=1.0) is True
    elapsed = time.monotonic() - start
    assert elapsed < 0.2  # didn't actually wait


def test_wait_for_exit_times_out_on_live_process() -> None:
    """A live process that doesn't exit should cause wait_for_exit to
    return False within the timeout window."""
    start = time.monotonic()
    result = wait_for_exit(os.getpid(), timeout_seconds=0.3)
    elapsed = time.monotonic() - start
    assert result is False
    assert 0.3 <= elapsed <= 0.6  # roughly the timeout, with poll slack


def test_terminate_with_escalation_already_gone() -> None:
    """Targeting a dead PID returns 'already_gone' without raising."""
    assert terminate_with_escalation(4_000_000) == "already_gone"


def _reaper(proc: subprocess.Popen) -> None:
    """Reap the test subprocess so its PID slot is freed.

    Without this, `os.kill(pid, 0)` on a dead-but-zombie process
    succeeds (PID slot held until the parent reaps), which makes
    `is_process_alive` correctly report True for the zombie. In
    production this is fine — the daemon's parent shell / systemd
    reaps the daemon — but in tests we need to reap ourselves to
    assert on `is_process_alive`."""
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=2)


def test_terminate_with_escalation_term_succeeds(tmp_path: Path) -> None:
    """Spawn a real subprocess that responds to SIGTERM; verify
    SIGTERM is sent and the process exits within the grace window.
    Reap before checking is_process_alive so zombie state doesn't
    confuse the assertion."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"],
    )
    try:
        time.sleep(0.2)
        assert is_process_alive(proc.pid)
        outcome = terminate_with_escalation(
            proc.pid,
            graceful_timeout_seconds=2.0,
            force_timeout_seconds=1.0,
        )
        # Reap immediately so the PID slot frees.
        proc.wait(timeout=2)
        assert outcome in ("term", "kill")  # SIGTERM should be enough
        # After reap, the PID slot is free; is_process_alive reports False.
        assert not is_process_alive(proc.pid)
        # The signal that killed it is recorded in returncode (negative).
        assert proc.returncode < 0
    finally:
        _reaper(proc)


def test_terminate_with_escalation_kill_needed(tmp_path: Path) -> None:
    """Spawn a subprocess that ignores SIGTERM; verify escalation to
    SIGKILL happens."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "while True: time.sleep(0.1)\n",
        ],
    )
    try:
        time.sleep(0.3)  # let signal handler register
        assert is_process_alive(proc.pid)
        outcome = terminate_with_escalation(
            proc.pid,
            graceful_timeout_seconds=0.5,  # short grace; we expect escalation
            force_timeout_seconds=2.0,
        )
        proc.wait(timeout=2)
        assert outcome == "kill"
        assert not is_process_alive(proc.pid)
        # SIGKILL is signal 9; returncode should be -9.
        assert proc.returncode == -signal.SIGKILL
    finally:
        _reaper(proc)
