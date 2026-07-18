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
from typing import Any

import anyio

from capabledeputy.ipc.client import DaemonClient, DaemonNotRunningError

_MAX_BACKOFF = 2.0


async def call_with_reconnect(
    client: DaemonClient,
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
