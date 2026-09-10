"""Dispatch-layer enforcement of operator-only RPC methods.

Before this, "operator-only" was pure convention: the daemon socket had no
caller-identity check at all, and the only thing standing between an
agent-facing MCP client and e.g. `operator.grant_capability` was whether
`mcp_server/control.py` happened to curate it as a tool. These tests exercise
the actual dispatch-layer gate in `daemon/server.py` + `daemon/authz.py`,
independent of any client's tool curation.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from capabledeputy.daemon.authz import requires_operator
from capabledeputy.daemon.server import Daemon
from capabledeputy.ipc.client import DaemonClient, DaemonError
from capabledeputy.ipc.rpc import PERMISSION_DENIED
from capabledeputy.ipc.socket_path import operator_token_path
from tests._socket_helpers import short_socket_path


@pytest.fixture
def socket_path(tmp_path: Path) -> Path:
    return short_socket_path()


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


async def _raw_call(
    socket_path: Path,
    method: str,
    params: dict | None = None,
    auth: str | None = None,
) -> dict:
    """Speak the wire protocol directly, bypassing DaemonClient entirely —
    this is exactly what "any other same-user process that knows the socket
    path and an RPC method name" can do."""
    stream = await anyio.connect_unix(str(socket_path))
    try:
        payload: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params:
            payload["params"] = params
        if auth is not None:
            payload["auth"] = auth
        await stream.send((json.dumps(payload) + "\n").encode("utf-8"))
        buf = b""
        async for chunk in stream:
            buf += chunk
            if b"\n" in buf:
                line, _, _ = buf.partition(b"\n")
                return json.loads(line)
        raise AssertionError("connection closed without response")
    finally:
        await stream.aclose()


def test_operator_only_methods_include_documented_gaps() -> None:
    """Guard against the table drifting from what's actually documented as
    operator-only elsewhere in the daemon — the finding this fixes was
    precisely that convention and enforcement had diverged."""
    for method in (
        "operator.grant_capability",
        "capability.revoke",
        "session.set_enforcement",
        "session.set_first_use_prompts",
        "relationship_group.add_member",
        "relationship_group.remove_member",
        "relationship_group.promote",
        "shutdown",
    ):
        assert requires_operator(method), method
    # session.grant_capability is the deliberately-AI-safe narrowing path —
    # must never require operator auth or the agent's normal grant flow breaks.
    assert not requires_operator("session.grant_capability")


async def test_operator_token_file_is_written_owner_only(socket_path: Path) -> None:
    daemon = Daemon(socket_path)
    token_path = operator_token_path(socket_path)
    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        assert token_path.is_file()
        assert (token_path.stat().st_mode & 0o777) == 0o600
        assert token_path.read_text().strip()

        await DaemonClient(socket_path).call("shutdown")

    assert not token_path.exists()


async def test_operator_only_method_rejected_without_auth(socket_path: Path) -> None:
    daemon = Daemon(socket_path)
    daemon.register("capability.revoke", lambda params: _ok())

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        response = await _raw_call(socket_path, "capability.revoke", {})
        assert response["error"]["code"] == PERMISSION_DENIED
        assert "operator" in response["error"]["message"]

        await DaemonClient(socket_path).call("shutdown")


async def test_operator_only_method_rejected_with_forged_token(socket_path: Path) -> None:
    daemon = Daemon(socket_path)
    daemon.register("capability.revoke", lambda params: _ok())

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        response = await _raw_call(
            socket_path,
            "capability.revoke",
            {},
            auth="not-the-real-token",
        )
        assert response["error"]["code"] == PERMISSION_DENIED

        await DaemonClient(socket_path).call("shutdown")


async def test_operator_only_method_succeeds_with_real_token(socket_path: Path) -> None:
    daemon = Daemon(socket_path)
    daemon.register("capability.revoke", lambda params: _ok())

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        real_token = operator_token_path(socket_path).read_text().strip()
        response = await _raw_call(socket_path, "capability.revoke", {}, auth=real_token)
        assert response.get("result") == {"ok": True}

        await DaemonClient(socket_path).call("shutdown")


async def test_untrusted_daemon_client_cannot_reach_operator_only_method(
    socket_path: Path,
) -> None:
    """This is the MCP control/admin/session server's exact posture:
    DaemonClient(socket, trusted=False)."""
    daemon = Daemon(socket_path)
    daemon.register("capability.revoke", lambda params: _ok())

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        untrusted = DaemonClient(socket_path, trusted=False)
        with pytest.raises(DaemonError, match="operator"):
            await untrusted.call("capability.revoke", {})

        await DaemonClient(socket_path).call("shutdown")


async def test_untrusted_daemon_client_cannot_shut_down_daemon(socket_path: Path) -> None:
    daemon = Daemon(socket_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        untrusted = DaemonClient(socket_path, trusted=False)
        with pytest.raises(DaemonError, match="operator"):
            await untrusted.call("shutdown")
        assert socket_path.exists()

        await DaemonClient(socket_path).call("shutdown")


async def test_trusted_daemon_client_still_reaches_non_operator_methods(
    socket_path: Path,
) -> None:
    """Every non-operator RPC keeps working with zero client-side change —
    the default-trusted DaemonClient auto-attaches the token transparently."""
    daemon = Daemon(socket_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(daemon.serve)
        await _wait_for_socket(socket_path)

        client = DaemonClient(socket_path)
        assert await client.call("ping") == {"ok": True}

        await client.call("shutdown")


async def _ok() -> dict:
    return {"ok": True}
