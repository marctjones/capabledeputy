#!/usr/bin/env python3
"""Supervise a daemon process group; stop conservatively under host pressure."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time


def sample(pgid: int) -> dict:
    rows = subprocess.check_output(
        ["/bin/ps", "-axo", "pgid=,rss=,pcpu="], text=True, timeout=3
    ).splitlines()
    members = [r.split() for r in rows if r.split() and int(r.split()[0]) == pgid]
    pressure = int(
        subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            text=True,
            timeout=3,
        ).strip()
    )
    return {
        "rss_mib": sum(int(r[1]) for r in members) / 1024,
        "cpu_percent": sum(float(r[2]) for r in members),
        "pressure": pressure,
    }


def reason_for(s: dict, warning_seconds: float, cpu_seconds: float) -> str | None:
    if s["pressure"] >= 4:
        return "critical system memory pressure"
    if s["rss_mib"] >= 2048:
        return "daemon process group exceeded 2048 MiB RSS"
    if warning_seconds >= 15:
        return "system memory warning sustained for 15 seconds"
    if cpu_seconds >= 30:
        return "daemon CPU exceeded 200 percent for 30 seconds"
    return None


def emit(**values):
    print(json.dumps({"time": time.time(), **values}), flush=True)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: guard-daemon-resources.py COMMAND [ARG ...]")
    child = subprocess.Popen(sys.argv[1:], start_new_session=True)
    stopped = False

    def stop(signum=None, frame=None):
        nonlocal stopped
        stopped = True
        # ProcessLookupError: the group is already gone. PermissionError:
        # some sandboxes (CI, restricted test harnesses) refuse cross-group
        # signals even to a child we spawned with start_new_session=True.
        # Either way, this is best-effort — child.wait()/poll() below still
        # observes the real exit, so a suppressed failure here doesn't mask
        # a hung process.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(child.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    warning_since = cpu_since = None
    emit(event="started", pid=child.pid, rss_limit_mib=2048)
    try:
        while child.poll() is None and not stopped:
            now = time.monotonic()
            try:
                s = sample(child.pid)
                warning_since = (warning_since or now) if s["pressure"] >= 2 else None
                cpu_since = (cpu_since or now) if s["cpu_percent"] >= 200 else None
                reason = reason_for(
                    s,
                    now - warning_since if warning_since else 0,
                    now - cpu_since if cpu_since else 0,
                )
                emit(event="sample", pid=child.pid, **s)
                if reason:
                    emit(event="resource_stop", reason=reason)
                    stop()
            except Exception as exc:
                emit(event="monitor_failed", error=str(exc))
                stop()  # Do not leave an unmonitored workload running.
            if not stopped:
                time.sleep(2)
    finally:
        stop()
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=5)
        # Also clean up upstream children after the daemon exits.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
    emit(event="exited", returncode=child.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
