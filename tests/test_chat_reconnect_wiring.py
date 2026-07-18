"""#319 — the CLI REPL (`_call`) and TUI console (`_rpc`) route one-shot RPCs
through `call_with_reconnect`, so a transient daemon bounce (a #318 supervisor
restart) recovers instead of erroring. The daemon-probe path opts out
(`reconnect=False`) so the "is it up?" check still fails fast."""

from __future__ import annotations

from typing import Any

import pytest

import capabledeputy.cli.chat as chat
from capabledeputy.ipc.client import DaemonError, DaemonNotRunningError
from capabledeputy.ipc.reconnect import SEND_RECONNECT
from capabledeputy.tui.console import CapDepConsole


class _FlakyClient:
    """Raises DaemonNotRunningError `fail_times`, then returns `result`."""

    def __init__(self, fail_times: int, result: Any = "ok") -> None:
        self._left = fail_times
        self._result = result
        self.calls = 0

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise DaemonNotRunningError("bounced")
        return self._result


class _AlwaysErrors:
    def __init__(self) -> None:
        self.calls = 0

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.calls += 1
        raise DaemonError("bad method (code -32601)")


# --- CLI: chat._call ---------------------------------------------------------


def test_cli_call_recovers_from_transient_bounce(monkeypatch: pytest.MonkeyPatch) -> None:
    flaky = _FlakyClient(fail_times=2, result={"ok": True})
    monkeypatch.setattr(chat, "_client", lambda: flaky)
    # base_delay defaults keep this well under a second (0.1 + 0.2).
    assert chat._call("ping") == {"ok": True}
    assert flaky.calls == 3  # 2 bounces + 1 success


def test_cli_call_reconnect_false_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    flaky = _FlakyClient(fail_times=99)
    monkeypatch.setattr(chat, "_client", lambda: flaky)
    with pytest.raises(DaemonNotRunningError):
        chat._call("ping", reconnect=False)
    assert flaky.calls == 1  # the probe path does not retry


def test_cli_call_real_error_propagates_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    erroring = _AlwaysErrors()
    monkeypatch.setattr(chat, "_client", lambda: erroring)
    with pytest.raises(DaemonError, match="bad method"):
        chat._call("nope")
    assert erroring.calls == 1  # a real RPC error is not a bounce


# --- TUI: CapDepConsole._rpc -------------------------------------------------


async def test_tui_rpc_recovers_from_transient_bounce() -> None:
    console = CapDepConsole("session-1")
    console._client = _FlakyClient(fail_times=2, result="recovered")  # type: ignore[assignment]
    # _safe_log's query_one fails outside a running app; it swallows that, so
    # the reconnect notice never crashes the RPC.
    out = await console._rpc("session.get", {"session_id": "session-1"})
    assert out == "recovered"
    assert console._client.calls == 3  # type: ignore[attr-defined]


async def test_tui_rpc_real_error_propagates_without_retry() -> None:
    console = CapDepConsole("session-1")
    console._client = _AlwaysErrors()  # type: ignore[assignment]
    with pytest.raises(DaemonError, match="bad method"):
        await console._rpc("nope")
    assert console._client.calls == 1  # type: ignore[attr-defined]


async def test_send_budget_recovers_a_single_blip_transparently() -> None:
    # The headline #319 case: a one-shot bounce right as the user sends recovers
    # transparently under the short SEND budget (one sub-second retry).
    console = CapDepConsole("session-1")
    flaky = _FlakyClient(fail_times=1, result={"content": "done"})
    console._client = flaky  # type: ignore[assignment]
    out = await console._rpc("session.send", {"message": "hi"}, budget=SEND_RECONNECT)
    assert out == {"content": "done"}
    assert flaky.calls == 2  # 1 blip + 1 success


async def test_send_budget_gives_up_fast_on_a_real_outage() -> None:
    # A persistently-down daemon surfaces after the SEND budget (2 attempts),
    # not the full ambient budget — no long UI hang.
    console = CapDepConsole("session-1")
    flaky = _FlakyClient(fail_times=99)
    console._client = flaky  # type: ignore[assignment]
    with pytest.raises(DaemonNotRunningError):
        await console._rpc("session.send", {"message": "hi"}, budget=SEND_RECONNECT)
    assert flaky.calls == SEND_RECONNECT["max_attempts"]  # bounded, small
