"""Client side of the daemon JSON-RPC protocol over a Unix socket.

Two patterns:
  - call(method, params) — request/response, one shot. Each call opens
    a fresh connection, sends, reads the response, closes.
  - subscribe(streams) — long-lived connection that yields server-pushed
    `event` notifications. Closing the iterator closes the connection
    and unsubscribes everything implicitly.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from anyio.abc import SocketStream

from capabledeputy.ipc.rpc import (
    INTERNAL_ERROR,
    JSONRPC_VERSION,
    RpcRequest,
    parse_response,
)
from capabledeputy.ipc.socket_path import operator_token_path


class DaemonError(RuntimeError):
    pass


class DaemonNotRunningError(DaemonError):
    pass


class DaemonClient:
    def __init__(self, socket_path: Path, *, trusted: bool = True) -> None:
        """`trusted=True` (default) is for the operator's own surfaces —
        CLI, TUI, the macOS app — and auto-attaches the daemon's
        per-run operator token (see `daemon/authz.py`) when its file is
        present, so operator-only RPCs keep working with no per-call
        change. Any client that speaks for an external MCP host (an
        agent's session-bound server, the admin server, the control
        server) MUST be constructed with `trusted=False` so operator-only
        methods are rejected by the daemon regardless of what that host's
        tool curation happens to expose."""
        self._socket_path = socket_path
        self._trusted = trusted

    def _operator_token(self) -> str | None:
        if not self._trusted:
            return None
        try:
            return operator_token_path(self._socket_path).read_text().strip()
        except OSError:
            return None

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[SocketStream]:
        try:
            stream = await anyio.connect_unix(str(self._socket_path))
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise DaemonNotRunningError(
                f"daemon not running at {self._socket_path}",
            ) from e
        try:
            yield stream
        finally:
            await stream.aclose()

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request = RpcRequest(method=method, params=params or {}, id=1, auth=self._operator_token())
        async with self._connect() as stream:
            await stream.send(request.encode())
            buf = b""
            async for chunk in stream:
                buf += chunk
                if b"\n" in buf:
                    line, _, _ = buf.partition(b"\n")
                    response = parse_response(line)
                    if response.error is not None:
                        msg = response.error.get("message", "unknown")
                        code = response.error.get("code", INTERNAL_ERROR)
                        raise DaemonError(f"{msg} (code {code})")
                    return response.result
            raise DaemonError("connection closed without response")

    async def subscribe(
        self,
        streams: list[str],
        *,
        cancel_turns_on_disconnect: list[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Open a long-lived connection and yield event notifications.

        Yields one dict per event with keys {stream, data}. Caller must
        consume the iterator (or close the underlying generator) to
        unsubscribe; closing the connection on the daemon side also
        ends iteration.
        """
        return _subscribe_iter(
            self._socket_path,
            streams,
            cancel_turns_on_disconnect=cancel_turns_on_disconnect,
            auth=self._operator_token(),
        )


async def _subscribe_iter(
    socket_path: Path,
    streams: list[str],
    *,
    cancel_turns_on_disconnect: list[str] | None = None,
    auth: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    try:
        stream = await anyio.connect_unix(str(socket_path))
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise DaemonNotRunningError(f"daemon not running at {socket_path}") from e

    try:
        sub_payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": "subscribe",
            "id": 1,
            "params": {
                "streams": streams,
                "cancel_turns_on_disconnect": cancel_turns_on_disconnect or [],
            },
        }
        if auth is not None:
            sub_payload["auth"] = auth
        sub = json.dumps(sub_payload, separators=(",", ":")) + "\n"
        await stream.send(sub.encode("utf-8"))

        buf = b""
        async for chunk in stream:
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("method") == "event":
                    yield obj.get("params") or {}
                # subscribe response and other request/response messages
                # are ignored here; consumers want event notifications.
    finally:
        await stream.aclose()
