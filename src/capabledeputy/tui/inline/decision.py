"""Decision rendering from a typed PolicyDecision (TUI redesign §8.1 #1/#2).

Decision chips and the actionable approval/override card are drawn HERE, from
the engine's typed `PolicyDecision` object — never from a model-supplied
string. That is the type-level guarantee behind hard requirement #1 ("engine-
authored facts only"): there is no parameter on these functions through which
model prose could reach a decision surface.

Every real card also carries the per-session **anti-spoof marker** (§8.1 #4):
a deterministic per-session glyph+accent the operator learns to expect. Because
untrusted content is quarantine-rendered (it cannot style or know the marker),
a card lacking the session's marker is, by construction, not real chrome.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from rich.panel import Panel
from rich.text import Text

from capabledeputy.policy.capabilities import CapabilityKind, kind_name
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.labels import LabelState, ProvenanceLevel
from capabledeputy.policy.rules import Decision
from capabledeputy.tui.inline.glyphs import (
    GLYPH_ACTION,
    GLYPH_TIER,
    GLYPH_UNTRUSTED,
    STYLE_UNTRUSTED,
    glyph_for,
    style_for,
)

# A small palette so each session gets a learnable, distinct mark. The mark is
# what untrusted content cannot forge (it can't style, and doesn't know which).
_MARKER_GLYPHS = ("◆", "◈", "▲", "⬢", "✦", "❖", "⬟", "✜")
_MARKER_STYLES = ("cyan", "green", "magenta", "blue", "yellow", "red", "white", "bright_cyan")


@dataclass(frozen=True)
class SessionMarker:
    """Per-session anti-spoof marker shown on trusted chrome."""

    glyph: str
    style: str


def marker_for_session(session_id: UUID) -> SessionMarker:
    """Deterministic per-session marker. Stable for a session (so the operator
    learns it) and well-distributed across sessions."""
    n = session_id.int
    return SessionMarker(
        glyph=_MARKER_GLYPHS[n % len(_MARKER_GLYPHS)],
        style=_MARKER_STYLES[(n // len(_MARKER_GLYPHS)) % len(_MARKER_STYLES)],
    )


def format_labels(labels: LabelState | None) -> Text:
    """Compact label chips from a LabelState — tier dots per Axis-A category,
    and the untrusted marker if Axis-B carries external-untrusted provenance."""
    out = Text()
    if labels is None:
        return out
    first = True
    for cat in sorted(labels.a, key=lambda c: c.category):
        if not first:
            out.append("  ")
        first = False
        out.append(f"{GLYPH_TIER} ", style="dim")
        out.append(f"{cat.category}/{cat.tier.value}")
    if any(p.level is ProvenanceLevel.EXTERNAL_UNTRUSTED for p in labels.b):
        if not first:
            out.append("  ")
        out.append(f"{GLYPH_UNTRUSTED} untrusted", style=STYLE_UNTRUSTED)
    return out


def _action_label(action_kind: CapabilityKind | str, target: str) -> str:
    return f"{kind_name(action_kind)}({target})"


def decision_chip(
    decision: PolicyDecision,
    *,
    action_kind: CapabilityKind | str,
    target: str,
) -> Text:
    """The inline one-line notice for a decision. `✓` allows are quiet; gated
    outcomes are accented. Built only from typed fields."""
    glyph = glyph_for(decision.decision)
    style = style_for(decision.decision)
    line = Text()
    line.append(f"{GLYPH_ACTION} ", style="dim")
    line.append(f"{glyph} ", style=style)
    line.append(_action_label(action_kind, target))
    if decision.rule and decision.decision is not Decision.ALLOW:
        line.append(f"   {decision.rule}", style=style)
    return line


def render_card(
    decision: PolicyDecision,
    *,
    action_kind: CapabilityKind | str,
    target: str,
    marker: SessionMarker,
) -> Panel:
    """The actionable approval/override card — engine facts + the anti-spoof
    marker + the keys. Drawn purely from the typed PolicyDecision; no model
    string can reach it."""
    is_override = decision.decision is Decision.OVERRIDE_REQUIRED
    verb = "override" if is_override else "approve"
    style = style_for(decision.decision)

    body = Text()
    body.append(f"{verb} this action?   ", style=style)
    body.append(_action_label(action_kind, target))
    labels = format_labels(decision.labels_snapshot)
    if labels.plain:
        body.append("\n")
        body.append(labels)
    if decision.rule:
        body.append("\n")
        body.append("floor: ", style="dim")
        body.append(decision.rule, style=style)
    if decision.reason:
        body.append("\n")
        body.append("why: ", style="dim")
        body.append(decision.reason)
    body.append("\n\n")
    keys = (
        "[a] approve   [d] deny   [w] why"
        if not is_override
        else "[o] override   [d] deny   [w] why"
    )
    body.append(keys, style="dim")

    # The title carries the per-session anti-spoof marker — untrusted content
    # (quarantine-rendered) can neither style this nor know which mark is right.
    title = Text()
    title.append(f"{marker.glyph} ", style=marker.style)
    title.append("decision", style=style)
    return Panel(body, title=title, border_style=style, expand=False)
