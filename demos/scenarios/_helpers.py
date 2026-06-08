"""Shared helpers for the demo scenarios.

Every demo constructs its own `App` with a tailored `PolicyContext`.
This module centralizes the boilerplate so each scenario can focus on
the security promise it's demonstrating.

The transcript helpers — `demo_header`, `step`, `user`, `ai`, `policy`,
`tool`, `audit`, `note` — produce a consistent fixed-width log that
reads as a trace of who-said-what + what-the-policy-decided. Each row
is keyed by ACTOR so the eye can scan a long run for ✓ / ✗ outcomes
without parsing prose.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from capabledeputy.app import App
from capabledeputy.audit.events import EventType
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session
from capabledeputy.tools.client import PolicyContext

# Frozen clock used across all demos. Determinism is part of the
# security promise (SC-002); demos hardcode the same `now` so the
# audit trails are byte-identical across runs.
FROZEN_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


# --- App + session boilerplate ---------------------------------------


def make_app(
    tmp_path: Path,
    *,
    policy_context: PolicyContext | None = None,
    purposes: Any = None,
) -> App:
    """Construct a demo App with audit log + state DB rooted in tmp_path."""
    return App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        policy_context=policy_context,
        purposes=purposes,
        enable_policy_preview=False,
    )


async def make_session(
    app: App,
    *,
    axis_a_categories: tuple[tuple[str, Tier], ...] = (),
    provenance: ProvenanceLevel = ProvenanceLevel.PRINCIPAL_DIRECT,
    initiator: str = "principal:alice",
    authentication: str = "device-bound",
    clearance_profile_id: str | None = None,
    purpose_handle: str = "unset",
    capabilities: frozenset[Any] = frozenset(),
) -> Session:
    """Bootstrap a session with the demo's required axes + capabilities."""
    from dataclasses import replace as dc_replace

    s = await app.graph.new(purpose_handle=purpose_handle)
    # R4b.4: the session's single label_state replaces the old axis_a/axis_b.
    label_state = LabelState(
        a=frozenset(CategoryTag(cat, tier) for cat, tier in axis_a_categories),
        b=frozenset({ProvenanceTag(provenance)}),
    )
    axis_d = AxisD(initiator=initiator, authentication=authentication)
    updated = dc_replace(
        s,
        label_state=label_state,
        axis_d=axis_d,
        capability_set=capabilities,
        clearance_profile_id=clearance_profile_id,
    )
    app.graph._sessions[s.id] = updated
    await app.graph._save(updated)
    return updated


# --- Transcript helpers ----------------------------------------------

WIDTH = 72
_GUTTER = "  POLICY │ "  # widest prefix; alignment anchor
_LEAD = "  "
_BAR = " │ "
_ACTOR_W = 6  # POLICY/AUDIT/USER/AI/TOOL all fit in 6
_WRAP = WIDTH - len(_LEAD) - _ACTOR_W - len(_BAR)


def demo_header(
    title: str,
    *,
    n: int | None = None,
    of: int | None = None,
    blurb: str = "",
    models: tuple[str, ...] = (),
    patterns: tuple[str, ...] = (),
) -> None:
    """Top-of-demo banner. Pass n/of for a 'DEMO i / N' label. Pass
    models / patterns to surface the security models and flow patterns
    being exercised — those appear in the banner so an operator running
    -s sees up front what the demo will demonstrate."""
    print()
    print("═" * WIDTH)
    head = f"  DEMO {n} / {of}  {title}" if n is not None and of is not None else f"  {title}"
    print(head)
    print("─" * WIDTH)
    if blurb:
        for line in textwrap.wrap(" ".join(blurb.split()), width=WIDTH - 2, break_long_words=False):
            print(f"  {line}")
    if models or patterns:
        if blurb:
            print()
        if models:
            _labeled_list("Models", models)
        if patterns:
            _labeled_list("Patterns", patterns)
    print()


def _labeled_list(label: str, items: tuple[str, ...]) -> None:
    """Render '  Models     · A · B · C' with wrapping. Models and
    Patterns share a label column so the bullets always align."""
    label_w = 10  # accommodates "Patterns " + 2-space gutter
    prefix = f"  {label.ljust(label_w)}"
    bullet = "· "
    body = bullet + (" · ").join(items)
    continuation_indent = " " * len(prefix)
    wrapped = textwrap.wrap(body, width=WIDTH - len(prefix), break_long_words=False) or [body]
    print(f"{prefix}{wrapped[0]}")
    for cont in wrapped[1:]:
        print(f"{continuation_indent}{cont}")


def step(n: int | str, label: str) -> None:
    """Step header. n may be int ('Step 3') or a string ('Part A')."""
    head = f"Step {n}  {label}" if isinstance(n, int) else f"{n}  {label}"
    print()
    print(f"  {head}")
    print(f"  {'─' * len(head)}")


def _row(actor: str, text: str) -> None:
    """One actor-prefixed line. Wraps long text under the gutter."""
    actor_pad = actor.ljust(_ACTOR_W)
    lines = textwrap.wrap(text, width=_WRAP, break_long_words=False) or [""]
    first = True
    for line in lines:
        if first:
            print(f"{_LEAD}{actor_pad}{_BAR}{line}")
            first = False
        else:
            print(f"{_LEAD}{' ' * _ACTOR_W}{_BAR}{line}")


def _continuation(text: str) -> None:
    """An indented rationale line under a row, with no leading actor."""
    for line in textwrap.wrap(text, width=_WRAP, break_long_words=False):
        print(f"{_LEAD}{' ' * _ACTOR_W}   {line}")


def user(text: str) -> None:
    _row("USER", text)


def ai(text: str) -> None:
    _row("AI", text)


def tool(text: str) -> None:
    _row("TOOL", text)


def audit(text: str) -> None:
    _row("AUDIT", text)


def note(text: str) -> None:
    """Inline scene-setter not tied to any actor. Italicized with a
    leading marker so it doesn't visually merge with a preceding
    policy()/tool() rationale block."""
    actor_pad = " " * _ACTOR_W
    marker = "  …    "
    lines = textwrap.wrap(text, width=_WRAP, break_long_words=False) or [""]
    for i, line in enumerate(lines):
        if i == 0:
            print(f"{_LEAD}{actor_pad}{marker}{line}")
        else:
            print(f"{_LEAD}{actor_pad}{' ' * len(marker)}{line}")


_DECISION_GLYPH = {
    "allow": "✓",
    "deny": "✗",
    "require_approval": "?",
    "override_required": "?",
    "refused": "✗",
    "active": "✓",
    "pending": "·",
    "pending_attestation": "·",
    "approved": "✓",
    "consumed": "·",
}


def policy(
    decision: str,
    rule: str | None = None,
    *,
    rationale: str | None = None,
) -> None:
    """Emit one POLICY row. `decision` is the bare outcome string
    ('allow', 'deny', 'refused', 'active', etc.). `rule` is the
    rule id; `rationale` is human prose continuation."""
    d = decision.lower()
    glyph = _DECISION_GLYPH.get(d, "·")
    label = d.upper().replace("_", "-")
    head = f"{glyph} {label}"
    if rule:
        head += f"  ·  {rule}"
    _row("POLICY", head)
    if rationale:
        _continuation(rationale)


def policy_outcome(outcome: Any, *, rationale: str | None = None) -> None:
    """Convenience: take a ToolCallOutcome and emit a POLICY row.
    Pulls outcome.decision, outcome.rule, outcome.reason."""
    fallback_rationale = None
    if outcome.decision.value != "allow":
        fallback_rationale = outcome.reason
    policy(
        outcome.decision.value,
        outcome.rule,
        rationale=rationale or fallback_rationale,
    )


# --- Audit log helpers -----------------------------------------------


async def collect_events(app: App) -> list[Any]:
    return await app.audit.read_all()


def event_types(events: list[Any]) -> list[EventType]:
    return [e.event_type for e in events]


# --- Backward-compat shim --------------------------------------------


def narrate(title: str, body: str) -> None:
    """Legacy free-text narrator. Retained so any old caller doesn't
    break — new code should use the actor-prefixed helpers
    (user / ai / policy / tool / audit)."""
    print(f"\n=== {title} ===")
    for line in body.strip().split("\n"):
        print(f"  {line}")


__all__ = [
    "FROZEN_NOW",
    "ai",
    "audit",
    "collect_events",
    "demo_header",
    "event_types",
    "make_app",
    "make_session",
    "narrate",
    "note",
    "policy",
    "policy_outcome",
    "step",
    "tool",
    "user",
]
