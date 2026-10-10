"""Client reconnect against a real daemon over the real Unix socket (#319).

The unit tests in test_ipc_reconnect.py use call doubles. These tests kill and
restart an actual daemon on the *same socket path* and assert what a connected
client observes: ambient calls ride out a bounce and see persisted state, while
the user's explicit send surfaces a real outage quickly instead of hanging.
"""

from __future__ import annotations

import time
from pathlib import Path

import anyio
import pytest

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError
from capabledeputy.ipc.reconnect import AMBIENT_RECONNECT, SEND_RECONNECT, call_with_reconnect
from tests.daemon_integration import (
    DaemonTestPaths,
    build_test_daemon,
    daemon_test_paths,
    wait_for_socket,
)


async def _first_run(paths: DaemonTestPaths) -> str:
    """Start a daemon, create a session, shut the daemon down; return the session id."""
    daemon, _app = await build_test_daemon(paths)
    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await wait_for_socket(paths.socket)
        client = DaemonClient(paths.socket)
        created = await client.call("session.new", {"intent": "survives a bounce"})
        await client.call("shutdown")
    return str(created["id"])


async def test_ambient_call_rides_out_a_daemon_restart_on_the_same_socket(tmp_path: Path) -> None:
    paths = daemon_test_paths(tmp_path)
    session_id = await _first_run(paths)
    client = DaemonClient(paths.socket)

    # The daemon is down: a plain call fails, which is what the reconnect layer exists for.
    with pytest.raises(DaemonNotRunningError):
        await client.call("session.list", {})

    attempts: list[int] = []

    async def restart_after_delay() -> None:
        await anyio.sleep(0.4)
        daemon2, _app2 = await build_test_daemon(paths)
        await daemon2.serve()

    async with anyio.create_task_group() as tg:
        tg.start_soon(restart_after_delay)
        listed = await call_with_reconnect(
            client,
            "session.list",
            {},
            on_reconnect=attempts.append,
            **AMBIENT_RECONNECT,
        )
        # Persisted state is visible through the restarted daemon.
        assert session_id in {s["id"] for s in listed["sessions"]}
        assert attempts, "the client should have retried at least once while the daemon was down"
        await client.call("shutdown")


async def test_send_budget_surfaces_a_real_outage_quickly(tmp_path: Path) -> None:
    paths = daemon_test_paths(tmp_path)
    await _first_run(paths)  # leaves the daemon stopped
    client = DaemonClient(paths.socket)

    started = time.monotonic()
    with pytest.raises(DaemonNotRunningError):
        await call_with_reconnect(client, "session.list", {}, **SEND_RECONNECT)
    elapsed = time.monotonic() - started

    # SEND_RECONNECT allows one sub-second retry, so an outage must not hang the UI.
    assert elapsed < 1.5, f"send path waited {elapsed:.2f}s on a dead daemon"


async def test_a_non_transient_daemon_error_is_not_retried(tmp_path: Path) -> None:
    paths = daemon_test_paths(tmp_path)
    daemon, _app = await build_test_daemon(paths)
    retries: list[int] = []
    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await wait_for_socket(paths.socket)
        client = DaemonClient(paths.socket)
        with pytest.raises(Exception) as excinfo:
            await call_with_reconnect(
                client,
                "no.such.method",
                {},
                on_reconnect=retries.append,
                **AMBIENT_RECONNECT,
            )
        assert not isinstance(excinfo.value, DaemonNotRunningError)
        assert retries == []
        await client.call("shutdown")
