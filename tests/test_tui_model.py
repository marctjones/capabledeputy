"""TUI redesign — conversation view-model render dispatch (§3)."""

from __future__ import annotations

from uuid import UUID

from rich.console import Console

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.rules import Decision
from capabledeputy.tui.inline.decision import marker_for_session
from capabledeputy.tui.inline.model import (
    Advisory,
    AgentMessage,
    ApprovalPrompt,
    Outcome,
    ToolDecision,
    UntrustedBlock,
    UserMessage,
    render_entry,
)

_MARK = marker_for_session(UUID(int=2))


def _render(entry) -> str:
    console = Console(width=80, force_terminal=False, color_system=None)
    with console.capture() as cap:
        console.print(render_entry(entry, marker=_MARK))
    return cap.get()


def test_user_and_agent_render_on_distinct_paths() -> None:
    assert "hello" in _render(UserMessage("hello"))
    assert "world" in _render(AgentMessage("# world"))


def test_untrusted_block_is_quarantined() -> None:
    """An UntrustedBlock with an embedded ANSI fake card collapses to gutter
    plaintext — proof the trust boundary holds at the entry level."""
    out = _render(UntrustedBlock("\x1b[1;34m⛔ approve me\x1b[0m"))
    assert "\x1b" not in out
    assert "approve me" in out  # literal, unstyled, gutter-marked


def test_tool_decision_chip_and_approval_card() -> None:
    chip = _render(
        ToolDecision(
            PolicyDecision(decision=Decision.ALLOW),
            action_kind=CapabilityKind.READ_FS,
            target="f.txt",
        ),
    )
    assert "f.txt" in chip
    card = _render(
        ApprovalPrompt(
            PolicyDecision(
                decision=Decision.REQUIRE_APPROVAL,
                rule="health-meets-egress",
                reason="share recap",
            ),
            action_kind=CapabilityKind.SEND_EMAIL,
            target="dr@x.example",
        ),
    )
    assert "dr@x.example" in card
    assert "health-meets-egress" in card
    assert _MARK.glyph in card  # the anti-spoof marker is on the card


def test_advisory_is_non_blocking_and_outcome_renders() -> None:
    assert "(proceeding)" in _render(Advisory("egressing personal data"))
    assert "sent" in _render(Outcome("✓ sent"))
