"""The trust-state status line (TUI redesign §3 / §8.1 #7).

A single, always-visible, engine-sourced line that makes the session's trust
state ambient: purpose, clearance ceiling, current taint, pending-advisory
count — and the per-session anti-spoof marker, so the operator sees the mark on
fixed chrome and learns to expect it. It is drawn from typed state only; a
rendering failure must never make it report a *safer* state than is true, so
unknown fields render explicitly (e.g. `purpose:—`) rather than being hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from capabledeputy.policy.labels import LabelState, ProvenanceLevel
from capabledeputy.tui.inline.decision import SessionMarker
from capabledeputy.tui.inline.glyphs import (
    GLYPH_UNTRUSTED,
    GLYPH_WARN,
    STYLE_UNTRUSTED,
    STYLE_WARN,
)


@dataclass(frozen=True)
class TrustState:
    """The engine-known session state the status line surfaces."""

    session_name: str
    purpose: str | None = None
    clearance: str | None = None
    labels: LabelState | None = None
    advisories: int = 0


def _taint_summary(labels: LabelState | None) -> tuple[str, bool]:
    """(category list, is_untrusted). Highest-tier categories first."""
    if labels is None:
        return "", False
    cats = sorted(labels.a, key=lambda c: c.category)
    summary = " ".join(f"{c.category}/{c.tier.value}" for c in cats)
    untrusted = any(p.level is ProvenanceLevel.EXTERNAL_UNTRUSTED for p in labels.b)
    return summary, untrusted


def render_status(state: TrustState, marker: SessionMarker) -> Text:
    """Render the fixed status line. Unknown fields show `—`, never blank, so
    a missing value can't read as 'nothing sensitive here'."""
    line = Text()
    # The per-session marker leads the line — the secure-attention anchor.
    line.append(f"{marker.glyph} ", style=marker.style)
    line.append(state.session_name)
    line.append("  ·  purpose:", style="dim")
    line.append(state.purpose or "—")
    line.append("  ·  clearance:", style="dim")
    line.append(state.clearance or "—")

    summary, untrusted = _taint_summary(state.labels)
    if summary:
        line.append("  ·  ", style="dim")
        line.append(summary)
    if untrusted:
        line.append("  ", style="dim")
        line.append(f"{GLYPH_UNTRUSTED} untrusted", style=STYLE_UNTRUSTED)
    if state.advisories:
        line.append("  ·  ", style="dim")
        line.append(f"{GLYPH_WARN}{state.advisories}", style=STYLE_WARN)
    return line
