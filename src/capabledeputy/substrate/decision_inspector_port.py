"""DecisionInspector port (spec 004 P0).

A DecisionInspector runs AFTER the standard policy decision and may
either RELAX (loosen) or TIGHTEN (strengthen) the proposed outcome.
Composes monotonically: when multiple inspectors fire, TIGHTEN wins
over RELAX so the most-restrictive position prevails.

Compared to RaiseOnlyInspector (which mutates session labels at ingest):
  - RaiseOnlyInspector — runs at INGEST; produces label deltas
  - DecisionInspector   — runs at DECISION; produces outcome adjustments

Common use cases:
  - SelfEgressRelaxer:    auto-allow email to operator's own addresses
  - AfterHoursTightener:  require approval for late-night purchases
  - PolicyHookForOPA:     consult OPA for a corporate baseline
  - PolicyHookForStarlark: run an operator-authored Starlark inspector

Contract: implementations MUST be pure functions of their inputs.
No I/O, no mutation of inputs, no side effects. Deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from capabledeputy.policy.rules import Decision


@dataclass(frozen=True)
class DecisionRelax:
    """Loosen the proposed decision (e.g., REQUIRE_APPROVAL → ALLOW).

    Attributes:
        to: The new (looser) decision. Must be strictly less restrictive
            than the proposed one — engine validates and refuses
            non-monotone moves.
        rule: A short rule identifier for the audit log.
        rationale: Human-readable explanation of why the relaxation is
                   appropriate.
    """

    to: Decision
    rule: str
    rationale: str = ""


@dataclass(frozen=True)
class DecisionTighten:
    """Strengthen the proposed decision (e.g., ALLOW → REQUIRE_APPROVAL,
    or REQUIRE_APPROVAL → DENY).

    Attributes:
        to: The new (stricter) decision. Must be strictly more
            restrictive than the proposed one.
        rule: A short rule identifier for the audit log.
        rationale: Human-readable explanation of why the tightening is
                   appropriate.
    """

    to: Decision
    rule: str
    rationale: str = ""


# Ordering for monotonicity checks. Higher index = more restrictive.
# Lookup-based; explicit so engine code doesn't depend on enum order.
_RESTRICTIVENESS: dict[Decision, int] = {
    Decision.ALLOW: 0,
    Decision.WARN: 1,
    Decision.REQUIRE_APPROVAL: 2,
    Decision.OVERRIDE_REQUIRED: 3,
    Decision.DENY: 4,
}


def is_strictly_more_restrictive(new: Decision, old: Decision) -> bool:
    """True iff `new` is strictly stricter than `old` (for Tighten)."""
    return _RESTRICTIVENESS.get(new, -1) > _RESTRICTIVENESS.get(old, -1)


def is_strictly_less_restrictive(new: Decision, old: Decision) -> bool:
    """True iff `new` is strictly looser than `old` (for Relax)."""
    return _RESTRICTIVENESS.get(new, -1) < _RESTRICTIVENESS.get(old, -1)


class DecisionInspector(Protocol):
    """Inspector contract.

    Implementations declare a `name` for audit attribution, and an
    `inspect()` method called by the chokepoint AFTER the standard
    policy decision. Return None to abstain (most common); return
    DecisionRelax/Tighten to adjust.
    """

    name: str

    def inspect(
        self,
        *,
        action: Any,
        session: Any,
        proposed_outcome: Any,
    ) -> DecisionRelax | DecisionTighten | None:
        """Inspect the proposed decision; return an adjustment or None."""
        ...


def compose_inspector_outcomes(
    proposed: Decision,
    outcomes: list[tuple[str, DecisionRelax | DecisionTighten | None]],
) -> tuple[Decision, str, str] | None:
    """Compose multiple DecisionInspector outcomes monotonically.

    Rules:
      - Any TIGHTEN beats any RELAX (most-restrictive wins).
      - Among TIGHTENS, the strictest decision wins.
      - Among RELAXES (no tightens present), the loosest decision wins.
      - Returns (decision, rule, rationale) for the WINNER, or None if
        no outcome applies (every inspector abstained).

    The winning outcome must still be strictly less/more restrictive
    than `proposed`; otherwise it's a no-op (returned as None).
    """
    tightens: list[tuple[str, DecisionTighten]] = []
    relaxes: list[tuple[str, DecisionRelax]] = []
    for name, oc in outcomes:
        if isinstance(oc, DecisionTighten) and is_strictly_more_restrictive(oc.to, proposed):
            tightens.append((name, oc))
        elif isinstance(oc, DecisionRelax) and is_strictly_less_restrictive(oc.to, proposed):
            relaxes.append((name, oc))
    if tightens:
        # Strictest tighten wins (highest restrictiveness index).
        name, t = max(tightens, key=lambda x: _RESTRICTIVENESS.get(x[1].to, -1))
        return (t.to, f"{name}:{t.rule}", t.rationale)
    if relaxes:
        # Loosest relax wins (lowest restrictiveness index).
        name, r = min(relaxes, key=lambda x: _RESTRICTIVENESS.get(x[1].to, 99))
        return (r.to, f"{name}:{r.rule}", r.rationale)
    return None
