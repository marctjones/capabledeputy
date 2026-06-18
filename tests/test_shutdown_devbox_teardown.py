"""Roadmap v2 #1 — daemon-shutdown teardown for live devboxes.

App.shutdown walks every live (session, spec) and calls
devbox_manager.stop_session, so the operator running
`capdep daemon stop` doesn't leave containers running until the
next idle-reaper tick.

Tests cover:
  - Shutdown with no devbox manager wired is a no-op (no crash)
  - Shutdown with no live containers is a no-op (no crash, no print)
  - Shutdown stops every live container via stop_session
  - Workspaces are preserved (operator's work survives shutdown)
  - Per-session failures don't stop the loop (best-effort teardown)
  - shutdown is idempotent: second call is a no-op
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from capabledeputy.app import App
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.policy.context import PolicyContext


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeDevboxManager:
    """Stand-in for PodmanDevbox — records stop_session calls
    without touching real Podman. _live mirrors the production
    shape: dict keyed by (session_id, spec_id)."""

    def __init__(self) -> None:
        self._live: dict[tuple[UUID, str], object] = {}
        self.stop_calls: list[tuple[UUID, bool]] = []
        # Toggle to simulate per-session teardown failure
        self.fail_for_session: UUID | None = None

    def stop_session(
        self,
        session_id: UUID,
        *,
        purge_workspace: bool = False,
    ) -> int:
        self.stop_calls.append((session_id, purge_workspace))
        if session_id == self.fail_for_session:
            raise RuntimeError(
                "simulated podman failure during teardown",
            )
        # Remove every entry for this session and report the count
        keys = [k for k in self._live if k[0] == session_id]
        for k in keys:
            del self._live[k]
        return len(keys)


@pytest.mark.anyio
async def test_shutdown_without_devbox_manager_is_noop(tmp_path: Path) -> None:
    """An install without Podman (devbox_manager=None) shutdown
    cleanly. Belt-and-suspenders for the common case."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    # No assertions — just confirming no exception
    await app.shutdown()


@pytest.mark.anyio
async def test_shutdown_with_no_live_containers_is_noop(tmp_path: Path) -> None:
    """devbox_manager present but no live containers → no
    stop_session calls; no noise."""
    manager = _FakeDevboxManager()
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
        policy_context=PolicyContext(devbox_manager=manager),
    )
    await app.startup()
    await app.shutdown()
    assert manager.stop_calls == []


@pytest.mark.anyio
async def test_shutdown_stops_every_live_container(tmp_path: Path) -> None:
    """Each (session, spec) in manager._live is torn down on
    shutdown. Distinct sessions get distinct stop_session calls;
    multiple specs in one session collapse to a single call."""
    manager = _FakeDevboxManager()
    sid_a = uuid4()
    sid_b = uuid4()
    manager._live[(sid_a, "py")] = object()
    manager._live[(sid_a, "node")] = object()
    manager._live[(sid_b, "py")] = object()

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
        policy_context=PolicyContext(devbox_manager=manager),
    )
    await app.startup()
    await app.shutdown()
    # Two distinct sessions → two stop_session calls (one per session)
    called_sids = {sid for sid, _ in manager.stop_calls}
    assert called_sids == {sid_a, sid_b}
    # Every live container was reaped
    assert manager._live == {}


@pytest.mark.anyio
async def test_shutdown_preserves_workspaces(tmp_path: Path) -> None:
    """purge_workspace must be False (default) — the operator's work
    survives daemon restart. Purging is operator-explicit via
    `capdep maintenance workspaces --apply`."""
    manager = _FakeDevboxManager()
    manager._live[(uuid4(), "py")] = object()
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
        policy_context=PolicyContext(devbox_manager=manager),
    )
    await app.startup()
    await app.shutdown()
    for _, purge in manager.stop_calls:
        assert purge is False


@pytest.mark.anyio
async def test_per_session_failure_does_not_stop_loop(tmp_path: Path) -> None:
    """A failed teardown for one session must NOT abort the others.
    Operator running daemon stop expects best-effort cleanup; a
    transient Podman error on one container shouldn't strand the
    rest."""
    manager = _FakeDevboxManager()
    sid_doomed = uuid4()
    sid_a = uuid4()
    sid_b = uuid4()
    manager._live[(sid_doomed, "py")] = object()
    manager._live[(sid_a, "py")] = object()
    manager._live[(sid_b, "node")] = object()
    manager.fail_for_session = sid_doomed

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
        policy_context=PolicyContext(devbox_manager=manager),
    )
    await app.startup()
    await app.shutdown()
    # All three sessions saw stop_session calls — including the
    # doomed one (which raised; we just suppressed it)
    called_sids = {sid for sid, _ in manager.stop_calls}
    assert called_sids == {sid_doomed, sid_a, sid_b}
    # The non-failing sessions actually got torn down
    assert (sid_a, "py") not in manager._live
    assert (sid_b, "node") not in manager._live


@pytest.mark.anyio
async def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    """Calling shutdown twice doesn't re-stop already-stopped
    containers. The reaper task field is cleared after first call;
    the manager has no live entries to find."""
    manager = _FakeDevboxManager()
    sid = uuid4()
    manager._live[(sid, "py")] = object()

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([]),
        policy_context=PolicyContext(devbox_manager=manager),
    )
    await app.startup()
    await app.shutdown()
    first_call_count = len(manager.stop_calls)
    await app.shutdown()
    # Second call doesn't double-stop (manager._live is empty)
    assert len(manager.stop_calls) == first_call_count
