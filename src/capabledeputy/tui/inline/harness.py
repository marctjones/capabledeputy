"""Automation harness for the inline console.

Makes it trivial to write automated scripts that test the server (via a real
driver) and the UI behavior (via the typed transcript), with no terminal:

    from capabledeputy.tui.inline.harness import ConsoleHarness, approve_all
    from capabledeputy.tui.inline.demo import DemoDriver

    h = ConsoleHarness(DemoDriver(delay=0), decide=approve_all)
    await h.send("recap my labs")
    assert h.decisions()[0].rule == "health-meets-egress"
    assert "sent" in h.events("outcome")[-1].text
    assert all("\x1b" not in e.text for e in h.events("untrusted"))  # quarantine held

`HeadlessConsole` records every driver call as a structured `TranscriptEvent`
(not scraped pixels), so assertions are robust. `decide` is a pluggable policy
that auto-answers gated decisions, so a script never hangs on a prompt. To test
the full stack, pass a real daemon-backed driver instead of `DemoDriver` — the
harness is unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from capabledeputy.policy.capabilities import kind_name
from capabledeputy.tui.inline.decision import SessionMarker, marker_for_session
from capabledeputy.tui.inline.model import (
    Advisory,
    AgentMessage,
    ApprovalPrompt,
    Entry,
    Outcome,
    ToolDecision,
    UntrustedBlock,
    UserMessage,
)
from capabledeputy.tui.inline.render import quarantine
from capabledeputy.tui.inline.status import TrustState

# A decider maps a gated prompt to a choice: "approve" | "deny" | "override".
Decider = Callable[[ApprovalPrompt], str]


def approve_all(_prompt: ApprovalPrompt) -> str:
    return "approve"


def deny_all(_prompt: ApprovalPrompt) -> str:
    return "deny"


def by_rule(table: dict[str, str], *, default: str = "deny") -> Decider:
    """Decide by the engine rule/floor that fired — e.g.
    by_rule({"health-meets-egress": "approve"})."""

    def decide(prompt: ApprovalPrompt) -> str:
        return table.get(prompt.decision.rule or "", default)

    return decide


@dataclass(frozen=True)
class TranscriptEvent:
    """One structured, assertable record of a driver/UI interaction."""

    kind: str  # user|agent|untrusted|tool|advisory|decision|resolved|outcome|trust
    text: str  # a normalized plaintext summary (escape-free)
    decision: str | None = None  # the Decision value, for tool/decision events
    rule: str | None = None  # the floor/rule that fired
    target: str | None = None
    choice: str | None = None  # the decider's answer, for resolved events


def _event_for(entry: Entry) -> TranscriptEvent:
    if isinstance(entry, UserMessage):
        return TranscriptEvent("user", entry.text)
    if isinstance(entry, AgentMessage):
        return TranscriptEvent("agent", entry.markdown)
    if isinstance(entry, UntrustedBlock):
        # capture the QUARANTINED text so a script can assert it's escape-free
        return TranscriptEvent("untrusted", quarantine(entry.raw).plain)
    if isinstance(entry, ToolDecision):
        return TranscriptEvent(
            "tool",
            f"{kind_name(entry.action_kind)}({entry.target})",
            decision=entry.decision.decision.value,
            rule=entry.decision.rule,
            target=entry.target,
        )
    if isinstance(entry, Advisory):
        return TranscriptEvent("advisory", entry.text)
    if isinstance(entry, Outcome):
        return TranscriptEvent("outcome", entry.text)
    # ApprovalPrompt (rare on append; request_decision records it richly)
    assert isinstance(entry, ApprovalPrompt)
    return TranscriptEvent(
        "decision",
        entry.target,
        decision=entry.decision.decision.value,
        rule=entry.decision.rule,
        target=entry.target,
    )


@dataclass
class HeadlessConsole:
    """A no-terminal ConsoleView that records a structured transcript and
    auto-answers gated decisions via `decide`."""

    decide: Decider = approve_all
    marker: SessionMarker = field(default_factory=lambda: marker_for_session(uuid4()))
    transcript: list[TranscriptEvent] = field(default_factory=list)

    def append(self, entry: Entry) -> None:
        self.transcript.append(_event_for(entry))

    def set_trust(self, trust: TrustState) -> None:
        tainted = trust.labels is not None and bool(trust.labels.a or trust.labels.b)
        self.transcript.append(
            TranscriptEvent(
                "trust",
                f"{trust.session_name} purpose:{trust.purpose or '—'}",
                decision="tainted" if tainted else None,
            ),
        )

    async def request_decision(self, prompt: ApprovalPrompt) -> str:
        self.transcript.append(
            TranscriptEvent(
                "decision",
                prompt.target,
                decision=prompt.decision.decision.value,
                rule=prompt.decision.rule,
                target=prompt.target,
            ),
        )
        choice = self.decide(prompt)
        self.transcript.append(TranscriptEvent("resolved", choice, choice=choice))
        return choice


class ConsoleHarness:
    """Run a driver against a headless console and assert on the transcript.

    The driver is whatever you want to test: `DemoDriver` for UI logic, or a
    real daemon-backed driver for the full server+UI stack.
    """

    def __init__(
        self, driver: object, *, decide: Decider = approve_all, session_id: UUID | None = None
    ) -> None:
        self._driver = driver
        self.console = HeadlessConsole(
            decide=decide,
            marker=marker_for_session(session_id or uuid4()),
        )

    async def send(self, text: str) -> None:
        await self._driver.run_turn(text, self.console)  # type: ignore[attr-defined]

    @property
    def transcript(self) -> list[TranscriptEvent]:
        return self.console.transcript

    def events(self, kind: str) -> list[TranscriptEvent]:
        return [e for e in self.transcript if e.kind == kind]

    def decisions(self) -> list[TranscriptEvent]:
        return self.events("decision")
