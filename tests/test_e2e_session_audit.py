"""End-to-end Phase 1 done-when checks: session lifecycle round-trips
through the daemon, persists across restarts, and produces audit
events that conform to the trace schema.
"""

from pathlib import Path

import anyio
import pytest

from capabledeputy.app import App
from capabledeputy.daemon.audit_handlers import make_audit_handlers
from capabledeputy.daemon.handlers import default_handlers
from capabledeputy.daemon.server import Daemon
from capabledeputy.daemon.session_handlers import make_session_handlers
from capabledeputy.ipc.client import DaemonClient


@pytest.fixture
def paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "socket": tmp_path / "test.sock",
        "state_db": tmp_path / "state.db",
        "audit_log": tmp_path / "audit.jsonl",
    }


async def _build_daemon(paths: dict[str, Path]) -> tuple[Daemon, App]:
    app = App(
        state_db_path=paths["state_db"],
        audit_log_path=paths["audit_log"],
    )
    await app.startup()
    handlers = default_handlers()
    handlers.update(make_session_handlers(app.graph))
    handlers.update(make_audit_handlers(app.audit))
    return Daemon(paths["socket"], handlers=handlers), app


async def _wait_for_socket(path: Path, timeout: float = 2.0) -> None:
    deadline = anyio.current_time() + timeout
    while anyio.current_time() < deadline:
        if path.exists():
            try:
                stream = await anyio.connect_unix(str(path))
                await stream.aclose()
                return
            except (FileNotFoundError, ConnectionRefusedError):
                pass
        await anyio.sleep(0.01)
    raise TimeoutError(f"socket {path} did not become available within {timeout}s")


async def test_full_lifecycle_through_daemon(paths: dict[str, Path]) -> None:
    daemon, _app = await _build_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        parent = await client.call("session.new", {"intent": "root"})
        child = await client.call(
            "session.fork",
            {"parent_id": parent["id"], "intent": "branch"},
        )
        await client.call("session.pause", {"session_id": child["id"]})
        await client.call("session.resume", {"session_id": child["id"]})

        listed = await client.call("session.list", {})
        ids = {s["id"] for s in listed["sessions"]}
        assert ids == {parent["id"], child["id"]}

        audit_result = await client.call("audit.list", {})
        types = [e["event_type"] for e in audit_result["events"]]
        assert "session.created" in types
        assert "session.forked" in types
        assert "session.paused" in types
        assert "session.resumed" in types

        await client.call("shutdown")


async def test_sessions_persist_across_daemon_restarts(paths: dict[str, Path]) -> None:
    daemon1, _ = await _build_daemon(paths)
    s1: dict | None = None
    s2: dict | None = None

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon1.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        s1 = await client.call("session.new", {"intent": "first"})
        s2 = await client.call("session.new", {"intent": "second"})
        await client.call("session.pause", {"session_id": s2["id"]})
        await client.call("shutdown")

    assert s1 is not None and s2 is not None

    daemon2, app2 = await _build_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon2.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        listed = await client.call("session.list", {})
        ids_to_status = {s["id"]: s["status"] for s in listed["sessions"]}
        assert ids_to_status[s1["id"]] == "active"
        assert ids_to_status[s2["id"]] == "paused"

        await client.call("shutdown")

    audit_events = await app2.audit.read_all()
    assert len(audit_events) >= 3


async def test_audit_events_conform_to_trace_schema(paths: dict[str, Path]) -> None:
    daemon, app = await _build_daemon(paths)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(paths["socket"])

        client = DaemonClient(paths["socket"])
        await client.call("session.new", {"intent": "schema-check"})
        await client.call("shutdown")

    events = await app.audit.read_all()
    assert events
    for ev in events:
        d = ev.to_dict()
        assert set(d.keys()) == {
            "audit_id",
            "timestamp",
            "event_type",
            "session_id",
            "turn_id",
            "step_id",
            "payload",
        }
        assert isinstance(d["audit_id"], str)
        assert isinstance(d["timestamp"], str)
        assert isinstance(d["event_type"], str)
