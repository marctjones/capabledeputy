"""TUI redesign — the scripted demo driver produces the full showcase turn."""

from __future__ import annotations

from typing import Any

from capabledeputy.tui.inline.demo import DemoDriver
from capabledeputy.tui.inline.model import (
    ApprovalPrompt,
    Outcome,
    ToolDecision,
    UntrustedBlock,
)


class _RecordingConsole:
    def __init__(self, choice: str = "approve") -> None:
        self.entries: list[Any] = []
        self.trust: list[Any] = []
        self.requested: list[ApprovalPrompt] = []
        self._choice = choice

    def append(self, entry: Any) -> None:
        self.entries.append(entry)

    def set_trust(self, trust: Any) -> None:
        self.trust.append(trust)

    async def request_decision(self, prompt: ApprovalPrompt) -> str:
        self.requested.append(prompt)
        return self._choice


async def test_demo_full_turn_emits_every_surface_and_requests_approval() -> None:
    rec = _RecordingConsole("approve")
    await DemoDriver(delay=0).run_turn("recap my labs", rec)
    types = {type(e).__name__ for e in rec.entries}
    assert {"ToolDecision", "UntrustedBlock", "Advisory", "Outcome"} <= types
    # exactly one gated action, on the real engine floor:
    assert len(rec.requested) == 1
    assert rec.requested[0].decision.rule == "health-meets-egress"
    # the session tainted (status updated):
    assert rec.trust
    # approved → sent:
    assert any(isinstance(e, Outcome) and "sent" in e.text for e in rec.entries)


async def test_demo_deny_path_does_not_send() -> None:
    rec = _RecordingConsole("deny")
    await DemoDriver(delay=0).run_turn("x", rec)
    assert any(isinstance(e, Outcome) and "not sent" in e.text for e in rec.entries)


def test_untrusted_block_in_the_demo_carries_an_injection() -> None:
    """The showcase deliberately includes an injection in the untrusted block;
    it is just data to the model and is quarantine-rendered by the UI."""
    rec = _RecordingConsole()
    import asyncio

    asyncio.run(DemoDriver(delay=0).run_turn("x", rec))
    blocks = [e for e in rec.entries if isinstance(e, ToolDecision | UntrustedBlock)]
    raw = next(e.raw for e in blocks if isinstance(e, UntrustedBlock))
    assert "SYSTEM OVERRIDE" in raw  # the injection is present as content
