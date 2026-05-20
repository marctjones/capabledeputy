"""Isolation posture rules (003 US6 T084 / FR-040/041/042).

A session running inside a Disposable Isolation Region (DIR) gets
its effective reversibility *lifted* to `reversible/system` —
because tearing down the region undoes the run by construction.
**Containment ≠ declassification** (FR-041): outputs that leave the
region retain their source category labels; the region kills the
side-effect, not the label.

`EXECUTE.sandbox` invocation when no SandboxActuator is wired
fails-closed with `OverrideRequired` (FR-042 + SC-017): the
substrate guarantee is missing, so the engine cannot promise
containment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)


class IsolationPostureError(RuntimeError):
    """An EXECUTE.sandbox invocation lacks the actuator port. Per
    FR-042 / SC-017, the engine fails-closed with OverrideRequired."""


class IsolationPosture(StrEnum):
    """Three postures the engine can be in for an action:
    - `none`: no containment — labels and reversibility govern
      directly.
    - `in_disposable_region`: the action runs in a DIR; effective
      reversibility lifts to `reversible/system`.
    - `sandbox_requested_no_actuator`: caller asked for EXECUTE.sandbox
      but no actuator is wired; engine refuses with OverrideRequired.
    """

    NONE = "none"
    IN_DISPOSABLE_REGION = "in_disposable_region"
    SANDBOX_REQUESTED_NO_ACTUATOR = "sandbox_requested_no_actuator"


@dataclass(frozen=True)
class EffectiveReversibility:
    """Outcome of composing the base reversibility with the isolation
    posture. `posture` records the path that produced the value so
    audits can show why (FR-021)."""

    label: ReversibilityLabel
    posture: IsolationPosture
    rationale: str


_REVERSIBLE_SYSTEM = ReversibilityLabel(
    degree=ReversibilityDegree.REVERSIBLE,
    agent=ReversalAgent.SYSTEM,
)


def compose_with_isolation(
    *,
    base: ReversibilityLabel,
    posture: IsolationPosture,
) -> EffectiveReversibility:
    """Compose `base` with the isolation `posture`.

    Rules:
      - posture=none ⇒ base unchanged.
      - posture=in_disposable_region ⇒ reversible/system regardless
        of base (the region-discard undoes everything in the region
        by construction; FR-040).
      - posture=sandbox_requested_no_actuator ⇒ raise
        IsolationPostureError (engine refuses; caller converts to
        OverrideRequired per FR-042).

    Note: containment ≠ declassification (FR-041) — this composition
    affects ONLY the reversibility label. Category labels on outputs
    that leave the region are preserved by the labels module, not
    here.
    """
    if posture is IsolationPosture.SANDBOX_REQUESTED_NO_ACTUATOR:
        raise IsolationPostureError(
            "EXECUTE.sandbox invoked without a SandboxActuator port — "
            "FR-042 fail-closed (caller should return OverrideRequired)",
        )
    if posture is IsolationPosture.NONE:
        return EffectiveReversibility(
            label=base,
            posture=posture,
            rationale="no containment; base reversibility governs",
        )
    if posture is IsolationPosture.IN_DISPOSABLE_REGION:
        return EffectiveReversibility(
            label=_REVERSIBLE_SYSTEM,
            posture=posture,
            rationale="disposable region tears down on discard — reversible/system",
        )
    # Defensive: exhaustive enum, but if a new posture lands here,
    # fail-closed rather than silently most-restrictive.
    raise IsolationPostureError(f"unknown isolation posture: {posture!r}")


def output_label_after_isolation(
    *,
    source_label_set: frozenset[str],
) -> frozenset[str]:
    """FR-041 — containment ≠ declassification. Outputs leaving the
    region retain their source labels exactly. This helper exists
    so the contract is explicit and visible at the call site, not
    accidentally short-circuited by some future composition pass."""
    return source_label_set
