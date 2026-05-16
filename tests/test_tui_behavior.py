"""Layer-3 TUI behavior tests: drive the apps through Pilot and assert
the *flows* — submit reaches the daemon, a require_approval outcome
opens the verbatim modal, approving calls the daemon, a deny renders
the recovery hint. Deterministic via an injected fake client; no
socket, no new dependency."""

from __future__ import annotations

from typing import Any

from capabledeputy.tui.app import ApprovalDetailScreen, CapDepTUI
from capabledeputy.tui.console import CapDepConsole

_SID = "abcd1234-0000-0000-0000-000000000000"
_SESSION = {
    "id": _SID, "status": "active",
    "label_set": ["untrusted.external"], "used_kinds": [],
    "capability_set": [], "history": [],
}
_APPROVAL = {
    "id": 5, "action": "SEND_EMAIL", "status": "pending",
    "target": "alice@example.com", "from_session": _SID,
    "labels_in": ["confidential.financial"],
    "justification": "agent-initiated email.send",
    "payload": "Q3 numbers attached.",
}


async def _settle(pilot: Any, n: int = 6) -> None:
    for _ in range(n):
        await pilot.pause()


async def test_console_submit_reaches_daemon_and_renders(fake_daemon) -> None:
    app = CapDepConsole(_SID)
    app._client = fake_daemon(
        {
            "session.get": _SESSION,
            "session.send": {
                "content": "done", "iterations": 1,
                "finish_reason": "stop", "tool_outcomes": [],
            },
        },
    )
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await _settle(pilot)
        before = len(app.query_one("#log", RichLog).lines)
        await pilot.press(*"hello", "enter")
        await _settle(pilot)
        assert "session.send" in [m for m, _ in app._client.calls]
        assert len(app.query_one("#log", RichLog).lines) > before


async def test_console_require_approval_opens_modal_then_approves(fake_daemon) -> None:
    app = CapDepConsole(_SID)
    app._client = fake_daemon(
        {
            "session.get": _SESSION,
            "session.send": {
                "content": "needs approval", "iterations": 1,
                "finish_reason": "stop",
                "tool_outcomes": [
                    {
                        "decision": "require_approval",
                        "tool_name": "purchase.queue",
                        "rule": "financial-meets-purchase",
                        "approval_id": 5,
                    },
                ],
            },
            "approval.show": _APPROVAL,
            "approval.approve": {
                "approval": _APPROVAL,
                "executed_in_session": "ffff0000-0000-0000-0000-000000000000",
                "dispatch": {"decision": "allow"},
            },
        },
    )
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press(*"buy it", "enter")
        await _settle(pilot)
        # the runtime-queued approval auto-opens the verbatim modal
        assert isinstance(app.screen, ApprovalDetailScreen)
        assert any(m == "approval.show" for m, _ in app._client.calls)
        # approve from the modal → daemon called with the id
        await pilot.press("a")
        await _settle(pilot)
        assert ("approval.approve", {"id": 5}) in app._client.calls
        assert not isinstance(app.screen, ApprovalDetailScreen)


async def test_console_deny_renders_recovery_hint(fake_daemon) -> None:
    app = CapDepConsole(_SID)
    app._client = fake_daemon(
        {
            "session.get": _SESSION,
            "session.send": {
                "content": "blocked", "iterations": 1,
                "finish_reason": "stop",
                "tool_outcomes": [
                    {
                        "decision": "deny",
                        "tool_name": "memory.read",
                        "rule": "rate-limit-exceeded",
                        "reason": "rate limit exceeded",
                    },
                ],
            },
        },
    )
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press(*"go", "enter")
        await _settle(pilot)
        text = " ".join(s.text for s in app.query_one("#log", RichLog).lines)
        assert "recover" in text  # DENY_RECOVERY hint surfaced


async def test_spectator_open_approval_and_approve(fake_daemon) -> None:
    app = CapDepTUI(poll_interval=999.0)
    app._client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": [_APPROVAL]},
            "audit.tail": {"events": []},
            "approval.approve": {"approval": _APPROVAL},
        },
    )
    async with app.run_test() as pilot:
        await _settle(pilot)
        app.action_open_approval()
        await _settle(pilot)
        assert isinstance(app.screen, ApprovalDetailScreen)
        await pilot.press("a")
        await _settle(pilot)
        assert ("approval.approve", {"id": 5}) in app._client.calls
