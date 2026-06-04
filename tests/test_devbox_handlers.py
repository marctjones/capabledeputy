"""Unit tests for devbox.summary_for_all RPC.

Drives the handler with a fake `policy_context.devbox_manager` that
exposes the `_live` dict the handler reads (same shape as the real
manager). Covers: live + dormant entries, both modes side-by-side,
and the "no manager wired" fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from capabledeputy.daemon.devbox_handlers import make_devbox_handlers


@dataclass
class _FakeLive:
    last_exec_at: float


class _FakeManager:
    def __init__(self) -> None:
        self._live: dict[tuple[UUID, str], _FakeLive] = {}


class _FakeApp:
    def __init__(self, manager: Any, workspace_root: Path) -> None:
        self.policy_context = type(
            "P",
            (),
            {"devbox_manager": manager},
        )()
        # Patch the workspace-root lookup at runtime via monkeypatch in tests.
        self._workspace_root = workspace_root


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "devbox-state"
    root.mkdir()
    monkeypatch.setattr(
        "capabledeputy.daemon.devbox_handlers._default_workspace_root",
        lambda: root,
    )
    return root


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- No manager wired ----------------------------------------------------


@pytest.mark.anyio
async def test_summary_handles_no_manager(workspace_root: Path) -> None:
    """When no PodmanDevbox is wired, the handler still works —
    returns whatever workspace dirs exist on disk, with n_live=0
    everywhere."""
    sid = "11111111-1111-1111-1111-111111111111"
    (workspace_root / sid / "py" / "work").mkdir(parents=True)
    (workspace_root / sid / "py" / "work" / "f").write_text("x")
    handler = make_devbox_handlers(
        _FakeApp(manager=None, workspace_root=workspace_root),
    )["devbox.summary_for_all"]
    result = await handler({})
    assert sid in result["sessions"]
    entry = result["sessions"][sid]
    assert entry["n_live"] == 0
    assert entry["n_workspace_dirs"] == 1
    assert entry["spec_ids"] == ["py"]
    assert entry["total_workspace_bytes"] > 0


# --- Manager wired -------------------------------------------------------


@pytest.mark.anyio
async def test_summary_merges_live_and_dormant(workspace_root: Path) -> None:
    """A session can have a live container for spec A and a dormant
    workspace dir for spec B at the same time. Both should appear in
    `spec_ids`; `n_live` reflects only the running containers."""
    sid = uuid4()
    (workspace_root / str(sid) / "py" / "work").mkdir(parents=True)
    (workspace_root / str(sid) / "node" / "work").mkdir(parents=True)
    manager = _FakeManager()
    manager._live[(sid, "py")] = _FakeLive(last_exec_at=12345.0)
    handler = make_devbox_handlers(
        _FakeApp(manager=manager, workspace_root=workspace_root),
    )["devbox.summary_for_all"]
    result = await handler({})
    entry = result["sessions"][str(sid)]
    assert entry["n_live"] == 1
    assert entry["n_workspace_dirs"] == 2  # py + node dirs on disk
    assert entry["last_exec_at"] == 12345.0
    assert "py" in entry["spec_ids"]
    assert "node" in entry["spec_ids"]


@pytest.mark.anyio
async def test_summary_live_only_no_disk_dir(workspace_root: Path) -> None:
    """Edge case: container was started but no workspace dir exists
    yet on disk (race window before mkdir or after an operator
    `rm -rf`). Entry should still appear with n_live=1."""
    sid = uuid4()
    manager = _FakeManager()
    manager._live[(sid, "py")] = _FakeLive(last_exec_at=99999.0)
    handler = make_devbox_handlers(
        _FakeApp(manager=manager, workspace_root=workspace_root),
    )["devbox.summary_for_all"]
    result = await handler({})
    entry = result["sessions"][str(sid)]
    assert entry["n_live"] == 1
    assert entry["spec_ids"] == ["py"]
    assert entry["last_exec_at"] == 99999.0


@pytest.mark.anyio
async def test_summary_last_exec_picks_max_across_specs(workspace_root: Path) -> None:
    """A session with two live containers should surface the MOST
    RECENT activity timestamp so the operator's `/sessions` view
    answers "how long since I touched this?" correctly."""
    sid = uuid4()
    manager = _FakeManager()
    manager._live[(sid, "py")] = _FakeLive(last_exec_at=100.0)
    manager._live[(sid, "node")] = _FakeLive(last_exec_at=500.0)
    handler = make_devbox_handlers(
        _FakeApp(manager=manager, workspace_root=workspace_root),
    )["devbox.summary_for_all"]
    result = await handler({})
    assert result["sessions"][str(sid)]["last_exec_at"] == 500.0
