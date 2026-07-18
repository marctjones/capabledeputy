"""#319 — reconnect-with-backoff for daemon RPC calls.

A daemon restart or socket-move mid-session otherwise surfaces a raw
`DaemonNotRunningError`. `call_with_reconnect` retries a call across that error
with exponential backoff up to a bounded number of attempts, so a transient
daemon bounce recovers gracefully instead of confusing the operator. Any
non-transient error (a real RPC error) propagates immediately.

This is the reconnect PRIMITIVE; wiring it into the REPL loop / TUI / Swift
client's subscribe+resubscribe path is the integration layer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import anyio

from capabledeputy.ipc.client import DaemonNotRunningError

_MAX_BACKOFF = 2.0

# #319 reconnect budgets for the interactive clients (CLI REPL / TUI console).
# AMBIENT — status/list/navigation queries: a few seconds to ride out a
#   transient bounce transparently, then surface (worst case ~3.1s).
# SEND — the user's explicit message-send: ONE sub-second retry, so a
#   socket-move / fast-restart blip recovers transparently while a real outage
#   surfaces in ~150ms rather than hanging the UI on the full budget.
AMBIENT_RECONNECT = {"max_attempts": 6, "base_delay": 0.1}
SEND_RECONNECT = {"max_attempts": 2, "base_delay": 0.15}


class RpcCaller(Protocol):
    """Anything with an async `call(method, params)` — `DaemonClient` and test
    doubles both satisfy this structurally."""

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any: ...


async def call_with_reconnect(
    client: RpcCaller,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    max_attempts: int = 8,
    base_delay: float = 0.1,
    on_reconnect: Callable[[int], Awaitable[None] | None] | None = None,
) -> Any:
    """Call `client.call(method, params)`, retrying on `DaemonNotRunningError`
    (the daemon is restarting / the socket moved) with exponential backoff.

    Retries up to `max_attempts` times; the final attempt's failure is re-raised.
    `on_reconnect(attempt)` is invoked before each retry (e.g. to print a
    'reconnecting…' notice). A non-transient `DaemonError` propagates at once."""
    last: DaemonNotRunningError | None = None
    for attempt in range(max_attempts):
        try:
            return await client.call(method, params)
        except DaemonNotRunningError as e:
            last = e
            if attempt == max_attempts - 1:
                break
            if on_reconnect is not None:
                result = on_reconnect(attempt)
                if result is not None:
                    await result
            await anyio.sleep(min(base_delay * (2**attempt), _MAX_BACKOFF))
    assert last is not None
    raise last
