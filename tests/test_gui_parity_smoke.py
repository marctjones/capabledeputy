"""Parity must not report interrupted, empty, or wrong turns as healthy."""

import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("status", "content", "passes"),
    [
        ("completed", "parity-ok", True),
        ("interrupted", "parity-ok", False),
        ("error", "", False),
        ("completed", "", False),
        ("completed", "something else", False),
    ],
)
async def test_chat_parity_requires_exact_success(status, content, passes) -> None:
    namespace = runpy.run_path(str(Path("scripts/verify-gui-parity.py")))

    class Client:
        def __init__(self):
            self.cancelled = False

        async def call(self, method, params):
            if method == "session.new":
                return {"id": "session"}
            if method == "session.turn.start":
                return {"turn": {"id": "turn"}}
            if method == "session.turn.cancel":
                self.cancelled = True
                return {}
            return {"turn": {"status": status, "result": {"content": content}}}

    client = Client()
    if passes:
        await namespace["_check_chat_session"](client)
        assert not client.cancelled
    else:
        with pytest.raises(RuntimeError):
            await namespace["_check_chat_session"](client)
        assert client.cancelled
