"""RPC method handlers exposed by the daemon."""

from __future__ import annotations

import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from capabledeputy.version import __version__

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


# Issue #10: daemon captures its code version at startup time so the
# chat command can detect when a stale daemon is serving older code
# than what's on disk. We snapshot once (at import time, which is
# effectively daemon-startup time for the daemon process); the value
# never changes for the lifetime of the daemon — that's the point.
def _capture_code_version() -> dict[str, str]:
    """Best-effort capture of the daemon's running code identity.
    `git_rev` reads `git rev-parse HEAD` if the daemon was launched
    from a git checkout; otherwise it's None. `version` is the
    package version. `manifest_hash` is a quick hash of the loaded
    capabledeputy source tree's mtimes — captures uncommitted
    changes the git rev wouldn't see."""
    src_root = Path(__file__).resolve().parent.parent  # capabledeputy/
    repo_root = src_root.parent.parent  # the project root

    git_rev: str | None = None
    git_dirty: bool | None = None
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            git_rev = result.stdout.strip()
            dirty_result = subprocess.run(  # noqa: S603
                ["git", "-C", str(repo_root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            git_dirty = bool(dirty_result.stdout.strip()) if dirty_result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Manifest hash: aggregate of mtimes for every .py under src_root.
    # Cheap, deterministic across identical source trees, sensitive to
    # any file edit. Captures uncommitted-source drift.
    import hashlib

    h = hashlib.sha256()
    try:
        for py in sorted(src_root.rglob("*.py")):
            try:
                stat = py.stat()
                h.update(f"{py.relative_to(src_root)}:{stat.st_mtime_ns}:{stat.st_size}".encode())
            except OSError:
                continue
        manifest_hash = h.hexdigest()[:16]
    except OSError:
        manifest_hash = ""

    return {
        "version": __version__,
        "git_rev": git_rev or "",
        "git_dirty": "" if git_dirty is None else ("dirty" if git_dirty else "clean"),
        "manifest_hash": manifest_hash,
    }


_CODE_VERSION = _capture_code_version()


async def handle_version(params: dict[str, Any]) -> dict[str, Any]:
    return {"version": __version__}


async def handle_ping(params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


async def handle_code_version(params: dict[str, Any]) -> dict[str, Any]:
    """Return the code identity captured at daemon startup. Used by
    chat / cli to detect daemon-vs-source drift (Issue #10)."""
    return dict(_CODE_VERSION)


def default_handlers() -> dict[str, Handler]:
    return {
        "version": handle_version,
        "ping": handle_ping,
        "daemon.code_version": handle_code_version,
    }
