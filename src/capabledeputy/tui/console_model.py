"""Pure view-model for the unified drive+monitor+approve TUI.

The Textual shell (`tui/console.py`) is intentionally thin: every
formatting / selection decision that can actually break lives here as
a pure function over the daemon's JSON shapes, so it is unit-testable
without spinning up a terminal app (constitution Principle III — the
Textual layer has no integration tests, by long-standing project
precedent; the logic that matters is tested here instead).

All strings returned use Rich console markup, which Textual's
RichLog / Static render natively.
"""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType

from capabledeputy.cli.markdown_media import render_trusted_markdown
from capabledeputy.presentation import (
    DENY_RECOVERY,
    capability_line,
    compartment_summary,
    render_labels,
)

_GLYPH = {"allow": "✓", "deny": "✗", "require_approval": "⚠"}
_COLOR = {"allow": "green", "deny": "red", "require_approval": "yellow"}


def outcome_line(o: dict[str, Any]) -> str:
    """One Rich-markup line summarising a tool outcome."""
    decision = o.get("decision", "?")
    color = _COLOR.get(decision, "white")
    glyph = _GLYPH.get(decision, "·")
    tool = o.get("tool_name") or "?"
    bits: list[str] = []
    if o.get("rule"):
        bits.append(f"rule={o['rule']}")
    if o.get("labels_added"):
        bits.append("+" + ",".join(o["labels_added"]))
    tail = ("  " + " ".join(bits)) if bits else ""
    return f"  [{color}]{glyph} {decision}[/{color}] [bold]{tool}[/bold]{tail}"


def format_history_turn(turn: dict[str, Any]) -> list[str | RenderableType]:
    """Rich lines/renderables for one persisted session-history turn."""
    role = str(turn.get("role") or "?")
    content = str(turn.get("content") or "")
    if role == "user":
        header = "[bold cyan]user[/bold cyan]"
    elif role == "agent":
        header = "[bold green]agent[/bold green]"
    else:
        header = f"[bold]{role}[/bold]"
    lines: list[str | RenderableType] = [header]
    if role == "agent" and content.strip():
        lines.append(render_trusted_markdown(content))
    else:
        for content_line in content.split("\n"):
            lines.append(f"  {content_line}")
    lines.append("")
    return lines


def format_session_history(history: list[dict[str, Any]]) -> list[str | RenderableType]:
    """Full scrollable transcript for a session's persisted history."""
    lines: list[str | RenderableType] = []
    for turn in history:
        lines.extend(format_history_turn(turn))
    return lines


def format_turn(result: dict[str, Any]) -> list[str | RenderableType]:
    """Rich lines/renderables for one agent turn: markdown reply, each
    tool outcome, and — for a denial — the deterministic recovery hint
    (same one the REPL and monitor TUI show)."""
    content = str(result.get("content") or "")
    lines: list[str | RenderableType] = ["[bold cyan]agent[/bold cyan]"]
    if content.strip():
        lines.append(render_trusted_markdown(content))
    lines.append(
        f"[dim](iterations={result.get('iterations')}, finish={result.get('finish_reason')})[/dim]",
    )
    for o in result.get("tool_outcomes", []):
        lines.append(outcome_line(o))
        if o.get("decision") == "deny":
            if o.get("reason"):
                lines.append(f"    [dim]{o['reason']}[/dim]")
            hint = DENY_RECOVERY.get(o.get("rule") or "")
            if hint:
                lines.append(f"    [cyan]↳ recover:[/cyan] [dim]{hint}[/dim]")
        elif o.get("decision") == "require_approval" and o.get("reason"):
            lines.append(f"    [dim]{o['reason']}[/dim]")
    return lines


def pending_approvals(result: dict[str, Any]) -> list[int]:
    """Approval ids the runtime queued for this turn's outcomes. The
    runtime registers them server-side at the chokepoint; the TUI only
    observes the ids and routes the human to the verbatim-review modal.
    """
    out: list[int] = []
    for o in result.get("tool_outcomes", []):
        if o.get("decision") != "require_approval":
            continue
        aid = o.get("approval_id")
        if aid is not None:
            out.append(int(aid))
    return out


def status_lines(session: dict[str, Any]) -> list[str]:
    """Rich-markup sidebar: compartment health + every capability with
    its full v0.7 constraint set. This is the live security view —
    you watch the compartment flip to TAINTED as the agent reads
    untrusted content."""
    labels = session.get("label_set", [])
    word, style = compartment_summary(labels)
    lines = [
        f"[bold]compartment[/bold] [{style}]{word}[/{style}]",
        render_labels(labels),
    ]
    used = session.get("used_kinds", [])
    if used:
        lines.append(f"[dim]used:[/dim] {', '.join(used)}")
    caps = session.get("capability_set", [])
    lines.append("")
    lines.append(f"[bold]capabilities[/bold] ({len(caps)})")
    if caps:
        lines.extend(f"  {capability_line(c)}" for c in caps)
    else:
        lines.append("  [dim]none[/dim]")
    return lines
