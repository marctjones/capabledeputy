"""Optimistic execution boundary (003 US6 T081 / FR-034).

If the effective reversibility of an action is `reversible/system`
AND the action is non-egressing, the engine returns AUTO without
prompting — that's the optimistic execution carve-out (SC-013).
Work whose reversal-agent is `human` (or `external`) surfaces / gates
through the normal approval path.

Purpose-contamination is *not* an act-then-flag case: an inadmissible
category is pre-excluded at session spawn (FR-009 via T056). So this
module never needs to back out a purpose-category contamination
post-facto.
"""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
)


@dataclass(frozen=True)
class OptimisticDecision:
    """Result of an optimistic-execution probe.

    `should_auto` is True iff (effective reversibility is reversible/
    system) AND (the action is non-egressing). When False, the caller
    falls back to the normal decide() outcome — optimistic execution
    is *additive*: it only relaxes when conditions are met, never
    ratchets stricter.
    """

    should_auto: bool
    rationale: str


def evaluate_optimistic(
    *,
    effective_reversibility: ReversibilityLabel,
    is_egressing: bool,
) -> OptimisticDecision:
    """Pure function: decide whether optimistic auto-execution applies.

    The strictness ladder:
      - irreversible / any-agent ⇒ never auto
      - reversible-with-friction / any-agent ⇒ never auto
      - reversible / external ⇒ never auto (reversal needs cooperation)
      - reversible / human ⇒ never auto (reversal needs a person)
      - reversible / system, non-egressing ⇒ AUTO
      - reversible / system, egressing ⇒ never auto (egress crosses
        a containment boundary — even reversibility cannot reach it)
    """
    if effective_reversibility.degree != ReversibilityDegree.REVERSIBLE:
        return OptimisticDecision(
            should_auto=False,
            rationale=(
                f"reversibility degree={effective_reversibility.degree.value} "
                f"is not 'reversible' — cannot auto-execute"
            ),
        )
    if effective_reversibility.agent != ReversalAgent.SYSTEM:
        return OptimisticDecision(
            should_auto=False,
            rationale=(
                f"reversal agent={effective_reversibility.agent.value} "
                f"is not 'system' — cannot auto-execute without human/external action"
            ),
        )
    if is_egressing:
        return OptimisticDecision(
            should_auto=False,
            rationale="action is egressing — containment is required, no optimistic auto",
        )
    return OptimisticDecision(
        should_auto=True,
        rationale="reversible/system, non-egressing — optimistic auto-execution",
    )
