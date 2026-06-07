"""T036 / T045 / T037 invariants for 003 US2.

The decision-rule evaluator's core invariants:
- Empty rule set ⇒ no AUTO outcome possible (FR-011 / SC-003).
- Matching rule ⇒ rule's outcome applies; multiple matches compose
  most-restrictive (FR-026a baseline).
- Unratified rule has zero effect (FR-014).
- Non-deterministic relax input refused (FR-031 / T037).
- default_when_no_match=AUTO refused (FR-011 hard floor).
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRuleError,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
    evaluate,
    refuse_non_deterministic_relax_input,
)
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier


def _empty_label_state() -> LabelState:
    return LabelState()


def _empty_axes() -> tuple[LabelState, AxisD]:
    # Helper for tests still using axis pattern; returns label_state + axis_d
    return LabelState(), AxisD()


def test_empty_rule_set_default_is_suggest() -> None:
    """SC-003: empty rule set ⇒ never AUTO; default is SUGGEST."""
    labels, axis_d = _empty_axes()
    result = evaluate(
        rules=DecisionRules(rules=()),
        labels=labels,
        axis_d=axis_d,
        effect_class="COMMUNICATE",
        target="user@example.com",
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert result.matched_rule_ids == ()


def test_empty_rule_set_can_default_to_deny() -> None:
    """Stricter cells configure default_when_no_match=DENY explicitly."""
    labels, axis_d = _empty_axes()
    result = evaluate(
        rules=DecisionRules(rules=()),
        labels=labels,
        axis_d=axis_d,
        effect_class="ADMINISTER",
        target="*",
        default_when_no_match=RuleOutcome.DENY,
    )
    assert result.outcome == RuleOutcome.DENY


def test_default_auto_refused() -> None:
    """FR-011 hard floor: AUTO cannot be the default."""
    labels, axis_d = _empty_axes()
    with pytest.raises(DecisionRuleError, match="never-auto"):
        evaluate(
            rules=DecisionRules(rules=()),
            labels=labels,
            axis_d=axis_d,
            effect_class="OBSERVE",
            target="x",
            default_when_no_match=RuleOutcome.AUTO,
        )


def test_unratified_rule_has_zero_effect() -> None:
    """FR-014: only human-ratified rules fire."""
    rule = DecisionRule(
        rule_id="auto-backup-cron",
        predicate=RulePredicate(effect_class="MUTATE_LOCAL"),
        outcome=RuleOutcome.AUTO,
        rationale="cron backup",
        human_ratified_by=None,  # unratified
    )
    labels, axis_d = _empty_axes()
    result = evaluate(
        rules=DecisionRules(rules=(rule,)),
        labels=labels,
        axis_d=axis_d,
        effect_class="MUTATE_LOCAL",
        target="/tmp/backup",
    )
    # Unratified rule ignored; default applies.
    assert result.outcome == RuleOutcome.SUGGEST
    assert result.matched_rule_ids == ()


def test_ratified_rule_matches_and_fires() -> None:
    """A ratified rule with matching predicate yields its outcome."""
    rule = DecisionRule(
        rule_id="auto-backup-cron",
        predicate=RulePredicate(
            effect_class="MUTATE_LOCAL",
            axis_d_initiator="cron-configured-by-principal",
            axis_d_expectedness="expected",
        ),
        outcome=RuleOutcome.AUTO,
        rationale="cron backup at 2am, expected",
        human_ratified_by="marc",
    )
    labels = LabelState()
    axis_d = AxisD(
        initiator="cron-configured-by-principal",
        authentication="principal-direct",
        expectedness="expected",
    )
    result = evaluate(
        rules=DecisionRules(rules=(rule,)),
        labels=labels,
        axis_d=axis_d,
        effect_class="MUTATE_LOCAL",
        target="/var/backups",
    )
    assert result.outcome == RuleOutcome.AUTO
    assert result.matched_rule_ids == ("auto-backup-cron",)


def test_unauth_inbound_does_not_match_backup_rule() -> None:
    """US2 scenario: same effect via unauthenticated inbound is NOT
    expected; rule does not fire; default applies."""
    rule = DecisionRule(
        rule_id="auto-backup-cron",
        predicate=RulePredicate(
            effect_class="MUTATE_LOCAL",
            axis_d_initiator="cron-configured-by-principal",
            axis_d_expectedness="expected",
        ),
        outcome=RuleOutcome.AUTO,
        rationale="cron backup",
        human_ratified_by="marc",
    )
    axis_d = AxisD(
        initiator="unauth-inbound",
        authentication="none",
        expectedness="anomalous",
    )
    result = evaluate(
        rules=DecisionRules(rules=(rule,)),
        labels=LabelState(),
        axis_d=axis_d,
        effect_class="MUTATE_LOCAL",
        target="/var/backups",
        default_when_no_match=RuleOutcome.DENY,  # cell is configured strict
    )
    assert result.outcome == RuleOutcome.DENY
    assert result.matched_rule_ids == ()


def test_multiple_matching_rules_compose_most_restrictive() -> None:
    """FR-026a baseline: multiple matches ⇒ min(outcomes) by rank."""
    permissive = DecisionRule(
        rule_id="permissive",
        predicate=RulePredicate(effect_class="FETCH"),
        outcome=RuleOutcome.AUTO,
        rationale="lenient fetch",
        human_ratified_by="marc",
    )
    strict = DecisionRule(
        rule_id="strict",
        predicate=RulePredicate(effect_class="FETCH"),
        outcome=RuleOutcome.REQUIRE_APPROVAL,
        rationale="strict on this kind",
        human_ratified_by="marc",
    )
    result = evaluate(
        rules=DecisionRules(rules=(permissive, strict)),
        labels=LabelState(),
        axis_d=AxisD(),
        effect_class="FETCH",
        target="https://example.com",
    )
    assert result.outcome == RuleOutcome.REQUIRE_APPROVAL
    assert set(result.matched_rule_ids) == {"permissive", "strict"}


def test_predicate_axis_a_category_must_match() -> None:
    rule = DecisionRule(
        rule_id="health-only",
        predicate=RulePredicate(axis_a_category="health"),
        outcome=RuleOutcome.SUGGEST,
        rationale="only fires for health data",
        human_ratified_by="marc",
    )
    health_state = LabelState(
        a=frozenset({CategoryTag(category="health", tier=Tier.REGULATED)}),
    )
    other_state = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
    )
    r_match = evaluate(
        rules=DecisionRules(rules=(rule,)),
        labels=health_state,
        axis_d=AxisD(),
        effect_class="OBSERVE",
        target="x",
    )
    r_miss = evaluate(
        rules=DecisionRules(rules=(rule,)),
        labels=other_state,
        axis_d=AxisD(),
        effect_class="OBSERVE",
        target="x",
    )
    assert r_match.matched_rule_ids == ("health-only",)
    assert r_miss.matched_rule_ids == ()


def test_predicate_axis_b_provenance_must_match() -> None:
    rule = DecisionRule(
        rule_id="trusted-only",
        predicate=RulePredicate(axis_b_provenance="principal-direct"),
        outcome=RuleOutcome.AUTO,
        rationale="trusted provenance",
        human_ratified_by="marc",
    )
    trusted = LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}))
    untrusted = LabelState(b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    r_match = evaluate(
        rules=DecisionRules(rules=(rule,)),
        labels=trusted,
        axis_d=AxisD(),
        effect_class="OBSERVE",
        target="x",
    )
    r_miss = evaluate(
        rules=DecisionRules(rules=(rule,)),
        labels=untrusted,
        axis_d=AxisD(),
        effect_class="OBSERVE",
        target="x",
    )
    assert r_match.outcome == RuleOutcome.AUTO
    assert r_miss.outcome == RuleOutcome.SUGGEST


def test_refuse_non_deterministic_relax_input_llm() -> None:
    """FR-031 / T037: any relax input from llm-suggested origin refused."""
    with pytest.raises(DecisionRuleError, match="FR-031"):
        refuse_non_deterministic_relax_input(input_origin="llm-suggested")


def test_refuse_non_deterministic_relax_input_unratified() -> None:
    with pytest.raises(DecisionRuleError, match="FR-031"):
        refuse_non_deterministic_relax_input(input_origin="unratified")


def test_refuse_non_deterministic_relax_input_curated_ok() -> None:
    """curated-mcp is on the allow list (deterministic operator source)."""
    refuse_non_deterministic_relax_input(input_origin="curated-mcp")
    refuse_non_deterministic_relax_input(input_origin="operator-config")
    refuse_non_deterministic_relax_input(input_origin="human-ratified-rule")


# --- Time-window predicate fail-closed (Principle VI) -------------------


def _time_window_rule(window: tuple[int, int]) -> DecisionRules:
    """Helper: minimal rule that fires only inside `window`."""
    return DecisionRules(
        rules=(
            DecisionRule(
                rule_id="time-bound-test",
                predicate=RulePredicate(
                    effect_class="send_email",
                    axis_d_time_window=window,
                ),
                outcome=RuleOutcome.REQUIRE_APPROVAL,
                rationale="time-window test",
                human_ratified_by="op",
            ),
        ),
    )


def test_time_window_rule_does_not_fire_without_now_hour() -> None:
    """Principle VI fail-closed: a rule that declares a time window
    MUST receive a now_hour. When the caller omits it the rule does
    NOT match — composition falls to the default SUGGEST. Without
    this fix the rule would silently match regardless of time, which
    is fail-OPEN for any AUTO time-window rule."""
    rules = _time_window_rule((22, 6))
    labels, axis_d = _empty_axes()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="x@example.com",
        # now_hour omitted — fail-closed
    )
    assert result.outcome == RuleOutcome.SUGGEST  # default
    assert result.matched_rule_ids == ()


def test_time_window_rule_fires_inside_window() -> None:
    """At 23:00 the wrap-midnight window 22..6 matches and the rule's
    REQUIRE_APPROVAL composes."""
    rules = _time_window_rule((22, 6))
    labels, axis_d = _empty_axes()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="x@example.com",
        now_hour=23,
    )
    assert result.outcome == RuleOutcome.REQUIRE_APPROVAL
    assert "time-bound-test" in result.matched_rule_ids


def test_time_window_rule_silent_outside_window() -> None:
    """At 14:00 (well outside 22..6) the rule does not match;
    composition falls through to default SUGGEST."""
    rules = _time_window_rule((22, 6))
    labels, axis_d = _empty_axes()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="x@example.com",
        now_hour=14,
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert result.matched_rule_ids == ()
