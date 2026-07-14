"""Per-upstream session supervisor with crash recovery.

`LiveSession` wraps the MCP `ClientSession` + the stdio subprocess
that backs it. The adapter sees a session-like object whose methods
(`initialize`, `list_tools`, `call_tool`, `list_resources`,
`read_resource`) work just like `ClientSession`'s — but on detected
session death, the supervisor transparently respawns the subprocess
and retries the failing call once. Persistent failures surface as
ordinary exceptions to the caller.

Why this matters: MCP servers are subprocesses. They die — OOM, a
gmail rate-limit response that crashes the SDK, an upstream that
panics on bad input. Without a supervisor, the first death means
"that whole upstream is gone until the daemon restarts," which makes
the daemon brittle in normal operation.

Design notes:
  - One LiveSession per upstream config. Each owns its own inner
    `AsyncExitStack` so a respawn can tear down + rebuild just that
    upstream's resources without touching the manager-level stack.
  - Respawn is serialized per-upstream via a per-instance lock so
    concurrent callers piggy-back on a single fresh session.
  - Backoff: exponential, capped at `max_backoff_seconds`. Reset to
    zero on a successful spawn.
  - Retry: at most one retry per call. If the first retry also fails,
    raise — we don't infinite-loop on a broken server.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anyio
import anyio.abc
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

if TYPE_CHECKING:
    from capabledeputy.upstream.config import UpstreamServerConfig


_DEFAULT_UPSTREAM_ENV_ALLOWLIST = frozenset(
    {
        # Process discovery and normal user-local tool caches.
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        # Locale and temp dirs. Many Node/Python CLIs assume these exist.
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TMPDIR",
        "TEMP",
        "TMP",
        # TLS trust roots. These are paths, not credentials, and are needed by
        # enterprise-managed machines and custom trust stores.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    },
)


def build_stdio_env(
    config: UpstreamServerConfig,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment for a stdio MCP upstream.

    Subprocesses must not inherit the daemon's full process environment:
    hosted-model keys, local service tokens, and CapDep internals would
    otherwise be visible to any upstream server that can read its own env.
    The daemon passes only mundane process-bootstrap variables plus the
    operator-approved per-server env, including credential-vault injections.
    """

    source = os.environ if environ is None else environ
    env = {name: value for name in _DEFAULT_UPSTREAM_ENV_ALLOWLIST if (value := source.get(name))}
    env.update(config.env)
    return env


class UpstreamDead(RuntimeError):  # noqa: N818 (descriptive domain exception)
    """Raised when an upstream session is dead and the respawn attempt
    also failed. The caller should treat the upstream as unavailable
    for the duration of the daemon process unless something else
    changes (operator restart, config update)."""


class UpstreamCallFailed(RuntimeError):  # noqa: N818 (descriptive domain exception)
    """Raised when an upstream tool call failed without transport death.

    Streamable-http MCP clients often surface remote HTTP/MCP failures
    as task cancellation (`asyncio.CancelledError`), which is a
    `BaseException` and must not be mistaken for session death or
    allowed to propagate uncaught through RPC handlers."""


def _is_task_cancellation(exc: BaseException) -> bool:
    """True when `exc` is asyncio/anyio task cancellation, not death."""
    if isinstance(exc, asyncio.CancelledError):
        return True
    # anyio.CancelledError is a distinct type on some backends.
    return type(exc).__name__ == "CancelledError"


def _normalize_call_failure(exc: BaseException, upstream_name: str) -> BaseException:
    """Convert non-death call failures into catchable `Exception`s."""
    if _is_task_cancellation(exc):
        return UpstreamCallFailed(
            f"upstream {upstream_name!r} request cancelled (remote error?)",
        )
    return exc


def _looks_like_session_death(exc: BaseException) -> bool:
    """Heuristic: is this exception consistent with the subprocess
    dying / pipe broken / stream closed?

    Conservative for transport failures, but **not** for ordinary
    remote HTTP/MCP errors. A 401/403 from Google's Gmail MCP is an
    auth/permission problem — respawning the local session cannot fix
    it and tearing down the streamable-http client during an in-flight
    RPC has been observed to take down the whole daemon task group.

    Known-safe exceptions we DO NOT want to interpret as death:
      - mcp.shared.exceptions.McpError with `code != -32000` is a
        protocol-level error (tool not found, bad args). Don't tear
        down the session for these.
      - httpx.HTTPStatusError — the remote server responded.
      - asyncio/anyio CancelledError — remote HTTP failures in the MCP
        streamable-http client cancel sibling tasks; not transport death.
      - UpstreamCallFailed — normalized non-death call failure.
      - ExceptionGroup/BaseExceptionGroup — inspect sub-exceptions; only
        death if at least one sub-exception looks like transport death.
    """
    if isinstance(exc, UpstreamCallFailed):
        return False
    if _is_task_cancellation(exc):
        return False
    if isinstance(exc, ExceptionGroup):
        if not exc.exceptions:
            return False
        return any(_looks_like_session_death(sub) for sub in exc.exceptions)

    name = type(exc).__name__
    # Anyio stream errors during read/write to the subprocess
    if name in (
        "BrokenResourceError",
        "ClosedResourceError",
        "EndOfStream",
        "ConnectionResetError",
        "BrokenPipeError",
    ):
        return True
    # Generic pipe / process errors
    if isinstance(exc, OSError):
        return True
    # Remote HTTP MCP servers answered with an HTTP status — not death.
    if name == "HTTPStatusError":
        return False
    # MCP protocol errors that AREN'T death
    if name == "McpError":
        code = getattr(exc, "code", None)
        # -32603 (internal error) tends to mean the upstream blew up.
        return code in (None, -32000, -32603)
    # Unknown — treat as candidate death.
    return True


@dataclass
class _OwnerRequest:
    coro: Callable[[], Awaitable[Any]]
    done: anyio.Event = field(default_factory=anyio.Event)
    result: Any = None
    error: BaseException | None = None


class LiveSession:
    """Crash-recovering wrapper around an MCP `ClientSession`.

    Lifecycle:
      - Construct (no spawn).
      - `await start()` — initial spawn. Raises on first-time failure.
      - Use as a session: `await live.call_tool(...)`.
      - `await stop()` — tear down. Idempotent.

    Thread / task safety: methods can be called concurrently from
    multiple anyio tasks. The respawn path is serialized; concurrent
    callers either piggy-back on a fresh session or fail with
    `UpstreamDead` if respawn itself failed.
    """

    def __init__(
        self,
        config: UpstreamServerConfig,
        *,
        max_backoff_seconds: float = 30.0,
        spawn_logger: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._max_backoff_seconds = max_backoff_seconds
        self._spawn_logger = spawn_logger or (lambda msg: None)
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._respawn_lock = anyio.Lock()
        self._consecutive_failures = 0
        self._last_failure_at = 0.0
        # streamable-http MCP clients must be spawned and called from the
        # same task — otherwise cancel scopes tear down the daemon.
        self._owner_stack: AsyncExitStack | None = None
        self._owner_tg: anyio.abc.TaskGroup | None = None
        self._ready = anyio.Event()
        self._req_send: anyio.abc.ObjectSendStream[_OwnerRequest] | None = None
        self._req_recv: anyio.abc.ObjectReceiveStream[_OwnerRequest] | None = None

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def is_alive(self) -> bool:
        return self._session is not None

    async def start(self) -> None:
        """Initial spawn. Raises on failure (no retry). Subsequent
        respawn-on-death attempts handle their own backoff."""
        if self._config.transport == "streamable_http":
            self._req_send, self._req_recv = anyio.create_memory_object_stream[_OwnerRequest](32)
            self._owner_stack = AsyncExitStack()
            await self._owner_stack.__aenter__()
            self._owner_tg = await self._owner_stack.enter_async_context(
                anyio.create_task_group(),
            )
            self._owner_tg.start_soon(self._owner_loop)
            await self._ready.wait()
            return
        await self._spawn()

    async def stop(self) -> None:
        """Tear down. Safe to call multiple times."""
        if self._req_send is not None:
            with contextlib.suppress(BaseException):
                await self._req_send.aclose()
            self._req_send = None
            self._req_recv = None
        if self._owner_stack is not None:
            with contextlib.suppress(BaseException):
                await self._owner_stack.__aexit__(None, None, None)
            self._owner_stack = None
            self._owner_tg = None
            self._stack = None
            self._session = None
            return
        if self._stack is not None:
            # During shutdown, swallow tear-down errors so we don't
            # mask the more important shutdown.
            with contextlib.suppress(BaseException):
                await self._stack.__aexit__(None, None, None)
            self._stack = None
            self._session = None

    async def _owner_loop(self) -> None:
        """Own streamable-http MCP I/O in one task (spawn + calls)."""
        try:
            await self._spawn()
            self._ready.set()
            assert self._req_recv is not None
            async for req in self._req_recv:
                try:
                    req.result = await req.coro()
                except BaseException as e:
                    req.error = _normalize_call_failure(e, self._config.name)
                req.done.set()
        finally:
            if self._stack is not None:
                with contextlib.suppress(BaseException):
                    await self._stack.__aexit__(None, None, None)
                self._stack = None
                self._session = None

    async def _dispatch(self, coro: Callable[[], Awaitable[Any]]) -> Any:
        if self._config.transport != "streamable_http":
            return await coro()
        assert self._req_send is not None
        req = _OwnerRequest(coro=coro)
        await self._req_send.send(req)
        await req.done.wait()
        if req.error is not None:
            raise req.error
        return req.result

    async def _spawn(self) -> None:
        stack = AsyncExitStack()
        try:
            await stack.__aenter__()
            if self._config.transport == "stdio":
                cmd = self._config.effective_command()
                params = StdioServerParameters(
                    command=cmd[0],
                    args=list(cmd[1:]),
                    env=build_stdio_env(self._config),
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif self._config.transport == "streamable_http":
                from mcp.client.streamable_http import streamablehttp_client

                from capabledeputy.upstream.http_auth import httpx_auth_from_config

                read, write, _get_session_id = await stack.enter_async_context(
                    streamablehttp_client(
                        self._config.url,
                        headers=self._config.headers or None,
                        auth=httpx_auth_from_config(
                            self._config.auth,
                            server_name=self._config.name,
                        ),
                    ),
                )
            else:  # pragma: no cover - config parser rejects this.
                raise ValueError(f"unsupported transport: {self._config.transport}")
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception:
            with contextlib.suppress(BaseException):
                await stack.__aexit__(None, None, None)
            raise
        self._stack = stack
        self._session = session
        if self._consecutive_failures > 0:
            self._spawn_logger(
                f"[supervisor] {self._config.name!r} respawned after "
                f"{self._consecutive_failures} failure(s)",
            )
        self._consecutive_failures = 0

    async def _respawn_with_backoff(self) -> None:
        """Tear down (if any) + sleep proportional to consecutive
        failures + spawn. Called under `_respawn_lock`."""
        if self._stack is not None:
            with contextlib.suppress(BaseException):
                await self._stack.__aexit__(None, None, None)
        self._stack = None
        self._session = None

        # Exponential backoff: 1, 2, 4, 8, 16, capped.
        now = time.monotonic()
        self._consecutive_failures += 1
        wait = min(
            2 ** (self._consecutive_failures - 1),
            self._max_backoff_seconds,
        )
        elapsed = now - self._last_failure_at
        if elapsed < wait:
            self._spawn_logger(
                f"[supervisor] {self._config.name!r} backing off "
                f"{wait - elapsed:.1f}s before respawn (failures="
                f"{self._consecutive_failures})",
            )
            await anyio.sleep(wait - elapsed)
        self._last_failure_at = time.monotonic()
        await self._spawn()

    async def _with_retry(self, op: Callable[[ClientSession], Awaitable[Any]]) -> Any:
        """Run `op(session)`. On detected death, respawn + retry once."""
        pre_session = self._session
        if pre_session is None:
            # Nothing alive — kick a respawn before trying.
            async with self._respawn_lock:
                if self._session is None:
                    try:
                        await self._respawn_with_backoff()
                    except Exception as e:
                        raise UpstreamDead(
                            f"upstream {self._config.name!r} is down and respawn failed: {e}",
                        ) from e
            assert self._session is not None
            return await op(self._session)

        try:
            return await op(pre_session)
        except Exception as e:
            if not _looks_like_session_death(e):
                raise
            # Try a single respawn + retry. Serialized so concurrent
            # callers piggy-back.
            async with self._respawn_lock:
                if self._session is pre_session:
                    # Still pointing at the dead session; bring up a new one.
                    self._spawn_logger(
                        f"[supervisor] {self._config.name!r} appears dead "
                        f"({type(e).__name__}: {e}); respawning",
                    )
                    try:
                        await self._respawn_with_backoff()
                    except Exception as respawn_err:
                        raise UpstreamDead(
                            f"upstream {self._config.name!r} died and respawn "
                            f"failed: {respawn_err}",
                        ) from e
            assert self._session is not None
            try:
                return await op(self._session)
            except Exception as retry_err:
                # Retry also failed. Don't loop: surface so the caller
                # can decide how to handle it.
                raise UpstreamDead(
                    f"upstream {self._config.name!r} respawned but the retry "
                    f"call also failed: {type(retry_err).__name__}: {retry_err}",
                ) from retry_err
        except BaseException as e:
            # CancelledError is BaseException, not Exception — it bypasses
            # the death heuristic and has been observed to kill the daemon
            # task group when allowed to propagate from tool.call handlers.
            normalized = _normalize_call_failure(e, self._config.name)
            if (
                isinstance(normalized, UpstreamCallFailed)
                and self._config.transport == "streamable_http"
            ):
                # A 401/cancel leaves the streamable-http transport wedged;
                # respawn in the owner task so later calls can proceed.
                with contextlib.suppress(BaseException):
                    await self._respawn_with_backoff()
            raise normalized from e

    # ---- Quack-like-ClientSession proxies. Adapter calls these. ----

    async def initialize(self) -> Any:
        return await self._dispatch(
            lambda: self._with_retry(lambda s: s.initialize()),
        )

    async def list_tools(self) -> Any:
        return await self._dispatch(
            lambda: self._with_retry(lambda s: s.list_tools()),
        )

    async def list_resources(self) -> Any:
        return await self._dispatch(
            lambda: self._with_retry(lambda s: s.list_resources()),
        )

    async def read_resource(self, uri: AnyUrl) -> Any:
        # Mirrors mcp.ClientSession.read_resource(uri: AnyUrl) so SessionLike
        # (ClientSession | LiveSession) is a coherent union for callers.
        return await self._dispatch(
            lambda: self._with_retry(lambda s: s.read_resource(uri)),
        )

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self._dispatch(
            lambda: self._with_retry(
                lambda s: s.call_tool(name, arguments=arguments),
            ),
        )
