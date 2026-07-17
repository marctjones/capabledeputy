"""The one precedence lattice (#379).

`docs/policy-authoring-design.md` §5 defines a single, fixed way policy inputs
combine so a human never has to wonder which knob wins:

    structural floors                         (non-negotiable; Principle VI)
      > requirements (operator MUSTs, proven at load)
        > authored rules
          > posture defaults
            > purpose tightening
    — and MOST-RESTRICTIVE ALWAYS WINS.

This module is the single home for that order. Today the composition itself
lives inside `engine._decide_impl` (each `_compose_with_*` step takes the
stricter outcome); this module makes the ordering *explicit and reusable* and
supplies the one piece that was missing — the **posture-vs-purpose
risk-preference precedence** that #307 was scoped to own but did not land.

Nothing here changes `decide()`; it is the declarative statement of the lattice
plus the pure resolver the session-spawn path uses to combine a posture's dial
with a purpose's dial.
"""

from __future__ import annotations

from enum import IntEnum

from capabledeputy.policy.envelope import RiskPreference
from capabledeputy.policy.rules import Decision


class PrecedenceLevel(IntEnum):
    """Where an outcome came from, ordered by authority (highest wins). Used for
    audit attribution and to document the lattice in one place. A higher level
    can only ever make the composite STRICTER (most-restrictive-wins); it never
    relaxes a stricter contribution from a lower level."""

    PURPOSE = 0  # per-task tightening
    POSTURE = 1  # posture defaults
    RULE = 2  # operator-authored rules
    REQUIREMENT = 3  # operator MUSTs (proven at load, not applied per-decision)
    FLOOR = 4  # structural, non-negotiable


# Restrictiveness of each decision outcome — the canonical ranking the whole
# engine composes on (mirrors engine._LEGACY_RANK). Lower = stricter; the
# most-restrictive outcome is the one with the LOWEST rank.
_DECISION_RANK: dict[Decision, int] = {
    Decision.DENY: 0,
    Decision.OVERRIDE_REQUIRED: 1,
    Decision.REQUIRE_APPROVAL: 2,
    Decision.WARN: 3,
    Decision.ALLOW: 4,
}


def most_restrictive(*decisions: Decision) -> Decision:
    """The strictest (lowest-rank) decision across the inputs. This is the one
    composition rule the whole lattice uses. Fail-closed: empty input is a
    programming error (callers must supply at least one outcome)."""
    if not decisions:
        raise ValueError("most_restrictive() requires at least one Decision")
    return min(decisions, key=lambda d: _DECISION_RANK[d])


def is_at_least_as_restrictive(a: Decision, b: Decision) -> bool:
    """True iff `a` is at least as strict as `b` (a's rank <= b's rank)."""
    return _DECISION_RANK[a] <= _DECISION_RANK[b]


# Autonomy ordering of the risk-preference dial: cautious < balanced < permissive
# (increasing autonomy). "Stricter" = LESS autonomy = the more cautious value.
_DIAL_AUTONOMY: dict[RiskPreference, int] = {
    RiskPreference.CAUTIOUS: 0,
    RiskPreference.BALANCED: 1,
    RiskPreference.PERMISSIVE: 2,
}


def stricter_dial(a: RiskPreference, b: RiskPreference) -> RiskPreference:
    """The more cautious (less autonomous) of two dials."""
    return min(a, b, key=lambda d: _DIAL_AUTONOMY[d])


def resolve_risk_preference(
    base: RiskPreference,
    purpose_override: RiskPreference | None,
) -> RiskPreference:
    """Combine the posture's `base` dial with a purpose's dial.

    Precedence (lattice §5): the posture BINDS the baseline; a purpose may only
    TIGHTEN it, never loosen it. So the resolved dial is the stricter (more
    cautious) of the two — a purpose asking for MORE autonomy than the posture
    allows is ignored, and the posture's caution stands. This is the piece #307
    was scoped to own.

    `purpose_override is None` (no purpose, or a purpose that doesn't set a dial)
    leaves the posture's baseline unchanged.
    """
    if purpose_override is None:
        return base
    return stricter_dial(base, purpose_override)
