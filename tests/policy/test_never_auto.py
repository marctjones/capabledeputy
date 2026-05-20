"""T036 — Never-auto default invariant (FR-011 / SC-003).

With an empty rule set, no combination of axis values may produce
`auto`. The default cell outcome is `suggest` or `deny` — both
collapse to REQUIRE_APPROVAL/DENY at the legacy chokepoint, never
ALLOW from the v2 leg.

These tests stress the invariant across a representative grid of
axis configurations (not exhaustive — combinatorial, but enough
to catch a regression where any axis "leaks" toward AUTO without
a ratified rule).
"""

from __future__ import annotations

import itertools

from capabledeputy.policy.decision_rules import (
    DecisionRules,
    RuleOutcome,
    evaluate,
)
from capabledeputy.policy.labels import (
    AxisA,
    AxisACategory,
    AxisB,
    AxisBEntry,
    AxisD,
    ProvenanceLevel,
)
from capabledeputy.policy.tiers import Tier

_CATEGORIES = ["personal", "health", "financial", "proprietary_work", "public"]
_PROVENANCES = [
    ProvenanceLevel.PRINCIPAL_DIRECT,
    ProvenanceLevel.SYSTEM_INTERNAL,
    ProvenanceLevel.EXTERNAL_UNTRUSTED,
]
_INITIATORS = [
    "principal:alice",
    "cron:backup-job",
    "inbound:unauthenticated",
    "unset",
]
_EFFECT_CLASSES = ["send_email", "read_file", "write_file", "queue_purchase"]
_TARGETS = ["alice@example.com", "/etc/passwd", "doc-123", "destination-x"]


def test_empty_rule_set_yields_only_suggest_for_default_suggest() -> None:
    """Every combination resolves to SUGGEST when rules are empty and
    default is SUGGEST. No AUTO leak anywhere in the grid."""
    rules = DecisionRules(rules=())
    saw_auto = False
    sample_count = 0
    for cat, prov, init, eff, target in itertools.product(
        _CATEGORIES,
        _PROVENANCES,
        _INITIATORS,
        _EFFECT_CLASSES,
        _TARGETS,
    ):
        axis_a = AxisA(
            categories=(AxisACategory(category=cat, tier=Tier.SENSITIVE),),
        )
        axis_b = AxisB(entries=(AxisBEntry(level=prov),))
        axis_d = AxisD(initiator=init)
        result = evaluate(
            rules=rules,
            axis_a=axis_a,
            axis_b=axis_b,
            axis_d=axis_d,
            effect_class=eff,
            target=target,
        )
        if result.outcome == RuleOutcome.AUTO:
            saw_auto = True
        assert result.outcome == RuleOutcome.SUGGEST, (
            f"empty rules with {cat}/{prov}/{init}/{eff}/{target} "
            f"yielded {result.outcome.value} instead of SUGGEST"
        )
        sample_count += 1
    assert not saw_auto
    # Grid size sanity: 5 * 3 * 4 * 4 * 4 = 960
    assert sample_count == 960


def test_empty_rule_set_yields_only_deny_for_default_deny() -> None:
    """A stricter cell may default to DENY. Still never AUTO."""
    rules = DecisionRules(rules=())
    for cat in _CATEGORIES:
        axis_a = AxisA(
            categories=(AxisACategory(category=cat, tier=Tier.PROHIBITED),),
        )
        axis_b = AxisB(
            entries=(AxisBEntry(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),),
        )
        axis_d = AxisD(initiator="inbound:unauthenticated")
        result = evaluate(
            rules=rules,
            axis_a=axis_a,
            axis_b=axis_b,
            axis_d=axis_d,
            effect_class="send_email",
            target="x",
            default_when_no_match=RuleOutcome.DENY,
        )
        assert result.outcome == RuleOutcome.DENY


def test_unratified_rules_cannot_produce_auto_even_if_they_match() -> None:
    """FR-014: a rule with no `human_ratified_by` produces zero effect.
    The never-auto default holds even if an unratified AUTO rule
    would have otherwise matched every input."""
    from capabledeputy.policy.decision_rules import DecisionRule, RulePredicate

    universal_unratified_auto = DecisionRule(
        rule_id="universal-auto",
        predicate=RulePredicate(),  # wildcard — matches anything
        outcome=RuleOutcome.AUTO,
        rationale="DRAFT — not yet ratified by a human",
        human_ratified_by=None,
    )
    rules = DecisionRules(rules=(universal_unratified_auto,))
    for cat in _CATEGORIES:
        axis_a = AxisA(categories=(AxisACategory(category=cat, tier=Tier.SENSITIVE),))
        result = evaluate(
            rules=rules,
            axis_a=axis_a,
            axis_b=AxisB(entries=(AxisBEntry(level=ProvenanceLevel.SYSTEM_INTERNAL),)),
            axis_d=AxisD(),
            effect_class="send_email",
            target="any",
        )
        assert result.outcome == RuleOutcome.SUGGEST
        assert result.matched_rule_ids == ()
