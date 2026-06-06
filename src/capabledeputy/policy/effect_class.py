"""Axis C — Effect Class (the CORE "Operation"). 003 redesign.

`EffectClass` is the canonical, closed taxonomy of what an action *does*.
It is the rule-matching key (a `DecisionRule` predicates on the enum).
An optional free-form `subtype` is retained per operation for display,
audit, and optional rule-narrowing — but a rule that omits `subtype`
matches the whole enum class.

See specs/003-labeling-framework/label-model-redesign.md §5. Landed in
R1; consumed by ToolDefinition / decide() in R3/R4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from capabledeputy.policy.labels import ProvenanceLevel


class EffectClass(StrEnum):
    """Canonical Operation taxonomy (Axis C). Rules match on this enum."""

    OBSERVE = "OBSERVE"  # read-only, non-egressing introspection
    FETCH = "FETCH"  # read external data into the session
    MUTATE_LOCAL = "MUTATE_LOCAL"  # write to a controlled local resource
    DESTROY = "DESTROY"  # destructive write / deletion
    COMMUNICATE = "COMMUNICATE"  # outbound message to a third party
    TRANSACT = "TRANSACT"  # committing/financial action
    EXECUTE_SANDBOX = "EXECUTE.sandbox"  # contained, egress-free execution (preferred)
    EXECUTE_HOST = "EXECUTE.host"  # execution on the host
    EXECUTE_REMOTE = "EXECUTE.remote"  # execution on a remote system
    EXECUTE_DEPLOY = "EXECUTE.deploy"  # deployment / release
    ADMINISTER = "ADMINISTER"  # control-plane (label/capability/profile/audit ops)
    ACTUATE_PHYSICAL = "ACTUATE_PHYSICAL"  # physical-world effect


@dataclass(frozen=True)
class Operation:
    """An action a tool performs (CORE "Operation").

    - `effect_class`: the canonical rule-matching key.
    - `subtype`: optional free-form refinement (display/audit/narrowing);
      e.g. `MUTATE_LOCAL` + `"calendar.delete"`.
    - `required_floor`: the Biba integrity requirement — the minimum input
      trustworthiness this action demands. `None` ⇒ no floor. Checked at
      decide() against the session's Axis-B provenance
      (`label_state.meets_required_floor`).
    """

    effect_class: EffectClass
    subtype: str | None = None
    required_floor: ProvenanceLevel | None = None
