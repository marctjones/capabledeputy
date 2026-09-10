"""Declassified re-dispatch carve-out (DESIGN.md §10.11).

Regression coverage for the gap where `approval.approve` on a
destructive/send/purchase action would mint a fresh one-shot
capability (legacy ALLOW), re-dispatch it through the same
`engine.decide()` chokepoint, and have the v2 rules leg — finding no
human-ratified rule for the cell — ratchet the fresh ALLOW straight
back down to REQUIRE_APPROVAL. The approve button became a no-op
whenever no rule happened to match, which in a real deployment with an
under-authored `rules.yaml` is nearly always.

`already_approved=True` (set only by
`approval_handlers._execute_declassified_*`, never by anything
RPC-reachable) suppresses that specific ratchet. DENY and
OVERRIDE_REQUIRED must still win — the approval queue resolves an
ordinary REQUIRE_APPROVAL/SUGGEST gate, not a hard floor; only an
Override Grant ceremony (FR-038) crosses those.
"""

from __future__ import annotations

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
)
from capabledeputy.policy.engine import decide
from capabledeputy.policy.labels import AxisD
from capabledeputy.policy.rules import Decision

_EFFECT_CLASS = "data.modify_local"
_TARGET = "note-1"

_ONE_SHOT_DESTRUCTIVE_CAP = Capability(
    kind=CapabilityKind.MEMORY_MODIFY,
    pattern=_TARGET,
    expiry=CapabilityExpiry.ONE_SHOT,
    origin=CapabilityOrigin.USER_APPROVED,
    allows_destructive=True,
)
_ACTION = Action(kind=CapabilityKind.MEMORY_MODIFY, target=_TARGET)
_NO_RULES = DecisionRules(rules=())


def _decide(*, rules_v2: DecisionRules, already_approved: bool) -> Decision:
    return decide(
        capabilities=frozenset({_ONE_SHOT_DESTRUCTIVE_CAP}),
        action=_ACTION,
        axis_d=AxisD(),
        effect_class=_EFFECT_CLASS,
        rules_v2=rules_v2,
        already_approved=already_approved,
    ).decision


def test_without_already_approved_v2_default_still_ratchets_to_require_approval() -> None:
    """Baseline: reproduces the reported bug. A fresh one-shot
    allows_destructive=True capability (legacy ALLOW) with no matching
    v2 rule collapses to REQUIRE_APPROVAL absent the carve-out —
    proving `already_approved` is the thing doing the work below, not
    some other change to the default composition."""
    assert _decide(rules_v2=_NO_RULES, already_approved=False) == Decision.REQUIRE_APPROVAL


def test_already_approved_suppresses_v2_default_ratchet() -> None:
    assert _decide(rules_v2=_NO_RULES, already_approved=True) == Decision.ALLOW


def _rules_with_outcome(outcome: RuleOutcome) -> DecisionRules:
    return DecisionRules(
        rules=(
            DecisionRule(
                rule_id="test-rule",
                predicate=RulePredicate(effect_class=_EFFECT_CLASS),
                outcome=outcome,
                rationale="test",
                human_ratified_by="marc@example.com",
            ),
        ),
    )


def test_already_approved_does_not_suppress_a_matching_deny_rule() -> None:
    """A human-ratified DENY floor (e.g. phi-egress-deny) still wins
    even on the declassified re-dispatch — the approval queue cannot
    cross a hard floor; only an Override Grant can (FR-038)."""
    assert (
        _decide(rules_v2=_rules_with_outcome(RuleOutcome.DENY), already_approved=True)
        == Decision.DENY
    )


def test_already_approved_does_not_suppress_override_required() -> None:
    assert (
        _decide(
            rules_v2=_rules_with_outcome(RuleOutcome.OVERRIDE_REQUIRED),
            already_approved=True,
        )
        == Decision.OVERRIDE_REQUIRED
    )


def test_already_approved_does_not_relax_a_legacy_deny() -> None:
    """already_approved only suppresses a *ratchet down from ALLOW*.
    It must never turn a legacy DENY (no capability matches this
    action at all) into an ALLOW."""
    unrelated_cap = Capability(
        kind=CapabilityKind.MEMORY_MODIFY,
        pattern="some-other-key",
        expiry=CapabilityExpiry.ONE_SHOT,
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=False,
    )
    result = decide(
        capabilities=frozenset({unrelated_cap}),
        action=_ACTION,
        axis_d=AxisD(),
        effect_class=_EFFECT_CLASS,
        rules_v2=_NO_RULES,
        already_approved=True,
    ).decision
    assert result == Decision.DENY
