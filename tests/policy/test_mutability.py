"""T062 — Mutability composition + planner-prune signal (FR-039 / SC-016).

Effects exceeding a target's effective mutability are deterministically
refused. The planner-prune signal — surfaced via a structured
exception — lets the planner know which (effect, target) pair was
rejected so it can offer an alternative tool, not "try harder."
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.reversibility import (
    MutabilityDegree,
    MutabilityLabel,
    ReversalAgent,
    ReversibilityError,
    compose_mutability,
)


def _m(degree: MutabilityDegree, agent: ReversalAgent) -> MutabilityLabel:
    return MutabilityLabel(degree=degree, agent=agent)


def test_compose_empty_input_raises() -> None:
    """Fail-closed composition: no inputs ⇒ explicit error, never a
    silent permissive default."""
    with pytest.raises(ReversibilityError):
        compose_mutability()


def test_immutable_dominates_in_place() -> None:
    a = _m(MutabilityDegree.IN_PLACE, ReversalAgent.SYSTEM)
    b = _m(MutabilityDegree.IMMUTABLE, ReversalAgent.HUMAN)
    composed = compose_mutability(a, b)
    assert composed.degree == MutabilityDegree.IMMUTABLE


def test_append_only_between_immutable_and_in_place() -> None:
    a = _m(MutabilityDegree.IN_PLACE, ReversalAgent.SYSTEM)
    b = _m(MutabilityDegree.APPEND_ONLY, ReversalAgent.SYSTEM)
    composed = compose_mutability(a, b)
    assert composed.degree == MutabilityDegree.APPEND_ONLY


def test_external_agent_wins_in_composition() -> None:
    """An external agent (un-cooperating third party) is the worst
    case for the actor — composition keeps it."""
    a = _m(MutabilityDegree.IN_PLACE, ReversalAgent.SYSTEM)
    b = _m(MutabilityDegree.IN_PLACE, ReversalAgent.EXTERNAL)
    composed = compose_mutability(a, b)
    assert composed.agent == ReversalAgent.EXTERNAL


def test_three_input_composition_pick_worst_per_dim() -> None:
    """Each dimension picks its own worst; the result is the
    elementwise minimum (most-restrictive)."""
    a = _m(MutabilityDegree.IN_PLACE, ReversalAgent.SYSTEM)
    b = _m(MutabilityDegree.APPEND_ONLY, ReversalAgent.HUMAN)
    c = _m(MutabilityDegree.IN_PLACE, ReversalAgent.EXTERNAL)
    composed = compose_mutability(a, b, c)
    assert composed.degree == MutabilityDegree.APPEND_ONLY  # worst degree
    assert composed.agent == ReversalAgent.EXTERNAL  # worst agent
