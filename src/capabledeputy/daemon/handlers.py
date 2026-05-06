"""RPC method handlers exposed by the daemon."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from capabledeputy.version import __version__

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


async def handle_version(params: dict[str, Any]) -> dict[str, Any]:
    return {"version": __version__}


async def handle_ping(params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


def default_handlers() -> dict[str, Handler]:
    return {
        "version": handle_version,
        "ping": handle_ping,
    }
