"""Shared test fixtures.

`fake_daemon` is the scriptable async daemon client the TUI tests
drive Pilot against — no socket, fully deterministic. It can model
*state evolution* (successive calls returning different values), RPC
failures, and a scripted audit event stream, which the earlier
one-shot fake could not. This is the enabling fixture that makes
extending TUI coverage cheap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest


class ScriptedClient:
    """Async stand-in for `ipc.client.DaemonClient`.

    Per-method scripting:
      - `.set(method, value)`         → always returns `value`
      - `.sequence(method, [v1, v2])` → returns v1 then v2 then v2…
                                         (last value repeats)
      - `.raises(method, exc)`        → raises `exc` when called
      - `.respond(method, fn)`        → returns `fn(params)`
    `.events([...])` scripts what `subscribe()` yields (each item is
    the raw `{"stream","data"}` envelope the TUIs expect), then ends.
    `.calls` records every (method, params) for assertions.

    A plain `ScriptedClient({...})` mirrors the old one-shot fake so
    existing tests port with a one-line change.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._scripts: dict[str, Any] = dict(responses or {})
        self._seq: dict[str, list[Any]] = {}
        self._raise: dict[str, BaseException | type[BaseException]] = {}
        self._fn: dict[str, Any] = {}
        self._events: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def set(self, method: str, value: Any) -> ScriptedClient:
        self._scripts[method] = value
        return self

    def sequence(self, method: str, values: list[Any]) -> ScriptedClient:
        self._seq[method] = list(values)
        return self

    def raises(
        self, method: str, exc: BaseException | type[BaseException],
    ) -> ScriptedClient:
        self._raise[method] = exc
        return self

    def respond(self, method: str, fn: Any) -> ScriptedClient:
        self._fn[method] = fn
        return self

    def events(self, evs: list[dict[str, Any]]) -> ScriptedClient:
        self._events = list(evs)
        return self

    async def call(
        self, method: str, params: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, params))
        if method in self._raise:
            exc = self._raise[method]
            raise exc if isinstance(exc, BaseException) else exc("scripted")
        if method in self._seq:
            q = self._seq[method]
            return q.pop(0) if len(q) > 1 else (q[0] if q else {})
        if method in self._fn:
            return self._fn[method](params)
        return self._scripts.get(method, {})

    async def subscribe(self, streams: list[str]) -> AsyncIterator[dict]:
        evs = list(self._events)

        async def _gen() -> AsyncIterator[dict]:
            for e in evs:
                yield e

        return _gen()


@pytest.fixture
def fake_daemon():
    """Returns the ScriptedClient class; a test builds and configures
    its own instance: `c = fake_daemon({...})` or
    `c = fake_daemon().sequence("session.get", [a, b])`."""
    return ScriptedClient
