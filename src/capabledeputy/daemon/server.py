"""Async Unix-socket JSON-RPC server with subscription support.

Handlers can register (request/response) the usual way. Connections
can additionally subscribe to named event streams; the daemon pushes
JSON-RPC notifications (no `id`) to subscribed connections as events
are emitted via `Daemon.publish(stream, payload)`.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import suppress
from pathlib import Path

import anyio
from anyio.abc import SocketStream

from capabledeputy.daemon.handlers import Handler, default_handlers
from capabledeputy.daemon.verbose_log import VerboseLogger
from capabledeputy.ipc.rpc import (
    INTERNAL_ERROR,
    JSONRPC_VERSION,
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
        verbose: bool = False,
        idle_shutdown_seconds: float | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._handlers = handlers or default_handlers()
        self._shutdown_event = anyio.Event()
        self._subscribers: dict[str, set[SocketStream]] = {}
        self._connection_streams: dict[int, set[str]] = {}
        self._sub_lock = anyio.Lock()
        self._connection_lock = anyio.Lock()
        self._active_connections = 0
        self._last_client_disconnect_at = time.monotonic()
        self._idle_shutdown_seconds = idle_shutdown_seconds
        self._verbose = VerboseLogger() if verbose else None

    def register(self, method: str, handler: Handler) -> None:
        self._handlers[method] = handler

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def publish(self, stream_name: str, payload: dict) -> None:
        """Push a JSON-RPC notification to all subscribers of the given stream."""
        async with self._sub_lock:
            streams = list(self._subscribers.get(stream_name, ()))
        if not streams:
            return
        line = (
            json.dumps(
                {
                    "jsonrpc": JSONRPC_VERSION,
                    "method": "event",
                    "params": {"stream": stream_name, "data": payload},
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for s in streams:
            try:
                await s.send(line)
            except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
                await self._unsubscribe_stream(s)

    async def _subscribe_stream(self, stream: SocketStream, name: str) -> None:
        async with self._sub_lock:
            self._subscribers.setdefault(name, set()).add(stream)
            self._connection_streams.setdefault(id(stream), set()).add(name)

    async def _unsubscribe_stream(
        self,
        stream: SocketStream,
        name: str | None = None,
    ) -> None:
        async with self._sub_lock:
            sub_names = self._connection_streams.get(id(stream), set())
            if name is not None:
                sub_names = {name} if name in sub_names else set()
            for s_name in list(sub_names):
                self._subscribers.get(s_name, set()).discard(stream)
                self._connection_streams.get(id(stream), set()).discard(s_name)
            if not self._connection_streams.get(id(stream)):
                self._connection_streams.pop(id(stream), None)

    async def snapshot(self) -> dict[str, object]:
        async with self._connection_lock:
            active_connections = self._active_connections
            last_disconnect = self._last_client_disconnect_at
        async with self._sub_lock:
            subscribers_by_stream = {
                stream_name: len(streams)
                for stream_name, streams in self._subscribers.items()
            }
            connection_subscriptions = {
                str(conn_id): sorted(streams)
                for conn_id, streams in self._connection_streams.items()
            }
        return {
            "active_connections": active_connections,
            "last_client_disconnect_at_monotonic": last_disconnect,
            "subscribers_by_stream": subscribers_by_stream,
            "connection_subscriptions": connection_subscriptions,
            "subscription_count": sum(subscribers_by_stream.values()),
        }

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
                if self._idle_shutdown_seconds is not None and self._idle_shutdown_seconds > 0:
                    tg.start_soon(self._idle_shutdown_monitor)
                with suppress(anyio.ClosedResourceError):
                    await listener.serve(self._handle_connection)
        finally:
            await listener.aclose()
            with suppress(FileNotFoundError):
                self._socket_path.unlink()

    async def _idle_shutdown_monitor(self) -> None:
        assert self._idle_shutdown_seconds is not None
        while True:
            await anyio.sleep(min(max(self._idle_shutdown_seconds / 2, 0.1), 5.0))
            async with self._connection_lock:
                active = self._active_connections
                idle_for = time.monotonic() - self._last_client_disconnect_at
            if active == 0 and idle_for >= self._idle_shutdown_seconds:
                self.request_shutdown()
                return

    async def _handle_connection(self, stream: SocketStream) -> None:
        async with self._connection_lock:
            self._active_connections += 1
        try:
            buf = b""
            async for chunk in stream:
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    if line.strip():
                        await self._handle_line(stream, line)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            # The client hung up (mid-request or mid-response). That is
            # a normal client lifecycle event — a polling TUI exiting, a
            # one-shot CLI closing — and MUST terminate only THIS
            # connection, never propagate out and take down the daemon.
            pass
        finally:
            await self._unsubscribe_stream(stream)
            async with self._connection_lock:
                self._active_connections = max(0, self._active_connections - 1)
                self._last_client_disconnect_at = time.monotonic()
            with suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
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

        if request.method == "subscribe":
            streams_to_join = request.params.get("streams") or []
            for s in streams_to_join:
                await self._subscribe_stream(stream, str(s))
            response = RpcResponse(
                id=request.id,
                result={"subscribed": list(streams_to_join)},
            )
            await stream.send(response.encode())
            return

        if request.method == "unsubscribe":
            stream_name = request.params.get("stream")
            await self._unsubscribe_stream(stream, stream_name)
            response = RpcResponse(id=request.id, result={"ok": True})
            await stream.send(response.encode())
            return

        handler = self._handlers.get(request.method)
        if handler is None:
            response = RpcResponse(
                id=request.id,
                error=error(METHOD_NOT_FOUND, f"method not found: {request.method}"),
            )
            await stream.send(response.encode())
            return

        start = time.monotonic()
        try:
            result = await handler(request.params)
        except Exception as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            if self._verbose is not None:
                self._verbose.log_error(request.method, request.params, e, elapsed_ms)
            response = RpcResponse(
                id=request.id,
                error=error(INTERNAL_ERROR, f"handler error: {e}"),
            )
            await stream.send(response.encode())
            return

        elapsed_ms = (time.monotonic() - start) * 1000
        if self._verbose is not None:
            self._verbose.log_ok(request.method, request.params, result, elapsed_ms)

        if request.id is not None:
            response = RpcResponse(id=request.id, result=result)
            await stream.send(response.encode())
