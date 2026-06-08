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

import contextlib
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import AnyUrl

if TYPE_CHECKING:
    from capabledeputy.upstream.config import UpstreamServerConfig


class UpstreamDead(RuntimeError):  # noqa: N818 (descriptive domain exception)
    """Raised when an upstream session is dead and the respawn attempt
    also failed. The caller should treat the upstream as unavailable
    for the duration of the daemon process unless something else
    changes (operator restart, config update)."""


def _looks_like_session_death(exc: BaseException) -> bool:
    """Heuristic: is this exception consistent with the subprocess
    dying / pipe broken / stream closed?

    Conservative: when in doubt, return True. The cost of a false
    positive is one respawn (we kill a possibly-fine session and
    bring it back); the cost of a false negative is a stuck upstream.
    We err toward the cheap mistake.

    Known-safe exceptions we DO NOT want to interpret as death:
      - mcp.shared.exceptions.McpError with `code != -32000` is a
        protocol-level error (tool not found, bad args). Don't tear
        down the session for these.
    """
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
    # MCP protocol errors that AREN'T death
    if name == "McpError":
        code = getattr(exc, "code", None)
        # -32603 (internal error) tends to mean the upstream blew up.
        return code in (None, -32000, -32603)
    # Unknown — treat as candidate death.
    return True


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

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def is_alive(self) -> bool:
        return self._session is not None

    async def start(self) -> None:
        """Initial spawn. Raises on failure (no retry). Subsequent
        respawn-on-death attempts handle their own backoff."""
        await self._spawn()

    async def stop(self) -> None:
        """Tear down. Safe to call multiple times."""
        if self._stack is not None:
            # During shutdown, swallow tear-down errors so we don't
            # mask the more important shutdown.
            with contextlib.suppress(Exception):
                await self._stack.__aexit__(None, None, None)
            self._stack = None
            self._session = None

    async def _spawn(self) -> None:
        import os

        cmd = self._config.effective_command()
        merged_env: dict[str, str] = dict(os.environ)
        merged_env.update(self._config.env)
        params = StdioServerParameters(
            command=cmd[0],
            args=list(cmd[1:]),
            env=merged_env if self._config.env else None,
        )
        stack = AsyncExitStack()
        try:
            await stack.__aenter__()
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception:
            with contextlib.suppress(Exception):
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
            with contextlib.suppress(Exception):
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

    # ---- Quack-like-ClientSession proxies. Adapter calls these. ----

    async def initialize(self) -> Any:
        return await self._with_retry(lambda s: s.initialize())

    async def list_tools(self) -> Any:
        return await self._with_retry(lambda s: s.list_tools())

    async def list_resources(self) -> Any:
        return await self._with_retry(lambda s: s.list_resources())

    async def read_resource(self, uri: AnyUrl) -> Any:
        # Mirrors mcp.ClientSession.read_resource(uri: AnyUrl) so SessionLike
        # (ClientSession | LiveSession) is a coherent union for callers.
        return await self._with_retry(lambda s: s.read_resource(uri))

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await self._with_retry(
            lambda s: s.call_tool(name, arguments=arguments),
        )
