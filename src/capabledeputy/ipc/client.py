"""Client side of the daemon JSON-RPC protocol over a Unix socket."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
from anyio.abc import SocketStream

from capabledeputy.ipc.rpc import (
    INTERNAL_ERROR,
    RpcRequest,
    parse_response,
)


class DaemonError(RuntimeError):
    pass


class DaemonNotRunningError(DaemonError):
    pass


class DaemonClient:
    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path

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
        request = RpcRequest(method=method, params=params or {}, id=1)
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
