"""T072 — Isolation posture composition (FR-040/041/042 + SC-017).

Three invariants:
  1. A disposable isolation region lifts effective reversibility to
     `reversible/system` regardless of base — FR-040.
  2. Containment ≠ declassification: source category labels survive
     even when contained — FR-041.
  3. EXECUTE.sandbox without an actuator port ⇒ raise (caller
     converts to OverrideRequired) — FR-042 + SC-017 caveat.
"""

from __future__ import annotations

import pytest

from capabledeputy.patterns.isolation_posture import (
    IsolationPosture,
    IsolationPostureError,
    compose_with_isolation,
    output_label_after_isolation,
)
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)


def _irreversible_external() -> ReversibilityLabel:
    return ReversibilityLabel(
        degree=ReversibilityDegree.IRREVERSIBLE,
        agent=ReversalAgent.EXTERNAL,
    )


def test_disposable_region_lifts_to_reversible_system() -> None:
    """FR-040 — even an irreversible/external base, when contained
    in a disposable region, composes to reversible/system because
    the region-discard tears down everything."""
    base = _irreversible_external()
    result = compose_with_isolation(
        base=base,
        posture=IsolationPosture.IN_DISPOSABLE_REGION,
    )
    assert result.label.degree == ReversibilityDegree.REVERSIBLE
    assert result.label.agent == ReversalAgent.SYSTEM


def test_no_isolation_passes_base_through() -> None:
    base = _irreversible_external()
    result = compose_with_isolation(base=base, posture=IsolationPosture.NONE)
    assert result.label == base


def test_sandbox_without_actuator_raises() -> None:
    """SC-017 — EXECUTE.sandbox without an actuator port available
    fails-closed. Caller turns the raise into an OverrideRequired."""
    with pytest.raises(IsolationPostureError):
        compose_with_isolation(
            base=_irreversible_external(),
            posture=IsolationPosture.SANDBOX_REQUESTED_NO_ACTUATOR,
        )


def test_output_label_preserved_through_isolation() -> None:
    """FR-041 — containment doesn't strip labels. A health-labelled
    input that runs inside a disposable region still produces
    health-labelled output."""
    source = frozenset({"health", "regulated"})
    out = output_label_after_isolation(source_label_set=source)
    assert out == source


def test_disposable_region_with_already_reversible_base() -> None:
    """The region can't make things WORSE — composing with a
    reversible base keeps reversible (the region just re-affirms)."""
    base = ReversibilityLabel(
        degree=ReversibilityDegree.REVERSIBLE,
        agent=ReversalAgent.SYSTEM,
    )
    result = compose_with_isolation(
        base=base,
        posture=IsolationPosture.IN_DISPOSABLE_REGION,
    )
    assert result.label.degree == ReversibilityDegree.REVERSIBLE
    assert result.label.agent == ReversalAgent.SYSTEM
