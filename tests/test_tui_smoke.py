"""Smoke tests for the Textual surfaces via App.run_test().

Closes the long-standing "the TUIs are logic-tested but never booted
in a harness" gap. The shared `fake_daemon` (conftest) client is
injected so these are deterministic and need no socket. Scope is
intentionally a smoke test: the app mounts, the expected widgets
exist, on_mount workers run without crashing, and the console's
drive path reaches the daemon. Pure formatting/selection logic is
covered separately (test_tui_console_model, test_presentation)."""

from __future__ import annotations

import pytest

from capabledeputy.tui.app import CapDepTUI
from capabledeputy.tui.console import CapDepConsole

_SESSION = {
    "id": "abcd1234-0000-0000-0000-000000000000",
    "status": "active",
    "label_set": ["untrusted.external"],
    "used_kinds": ["READ_FS"],
    "capability_set": [
        {"kind": "SEND_EMAIL", "pattern": "*", "expires_at": None},
    ],
    "history": [],
}


async def test_console_boots_and_renders_status(fake_daemon) -> None:
    app = CapDepConsole(_SESSION["id"])
    app._client = fake_daemon({"session.get": _SESSION})
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#log") is not None
        assert app.query_one("#status") is not None
        assert app.query_one("#prompt") is not None
        assert any(m == "session.get" for m, _ in app._client.calls)


async def test_console_input_drives_session_send(fake_daemon) -> None:
    app = CapDepConsole(_SESSION["id"])
    app._client = fake_daemon(
        {
            "session.get": _SESSION,
            "session.send": {
                "content": "hello back",
                "iterations": 1,
                "finish_reason": "stop",
                "tool_outcomes": [],
            },
        },
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*"hi there")
        await pilot.press("enter")
        await pilot.pause()
        assert "session.send" in [m for m, _ in app._client.calls]


async def test_spectator_tui_boots_without_daemon_crash(fake_daemon) -> None:
    app = CapDepTUI(poll_interval=999.0)  # no re-poll during the test
    app._client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": []},
        },
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#sessions") is not None
        assert app.query_one("#approvals") is not None
        assert app.query_one("#events") is not None
        assert any(m == "session.list" for m, _ in app._client.calls)


@pytest.mark.parametrize("app_factory", [
    lambda: CapDepConsole("x-session"),
])
async def test_console_quit_binding_exits(app_factory, fake_daemon) -> None:
    app = app_factory()
    app._client = fake_daemon({"session.get": _SESSION})
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
    # exiting the context without hang/raise is the assertion
