"""Layer-3+ TUI integration coverage for the workflows that were the
real gap: the live security sidebar evolving, the spectator's
session-detail wiring, and drive-loop resilience. All via the shared
scriptable `fake_daemon` (state evolution, RPC failure, scripted
audit events) + Pilot. No socket, no new deps."""

from __future__ import annotations

from typing import Any

from textual.widgets import Static

from capabledeputy.ipc.client import DaemonNotRunningError
from capabledeputy.tui.app import CapDepTUI
from capabledeputy.tui.console import CapDepConsole

_SID = "abcd1234-0000-0000-0000-000000000000"


async def _settle(pilot: Any, n: int = 6) -> None:
    for _ in range(n):
        await pilot.pause()


def _text(app: Any, sel: str) -> str:
    """Plain text of a Static's current renderable (Textual 8.x:
    .render(), not .renderable)."""
    return str(app.query_one(sel, Static).render())


def _status(app: CapDepConsole) -> str:
    return _text(app, "#status")


# ---- live security sidebar evolves clean → TAINTED ---------------------

async def test_sidebar_flips_clean_to_tainted_across_a_turn(
    fake_daemon,
) -> None:
    clean = {"id": _SID, "status": "active", "label_set": [],
             "used_kinds": [], "capability_set": [], "history": []}
    tainted = {
        "id": _SID, "status": "active",
        "label_set": ["untrusted.external"],
        "used_kinds": ["READ_FS"],
        "capability_set": [
            {"kind": "QUEUE_PURCHASE", "pattern": "amazon",
             "expires_at": None,
             "rate_limit": {"max_uses": 2, "window_seconds": 60}},
        ],
        "history": [],
    }
    client = fake_daemon().sequence("session.get", [clean, tainted])
    client.set("session.send", {
        "content": "read inbox", "iterations": 1,
        "finish_reason": "stop", "tool_outcomes": [],
    })
    app = CapDepConsole(_SID)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        assert "clean" in _status(app)  # first session.get
        await pilot.press(*"summarise inbox", "enter")
        await _settle(pilot)
        s = _status(app)
        assert "TAINTED" in s  # compartment flipped, live
        assert "untrusted.external" in s
        assert "rate 2/60s" in s  # capability constraint surfaced


# ---- spectator session-detail wiring -----------------------------------

async def test_spectator_session_detail_renders_caps_and_recovery(
    fake_daemon,
) -> None:
    full = {
        "id": _SID, "status": "active", "intent": "spectator demo",
        "parent": None,
        "label_set": ["untrusted.external"],
        "capability_set": [
            {"kind": "SEND_EMAIL", "pattern": "*", "expires_at": None},
        ],
        "history": [
            {"role": "user", "content": "hi"},
            {"role": "agent", "content": "hello"},
        ],
    }
    events = [
        {
            "timestamp": "2026-05-16T12:00:00",
            "event_type": "policy.decided",
            "session_id": _SID,
            "payload": {
                "decision": "deny",
                "rule": "untrusted-meets-egress",
                "tool": "email.send",
            },
        },
    ]
    client = fake_daemon({
        "session.get": full,
        "session.list": {"sessions": [full]},
        "approval.list": {"approvals": []},
        "audit.tail": {"events": events},
    })
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await app._update_session_detail(_SID, events)
        await _settle(pilot)
        convo = _text(app, "#conversation")
        trace = _text(app, "#trace")
        assert "hello" in convo and "hi" in convo
        assert "TAINTED" in trace          # compartment block
        assert "SEND_EMAIL" in trace       # capability block
        assert "recover:" in trace         # deny recovery hint surfaced


# ---- drive-loop resilience ---------------------------------------------

async def test_console_daemon_down_is_handled(fake_daemon) -> None:
    client = fake_daemon({"session.get": {"id": _SID, "label_set": [],
                          "capability_set": [], "history": []}})
    client.raises("session.send", DaemonNotRunningError)
    app = CapDepConsole(_SID)
    app._client = client
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press(*"hi", "enter")
        await _settle(pilot)
        text = " ".join(s.text for s in app.query_one("#log", RichLog).lines)
        assert "daemon not running" in text  # surfaced, not crashed


async def test_console_rpc_error_is_handled(fake_daemon) -> None:
    client = fake_daemon({"session.get": {"id": _SID, "label_set": [],
                          "capability_set": [], "history": []}})
    client.raises("session.send", RuntimeError)
    app = CapDepConsole(_SID)
    app._client = client
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press(*"hi", "enter")
        await _settle(pilot)
        text = " ".join(s.text for s in app.query_one("#log", RichLog).lines)
        assert "rpc error" in text


async def test_console_empty_input_does_not_send(fake_daemon) -> None:
    client = fake_daemon({"session.get": {"id": _SID, "label_set": [],
                          "capability_set": [], "history": []}})
    app = CapDepConsole(_SID)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("enter")  # empty prompt
        await _settle(pilot)
        assert "session.send" not in [m for m, _ in app._client.calls]


async def test_console_audit_event_triggers_status_refresh(
    fake_daemon,
) -> None:
    client = fake_daemon({"session.get": {"id": _SID, "label_set": [],
                          "capability_set": [], "history": []}})
    client.events([
        {"stream": "audit",
         "data": {"session_id": _SID, "event_type": "label.propagated"}},
    ])
    app = CapDepConsole(_SID)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        # on_mount refresh + the scripted audit event each call session.get
        n = sum(1 for m, _ in app._client.calls if m == "session.get")
        assert n >= 2
