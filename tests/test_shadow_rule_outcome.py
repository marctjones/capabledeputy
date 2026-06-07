"""Cookbook P2.8 — per-rule SHADOW outcome.

Lets operators author a new rule, set its outcome to `shadow`, deploy
it, and watch what it WOULD do for K turns via
EvaluationResult.shadowed_rule_ids in the audit trail. Once
confident, the operator promotes it to a real outcome (deny /
suggest / auto) and the rule begins composing into decisions.

Complements the per-session Pattern ⑥ shadow mode: that one
silences a whole session; this one silences a single rule across
all sessions.

Tests:
  - RuleOutcome enum has SHADOW value
  - SHADOW deliberately absent from _OUTCOME_RANK (fail-closed if
    fed to composition)
  - YAML "shadow" string deserializes into RuleOutcome.SHADOW
  - Shadow rule matching is recorded in shadowed_rule_ids
  - Shadow rule does NOT enter the most-restrictive composition
  - With shadow + real rules matching, the real rule's outcome
    governs; shadow stays in shadowed_rule_ids
  - With only shadow rules matching, evaluator falls to default
  - rationale text surfaces shadowed rules so operators can spot
    them in the audit log
  - Back-compat: pre-cookbook EvaluationResult constructions still
    work (shadowed_rule_ids defaults to ())
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.decision_rules import (
    _OUTCOME_RANK,
    DecisionRule,
    DecisionRules,
    EvaluationResult,
    RuleOutcome,
    RulePredicate,
    evaluate,
)
from capabledeputy.policy.labels import AxisD, LabelState


def _label_state() -> LabelState:
    return LabelState()


def _make_rule(
    rule_id: str,
    *,
    outcome: RuleOutcome,
    effect_class: str = "send_email",
) -> DecisionRule:
    return DecisionRule(
        rule_id=rule_id,
        predicate=RulePredicate(effect_class=effect_class),
        outcome=outcome,
        rationale="test rule",
        human_ratified_by="op",
    )


# --- Enum + composition guards ------------------------------------------


def test_shadow_value_present_in_enum() -> None:
    assert RuleOutcome("shadow") == RuleOutcome.SHADOW
    assert RuleOutcome.SHADOW.value == "shadow"


def test_shadow_excluded_from_outcome_rank() -> None:
    """SHADOW is deliberately absent from the rank table. Any
    accidental composition with SHADOW must fail loudly, not
    silently rank-degrade."""
    assert RuleOutcome.SHADOW not in _OUTCOME_RANK


# --- Evaluator behavior --------------------------------------------------


def test_shadow_only_match_falls_to_default() -> None:
    """When the only matched rule is in shadow, the composition
    falls to the never-auto default. shadowed_rule_ids surfaces
    what would have fired so the operator can verify intent."""
    rules = DecisionRules(rules=(_make_rule("shadow-test", outcome=RuleOutcome.SHADOW),))
    labels = _label_state()
    d = AxisD()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    assert result.outcome == RuleOutcome.SUGGEST  # default
    assert result.matched_rule_ids == ()
    assert result.shadowed_rule_ids == ("shadow-test",)
    assert "shadowed_rules" in result.rationale


def test_shadow_does_not_block_live_rule() -> None:
    """A real DENY rule + a shadow AUTO rule on the same action →
    live DENY wins. The shadow rule is invisible to composition
    but visible in shadowed_rule_ids."""
    rules = DecisionRules(
        rules=(
            _make_rule("real-deny", outcome=RuleOutcome.DENY),
            _make_rule("shadow-auto", outcome=RuleOutcome.SHADOW),
        ),
    )
    labels = _label_state()
    d = AxisD()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    assert result.outcome == RuleOutcome.DENY
    assert result.matched_rule_ids == ("real-deny",)
    assert result.shadowed_rule_ids == ("shadow-auto",)


def test_shadow_excluded_from_most_restrictive() -> None:
    """If we had a shadow DENY rule, composition would normally
    pick DENY (most-restrictive). Confirming the shadow IS
    excluded — a shadow DENY + live AUTO leaves AUTO unaffected."""
    rules = DecisionRules(
        rules=(
            _make_rule("shadow-deny", outcome=RuleOutcome.SHADOW),
            _make_rule("real-auto", outcome=RuleOutcome.AUTO),
        ),
    )
    labels = _label_state()
    d = AxisD()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    assert result.outcome == RuleOutcome.AUTO
    assert result.matched_rule_ids == ("real-auto",)
    assert "shadow-deny" in result.shadowed_rule_ids


def test_multiple_shadow_rules_all_recorded() -> None:
    """All matching shadow rules surface in shadowed_rule_ids
    sorted, regardless of composition outcome."""
    rules = DecisionRules(
        rules=(
            _make_rule("shadow-b", outcome=RuleOutcome.SHADOW),
            _make_rule("shadow-a", outcome=RuleOutcome.SHADOW),
            _make_rule("real-suggest", outcome=RuleOutcome.SUGGEST),
        ),
    )
    labels = _label_state()
    d = AxisD()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    assert result.shadowed_rule_ids == ("shadow-a", "shadow-b")


def test_no_match_no_shadow_no_changes() -> None:
    """Back-compat: when no rule matches at all, shadowed_rule_ids
    is empty and the result is indistinguishable from pre-cookbook."""
    rules = DecisionRules(rules=())
    labels = _label_state()
    d = AxisD()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    assert result.shadowed_rule_ids == ()
    assert result.matched_rule_ids == ()


def test_evaluation_result_field_defaults() -> None:
    """Constructing EvaluationResult without shadowed_rule_ids works
    — back-compat for any test fixture or external caller."""
    r = EvaluationResult(
        outcome=RuleOutcome.SUGGEST,
        matched_rule_ids=("x",),
        rationale="legacy",
    )
    assert r.shadowed_rule_ids == ()


# --- YAML loader -------------------------------------------------------


def test_loader_accepts_shadow_outcome(tmp_path) -> None:
    """A rule with outcome: shadow in YAML deserializes into a
    DecisionRule with outcome=RuleOutcome.SHADOW. Required for
    operators to actually use the feature from configs/rules.yaml."""
    from capabledeputy.policy.decision_rules import load

    path = tmp_path / "rules.yaml"
    path.write_text(
        "rules:\n"
        "  - rule_id: test-shadow\n"
        "    when:\n"
        "      axis_c:\n"
        "        effect_class: send_email\n"
        "    outcome: shadow\n"
        "    rationale: shadow-test\n"
        "    risk_ids: []\n"
        "    human_ratified_by: op\n"
        "    human_ratified_at: 2026-06-04T00:00:00Z\n",
        encoding="utf-8",
    )
    rules = load(path)
    assert len(rules.rules) == 1
    assert rules.rules[0].outcome == RuleOutcome.SHADOW


def test_rationale_distinguishes_shadow_only_from_no_match() -> None:
    """Operators reading the audit log need to tell 'no rule
    matched at all' from 'only shadow rules matched'. The
    rationale text carries the shadowed_rules list when one is
    present."""
    rules_shadow_only = DecisionRules(
        rules=(_make_rule("only-shadow", outcome=RuleOutcome.SHADOW),),
    )
    rules_no_match = DecisionRules(rules=())
    labels = _label_state()
    d = AxisD()
    r_shadow = evaluate(
        rules=rules_shadow_only,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    r_empty = evaluate(
        rules=rules_no_match,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    # Both outcomes are SUGGEST (default), but the rationale must
    # tell them apart.
    assert r_shadow.outcome == r_empty.outcome == RuleOutcome.SUGGEST
    assert "shadowed_rules" in r_shadow.rationale
    assert "shadowed_rules" not in r_empty.rationale


@pytest.mark.parametrize(
    "outcome",
    [
        RuleOutcome.DENY,
        RuleOutcome.SUGGEST,
        RuleOutcome.AUTO,
        RuleOutcome.OVERRIDE_REQUIRED,
        RuleOutcome.REQUIRE_APPROVAL,
    ],
)
def test_shadow_does_not_alter_live_outcomes(outcome: RuleOutcome) -> None:
    """For every real outcome class, adding a shadow rule that also
    matches leaves the live composition unchanged. Parametric guard
    against drift."""
    rules = DecisionRules(
        rules=(
            _make_rule("live", outcome=outcome),
            _make_rule("ignore-me", outcome=RuleOutcome.SHADOW),
        ),
    )
    labels = _label_state()
    d = AxisD()
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=d,
        effect_class="send_email",
        target="x@example.com",
    )
    assert result.outcome == outcome
    assert "ignore-me" in result.shadowed_rule_ids
