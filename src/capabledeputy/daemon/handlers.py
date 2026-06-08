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
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            git_rev = result.stdout.strip()
            dirty_result = subprocess.run(
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


# Daemon startup timestamp — captured at module import (which is
# effectively daemon-startup for the daemon process). Used by
# daemon.info to compute uptime.
import os as _os  # noqa: E402 (late stdlib import, perf-localized)
import time as _time  # noqa: E402 (late stdlib import, perf-localized)

_DAEMON_STARTED_AT = _time.time()
_DAEMON_PID = _os.getpid()


def make_info_handler(app: Any) -> Handler:
    """Issue (operator-stats) — return a comprehensive daemon snapshot.

    Powers the `/server` slash command in chat. Operator runs this
    locally; daemon is single-tenant; no privacy concern with the
    detailed output. Returns version, uptime, PID, socket path,
    upstream server status, tool count by kind, session count,
    custom kind count, audit log size, memory if available.
    """

    async def handle_info(params: dict[str, Any]) -> dict[str, Any]:
        uptime_seconds = int(_time.time() - _DAEMON_STARTED_AT)

        # Tool counts by capability kind
        tools = app.registry.list() if hasattr(app, "registry") else []
        by_kind: dict[str, int] = {}
        for t in tools:
            kind = t.capability_kind
            kind_str = kind.value if hasattr(kind, "value") else str(kind)
            by_kind[kind_str] = by_kind.get(kind_str, 0) + 1

        # Session count (active vs all)
        sessions = []
        if hasattr(app, "graph") and app.graph is not None:
            try:
                sessions = list(app.graph._sessions.values())  # internal access
            except (AttributeError, TypeError):
                sessions = []
        active_sessions = sum(
            1 for s in sessions if getattr(s, "status", None) and str(s.status) == "active"
        )

        # Custom kinds (from servers.d/*.yaml — Issue #35)
        from capabledeputy.policy.capabilities import _CUSTOM_KIND_REGISTRY

        custom_kinds_count = 0
        custom_kinds_list: list[dict[str, Any]] = []
        if _CUSTOM_KIND_REGISTRY is not None:
            kinds = _CUSTOM_KIND_REGISTRY.all()
            custom_kinds_count = len(kinds)
            custom_kinds_list = [
                {
                    "name": k.name,
                    "destructive": k.destructive,
                    "description": k.description,
                }
                for k in kinds[:50]  # Truncate for response sanity
            ]

        # Audit log size (best effort — depends on whether audit
        # writer exposes its path)
        audit_size_bytes = 0
        audit_path_str = ""
        try:
            audit_path = getattr(app.audit, "_path", None) or getattr(
                app.audit,
                "path",
                None,
            )
            if audit_path is not None:
                p = Path(audit_path)
                if p.is_file():
                    audit_size_bytes = p.stat().st_size
                    audit_path_str = str(p)
        except (AttributeError, OSError):
            pass

        # Memory: best-effort RSS. Linux /proc/self/status, else
        # resource.getrusage. Skip if neither.
        rss_mb = 0
        try:
            status_path = Path("/proc/self/status")
            if status_path.is_file():
                for line in status_path.read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        # "VmRSS:    123456 kB"
                        kb = int(line.split()[1])
                        rss_mb = kb // 1024
                        break
        except (OSError, ValueError):
            try:
                import resource

                ru = resource.getrusage(resource.RUSAGE_SELF)
                # ru_maxrss is KB on Linux, bytes on macOS
                rss_mb = ru.ru_maxrss // 1024
            except (ImportError, AttributeError):
                pass

        # Upstream MCP server status (Issue: operator visibility into
        # which servers came up and which failed). Per #35 / preset
        # configs / daemon.yaml legacy block, each upstream MCP server
        # gets a row showing state + registered/rejected tool counts.
        upstream_servers_status: list[dict[str, Any]] = []
        mgr = getattr(app, "upstream_manager", None)
        if mgr is not None and hasattr(mgr, "server_status"):
            for status in sorted(mgr.server_status.values(), key=lambda s: s.name):
                upstream_servers_status.append(
                    {
                        "name": status.name,
                        "state": status.state,
                        "registered_at_epoch": status.registered_at_epoch,
                        "registered_tool_count": status.registered_tool_count,
                        "rejected_tool_count": status.rejected_tool_count,
                        "rejected_tool_names": list(status.rejected_tool_names),
                        "error": status.error,
                        "command": list(status.command),
                    }
                )

        # Python + OS info — small + useful for "what's actually running"
        import platform
        import sys

        return {
            **dict(_CODE_VERSION),  # version, git_rev, git_dirty, manifest_hash
            "pid": _DAEMON_PID,
            "uptime_seconds": uptime_seconds,
            "started_at_epoch": int(_DAEMON_STARTED_AT),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "rss_mb": rss_mb,
            "tool_count": len(tools),
            "tools_by_kind": by_kind,
            "session_count": len(sessions),
            "session_count_active": active_sessions,
            "custom_kind_count": custom_kinds_count,
            "custom_kinds": custom_kinds_list,
            "audit_path": audit_path_str,
            "audit_size_bytes": audit_size_bytes,
            "upstream_servers": upstream_servers_status,
        }

    return handle_info
