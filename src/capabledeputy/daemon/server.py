"""Async Unix-socket JSON-RPC server."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path

import anyio
from anyio.abc import SocketStream

from capabledeputy.daemon.handlers import Handler, default_handlers
from capabledeputy.ipc.rpc import (
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    RpcResponse,
    error,
    parse_request,
)


class Daemon:
    def __init__(
        self,
        socket_path: Path,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._handlers = handlers or default_handlers()
        self._shutdown_event = anyio.Event()

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def serve(self) -> None:
        with suppress(FileNotFoundError):
            self._socket_path.unlink()

        listener = await anyio.create_unix_listener(str(self._socket_path))
        try:
            os.chmod(self._socket_path, 0o600)
            async with anyio.create_task_group() as tg:

                async def _wait_shutdown() -> None:
                    await self._shutdown_event.wait()
                    tg.cancel_scope.cancel()

                tg.start_soon(_wait_shutdown)
                with suppress(anyio.ClosedResourceError):
                    await listener.serve(self._handle_connection)
        finally:
            await listener.aclose()
            with suppress(FileNotFoundError):
                self._socket_path.unlink()

    async def _handle_connection(self, stream: SocketStream) -> None:
        try:
            buf = b""
            async for chunk in stream:
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    if line.strip():
                        await self._handle_line(stream, line)
        finally:
            await stream.aclose()

    async def _handle_line(self, stream: SocketStream, line: bytes) -> None:
        try:
            request = parse_request(line)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            response = RpcResponse(
                id=None,
                error=error(PARSE_ERROR, f"parse error: {e}"),
            )
            await stream.send(response.encode())
            return

        if request.method == "shutdown":
            response = RpcResponse(id=request.id, result={"ok": True})
            await stream.send(response.encode())
            self.request_shutdown()
            return

        handler = self._handlers.get(request.method)
        if handler is None:
            response = RpcResponse(
                id=request.id,
                error=error(METHOD_NOT_FOUND, f"method not found: {request.method}"),
            )
            await stream.send(response.encode())
            return

        try:
            result = await handler(request.params)
        except Exception as e:
            response = RpcResponse(
                id=request.id,
                error=error(INTERNAL_ERROR, f"handler error: {e}"),
            )
            await stream.send(response.encode())
            return

        if request.id is not None:
            response = RpcResponse(id=request.id, result=result)
            await stream.send(response.encode())
