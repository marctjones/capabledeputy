"""App-level timeouts for hang-prone I/O (#320).

The upstream supervisor only reacts to detected *death*, not to a *hang*:
`litellm.acompletion`, an upstream MCP tool op, or a wedged local model can block
a turn forever. This wraps any awaitable in an `anyio.fail_after` deadline so a
hung call is cancelled and surfaced as a clear, labeled `OperationTimeoutError`
instead of stalling the daemon.

Timeouts are operator-configurable (env), with fail-safe defaults — a
non-positive / unparseable value falls back to the default, never to "no
timeout".
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import anyio

_DEFAULT_LLM_TIMEOUT = 120.0
_DEFAULT_TOOL_TIMEOUT = 60.0


class OperationTimeoutError(TimeoutError):
    """A guarded operation exceeded its deadline. Subclasses TimeoutError so
    existing `except TimeoutError` handlers keep working."""


def _env_seconds(var: str, default: float) -> float:
    raw = os.environ.get(var)
    if raw is None:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return v if v > 0 else default


def default_llm_timeout_seconds() -> float:
    """Per-call LLM timeout — `CAPDEP_LLM_TIMEOUT_SECONDS`, else 120s."""
    return _env_seconds("CAPDEP_LLM_TIMEOUT_SECONDS", _DEFAULT_LLM_TIMEOUT)


def default_tool_timeout_seconds() -> float:
    """Per-op upstream tool timeout — `CAPDEP_TOOL_TIMEOUT_SECONDS`, else 60s."""
    return _env_seconds("CAPDEP_TOOL_TIMEOUT_SECONDS", _DEFAULT_TOOL_TIMEOUT)


async def with_timeout[T](
    seconds: float,
    label: str,
    make_awaitable: Callable[[], Awaitable[T]],
) -> T:
    """Await `make_awaitable()` under a hard `seconds` deadline. On expiry the
    operation is cancelled and `OperationTimeoutError` is raised with `label`.

    Takes a factory (not a coroutine) so the awaitable is created inside the
    cancel scope — avoiding an "coroutine was never awaited" leak if construction
    itself is cheap and the deadline is generous."""
    try:
        with anyio.fail_after(seconds):
            return await make_awaitable()
    except TimeoutError as e:
        # anyio raises the builtin TimeoutError on deadline; re-label it.
        raise OperationTimeoutError(
            f"{label} exceeded its {seconds:g}s timeout and was cancelled",
        ) from e
