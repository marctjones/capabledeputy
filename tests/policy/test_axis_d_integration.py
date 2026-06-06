"""T039 — Axis-D decision context drives the cron-vs-unauth distinction.

The same nominal *effect* (storage backup) reaches opposite outcomes
depending on Axis-D context (US2 scenario 2):

  * `initiator=cron:backup-job, expectedness=expected` ⇒ `auto`
    via a human-ratified rule.
  * `initiator=inbound:unauthenticated, expectedness=anomalous` ⇒
    `suggest` (the never-auto default; the cron rule does not match).

This is the marquee test for FR-006/FR-010/FR-011 working together:
expectedness + initiator authentication together discriminate
indistinguishable-by-effect cases.
"""

from __future__ import annotations

from capabledeputy.policy.decision_rules import (
    DecisionRule,
    DecisionRules,
    RuleOutcome,
    RulePredicate,
    evaluate,
)
from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    AxisD,
    CategoryTag,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier


def _cron_backup_rule() -> DecisionRule:
    """Human-ratified rule authorizing the backup-job cron under
    expected context. The rule does NOT mention time-of-day — that's
    the ExpectationBinding's job (T040). What the DecisionRule sees
    is `expectedness=expected` already resolved upstream."""
    return DecisionRule(
        rule_id="cron-backup-auto",
        predicate=RulePredicate(
            axis_d_initiator="cron:backup-job",
            axis_d_expectedness="expected",
            effect_class="backup_storage",
        ),
        outcome=RuleOutcome.AUTO,
        rationale="cron-initiated backups under expected schedule are routine",
        human_ratified_by="marc@example.com",
    )


def _common_axes() -> tuple[AxisA, AxisB]:
    """Both scenarios share Axis A (proprietary work) and Axis B
    (system-internal — the data we're backing up was already in
    our own systems)."""
    axis_a = AxisA(
        categories=(CategoryTag(category="proprietary_work", tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(
        entries=(ProvenanceTag(level=ProvenanceLevel.SYSTEM_INTERNAL),),
    )
    return axis_a, axis_b


def test_cron_initiated_backup_resolves_to_auto() -> None:
    axis_a, axis_b = _common_axes()
    axis_d = AxisD.from_dict(
        {
            "initiator": "cron:backup-job",
            "authentication": "device-bound",
            "expectedness": "expected",
            "reversibility": {"degree": "reversible", "agent": "system"},
        }
    )
    rules = DecisionRules(rules=(_cron_backup_rule(),))
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="backup_storage",
        target="s3://backups/db-snapshot",
    )
    assert result.outcome == RuleOutcome.AUTO
    assert result.matched_rule_ids == ("cron-backup-auto",)


def test_unauth_inbound_same_effect_does_not_match_cron_rule() -> None:
    """The same effect_class + same target, but initiator/expectedness
    diverge. The cron rule does not match — the never-auto default
    (SUGGEST) holds."""
    axis_a, axis_b = _common_axes()
    axis_d = AxisD.from_dict(
        {
            "initiator": "inbound:unauthenticated",
            "authentication": "none",
            "expectedness": "anomalous",
            "reversibility": {"degree": "reversible", "agent": "system"},
        }
    )
    rules = DecisionRules(rules=(_cron_backup_rule(),))
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="backup_storage",
        target="s3://backups/db-snapshot",
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert result.matched_rule_ids == ()


def test_cron_initiated_but_unexpected_does_not_match() -> None:
    """Cron initiator alone is not enough — expectedness must also be
    `expected` (an ExpectationBinding match). Cron that fires at the
    wrong window is `anomalous` and the rule does not match."""
    axis_a, axis_b = _common_axes()
    axis_d = AxisD.from_dict(
        {
            "initiator": "cron:backup-job",
            "authentication": "device-bound",
            "expectedness": "anomalous",  # window mismatch upstream
            "reversibility": {"degree": "reversible", "agent": "system"},
        }
    )
    rules = DecisionRules(rules=(_cron_backup_rule(),))
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="backup_storage",
        target="s3://backups/db-snapshot",
    )
    assert result.outcome == RuleOutcome.SUGGEST


def test_authenticated_inbound_principal_is_not_cron() -> None:
    """A principal-initiated equivalent action (alice asks to back up
    her own DB) does NOT match the cron rule — initiator string
    differs — so it falls to never-auto SUGGEST. A separate rule for
    `principal:alice` would be needed to grant her AUTO; that's the
    whole point of FR-031 asymmetry (per-cell rules, not heuristic)."""
    axis_a, axis_b = _common_axes()
    axis_d = AxisD.from_dict(
        {
            "initiator": "principal:alice",
            "authentication": "device-bound",
            "expectedness": "expected",
            "reversibility": {"degree": "reversible", "agent": "system"},
        }
    )
    rules = DecisionRules(rules=(_cron_backup_rule(),))
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="backup_storage",
        target="s3://backups/db-snapshot",
    )
    assert result.outcome == RuleOutcome.SUGGEST
