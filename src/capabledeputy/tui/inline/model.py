"""Conversation view-model for the inline console (TUI redesign §3).

The conversation is a sequence of typed entries the app renders into its log.
Keeping entries typed (not pre-rendered strings) preserves the trust boundary:
agent prose and untrusted tool output are *different entry types* with
*different render paths* (AgentMessage → Markdown; UntrustedBlock → quarantine),
and decisions render only from a typed PolicyDecision. There is no entry type
through which model text reaches a decision surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.tui.inline.decision import (
    SessionMarker,
    decision_chip,
    render_card,
)
from capabledeputy.tui.inline.glyphs import (
    GLYPH_PROMPT,
    GLYPH_WARN,
    STYLE_WARN,
)
from capabledeputy.tui.inline.render import quarantine


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AgentMessage:
    """Trusted agent prose — rendered as Markdown (a *different* path from
    untrusted content)."""

    markdown: str


@dataclass(frozen=True)
class UntrustedBlock:
    """Untrusted tool output / relayed content — always quarantine-rendered."""

    raw: str


@dataclass(frozen=True)
class ToolDecision:
    """A completed tool call + its engine decision — the inline chip."""

    decision: PolicyDecision
    action_kind: CapabilityKind | str
    target: str


@dataclass(frozen=True)
class ApprovalPrompt:
    """A gated action awaiting the human — the actionable card. `armed` marks
    the one decision the keys currently act on."""

    decision: PolicyDecision
    action_kind: CapabilityKind | str
    target: str
    armed: bool = True


@dataclass(frozen=True)
class Advisory:
    """A non-blocking WARN — informed-proceed, not a stop."""

    text: str


@dataclass(frozen=True)
class Outcome:
    """A resolution notice (approved / denied / overridden / sent)."""

    text: str
    style: str = "dim"


Entry = (
    UserMessage | AgentMessage | UntrustedBlock | ToolDecision | ApprovalPrompt | Advisory | Outcome
)


def render_entry(entry: Entry, *, marker: SessionMarker) -> RenderableType:
    """Render one conversation entry to a Rich renderable. The dispatch keeps
    each entry type on its own render path (the trust boundary)."""
    if isinstance(entry, UserMessage):
        line = Text()
        line.append(f"{GLYPH_PROMPT} ", style="bold")
        line.append(entry.text)
        return line
    if isinstance(entry, AgentMessage):
        return Markdown(entry.markdown)
    if isinstance(entry, UntrustedBlock):
        return quarantine(entry.raw)
    if isinstance(entry, ToolDecision):
        return decision_chip(
            entry.decision,
            action_kind=entry.action_kind,
            target=entry.target,
        )
    if isinstance(entry, ApprovalPrompt):
        return render_card(
            entry.decision,
            action_kind=entry.action_kind,
            target=entry.target,
            marker=marker,
        )
    if isinstance(entry, Advisory):
        line = Text()
        line.append(f"{GLYPH_WARN} ", style=STYLE_WARN)
        line.append(entry.text, style=STYLE_WARN)
        line.append("   (proceeding)", style="dim")
        return line
    # Outcome
    return Text(entry.text, style=entry.style)
