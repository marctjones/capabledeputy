"""#331 — the foreground recovery-leak repair must cover OVERRIDE_REQUIRED.

`_repair_foreground_recovery_leak` rewrites model-authored terminal-recovery
text (e.g. "type `/override ...`") into GUI-mediated language for CapDepMac
sessions. Before #331 its `blocked` set only matched DENY / REQUIRE_APPROVAL,
so an OVERRIDE_REQUIRED turn that leaked `/override` fell through to the
generic "no valid runtime recovery action" dead-end instead of pointing at the
structured control. These tests pin the OVERRIDE_REQUIRED path and guard the
existing DENY/REQUIRE_APPROVAL behavior.
"""

from __future__ import annotations

from capabledeputy.agent.loop import _repair_foreground_recovery_leak
from capabledeputy.policy.rules import Decision
from capabledeputy.session.model import Session
from capabledeputy.tools.client import ToolCallOutcome

_LEAK = "The runtime suggests you type `/override request ...` to proceed."


def _gui_session() -> Session:
    return Session.new(owner="CapDepMac")


def test_override_required_gets_structured_control_message() -> None:
    out = _repair_foreground_recovery_leak(
        _LEAK,
        session=_gui_session(),
        outcomes=[ToolCallOutcome(decision=Decision.OVERRIDE_REQUIRED, tool_name="email.send")],
    )
    # Not the dead-end generic message.
    assert "do not have a valid runtime recovery action" not in out
    # The structured-control message, now naming override.
    assert "override" in out.lower()
    assert "email.send" in out
    assert "slash commands" in out.lower()


def test_deny_still_repaired() -> None:
    out = _repair_foreground_recovery_leak(
        _LEAK,
        session=_gui_session(),
        outcomes=[ToolCallOutcome(decision=Decision.DENY, tool_name="fs.read")],
    )
    assert "fs.read" in out
    assert "do not have a valid runtime recovery action" not in out


def test_no_leak_passes_through_unchanged() -> None:
    clean = "Here is the summary you asked for."
    out = _repair_foreground_recovery_leak(
        clean,
        session=_gui_session(),
        outcomes=[ToolCallOutcome(decision=Decision.OVERRIDE_REQUIRED, tool_name="email.send")],
    )
    assert out == clean


def test_non_gui_session_passes_through_unchanged() -> None:
    out = _repair_foreground_recovery_leak(
        _LEAK,
        session=Session.new(owner="some-batch-worker"),
        outcomes=[ToolCallOutcome(decision=Decision.OVERRIDE_REQUIRED, tool_name="email.send")],
    )
    assert out == _LEAK
