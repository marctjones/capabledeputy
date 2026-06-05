"""RPC handlers for persistent devbox state.

Surfaces what the chat REPL needs to enrich the `/sessions` view:
how many containers per session, total workspace size, last activity.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.daemon.handlers import Handler
from capabledeputy.substrate.podman_devbox import _default_workspace_root


def _dir_size(path: Path) -> int:
    """Recursive byte count. Symlinks count by target size; OSErrors
    swallow to 0 so a permission-denied entry doesn't break the whole
    scan. Same semantics as the CLI helper of the same name."""
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def make_devbox_handlers(app: Any) -> dict[str, Handler]:
    """Devbox RPC. Returns an empty dict when no devbox manager is
    wired — the chat REPL is expected to handle the missing handler
    gracefully (renders an empty devbox column). Mirrors the
    "no provider → no surface" pattern from the tool layer."""

    policy_context = getattr(app, "policy_context", None)
    manager = getattr(policy_context, "devbox_manager", None) if policy_context else None

    async def devbox_summary_for_all(_params: dict[str, Any]) -> dict[str, Any]:
        """Map of session_id (str) → {n_live, total_workspace_bytes,
        spec_ids[]}. Includes both LIVE containers (from the manager's
        in-memory registry) and dormant workspace dirs on disk."""
        root = _default_workspace_root()
        summary: dict[str, dict[str, Any]] = {}

        # Workspace dirs on disk — even if no container is live, the
        # /work volume's bytes still count. Each subdir is a session id.
        if root.is_dir():
            for sdir in root.iterdir():
                if not sdir.is_dir():
                    continue
                sid = sdir.name
                size = _dir_size(sdir)
                specs = sorted(p.name for p in sdir.iterdir() if p.is_dir())
                summary[sid] = {
                    "n_live": 0,
                    "n_workspace_dirs": len(specs),
                    "total_workspace_bytes": size,
                    "spec_ids": specs,
                    "last_exec_at": None,
                }

        # Live container records — bumps n_live + last_exec_at for
        # any (session, spec) currently managed.
        if manager is not None:
            for (sid_uuid, spec_id), live in manager._live.items():
                sid = str(sid_uuid)
                entry = summary.setdefault(
                    sid,
                    {
                        "n_live": 0,
                        "n_workspace_dirs": 0,
                        "total_workspace_bytes": 0,
                        "spec_ids": [],
                        "last_exec_at": None,
                    },
                )
                entry["n_live"] += 1
                if spec_id not in entry["spec_ids"]:
                    entry["spec_ids"] = sorted(entry["spec_ids"] + [spec_id])
                prev = entry["last_exec_at"]
                if prev is None or live.last_exec_at > prev:
                    entry["last_exec_at"] = live.last_exec_at

        return {"sessions": summary}

    return {"devbox.summary_for_all": devbox_summary_for_all}
