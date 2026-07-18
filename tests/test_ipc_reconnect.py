"""#319 — call_with_reconnect retries across a transient daemon bounce and gives
up (re-raising) after the attempt budget; real RPC errors propagate at once."""

from __future__ import annotations

import pytest

from capabledeputy.ipc.client import DaemonError, DaemonNotRunningError
from capabledeputy.ipc.reconnect import call_with_reconnect


class _FlakyClient:
    """Fails `fail_times` with DaemonNotRunningError, then returns `result`."""

    def __init__(self, fail_times: int, result: object = "ok") -> None:
        self._left = fail_times
        self._result = result
        self.calls = 0

    async def call(self, method: str, params=None):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise DaemonNotRunningError("bounced")
        return self._result


async def test_recovers_after_transient_bounce() -> None:
    client = _FlakyClient(fail_times=3, result="recovered")
    reconnects: list[int] = []
    out = await call_with_reconnect(
        client, "ping", base_delay=0.0, on_reconnect=lambda a: reconnects.append(a)
    )
    assert out == "recovered"
    assert client.calls == 4  # 3 failures + 1 success
    assert reconnects == [0, 1, 2]


async def test_gives_up_after_max_attempts() -> None:
    client = _FlakyClient(fail_times=99)
    with pytest.raises(DaemonNotRunningError):
        await call_with_reconnect(client, "ping", max_attempts=3, base_delay=0.0)
    assert client.calls == 3


async def test_real_rpc_error_propagates_immediately() -> None:
    class _Erroring:
        calls = 0

        async def call(self, method, params=None):
            self.__class__.calls += 1
            raise DaemonError("bad method (code -32601)")

    c = _Erroring()
    with pytest.raises(DaemonError, match="bad method"):
        await call_with_reconnect(c, "nope", base_delay=0.0)
    assert _Erroring.calls == 1  # no retry on a real error


async def test_first_try_success_no_retry() -> None:
    client = _FlakyClient(fail_times=0, result=42)
    assert await call_with_reconnect(client, "ping", base_delay=0.0) == 42
    assert client.calls == 1
