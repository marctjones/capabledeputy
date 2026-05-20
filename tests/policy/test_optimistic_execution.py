"""T069 — Optimistic execution boundary (FR-034 / SC-013 / Quickstart §2).

Reversible/non-egressing work runs without prompts. Anything else
falls back to the normal decide() pipeline.
"""

from __future__ import annotations

from capabledeputy.policy.optimistic import evaluate_optimistic
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)


def _r(degree: ReversibilityDegree, agent: ReversalAgent) -> ReversibilityLabel:
    return ReversibilityLabel(degree=degree, agent=agent)


def test_reversible_system_non_egressing_auto() -> None:
    """The happy path. SC-013: 'reversible/non-egressing pipelines
    run autonomously'."""
    decision = evaluate_optimistic(
        effective_reversibility=_r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM),
        is_egressing=False,
    )
    assert decision.should_auto


def test_reversible_system_egressing_does_not_auto() -> None:
    """Even reversible/system loses optimism when crossing an egress
    boundary — egress is the containment edge."""
    decision = evaluate_optimistic(
        effective_reversibility=_r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM),
        is_egressing=True,
    )
    assert not decision.should_auto


def test_reversible_human_does_not_auto() -> None:
    """Reversal-agent=human means a person must take action to undo;
    no optimistic auto-execution."""
    decision = evaluate_optimistic(
        effective_reversibility=_r(ReversibilityDegree.REVERSIBLE, ReversalAgent.HUMAN),
        is_egressing=False,
    )
    assert not decision.should_auto


def test_reversible_external_does_not_auto() -> None:
    decision = evaluate_optimistic(
        effective_reversibility=_r(ReversibilityDegree.REVERSIBLE, ReversalAgent.EXTERNAL),
        is_egressing=False,
    )
    assert not decision.should_auto


def test_with_friction_does_not_auto() -> None:
    decision = evaluate_optimistic(
        effective_reversibility=_r(
            ReversibilityDegree.REVERSIBLE_WITH_FRICTION,
            ReversalAgent.SYSTEM,
        ),
        is_egressing=False,
    )
    assert not decision.should_auto


def test_irreversible_does_not_auto() -> None:
    decision = evaluate_optimistic(
        effective_reversibility=_r(
            ReversibilityDegree.IRREVERSIBLE,
            ReversalAgent.SYSTEM,
        ),
        is_egressing=False,
    )
    assert not decision.should_auto
