"""T049 — `configs/rules.yaml` example loads and exercises the US2
scenario battery.

This test loads the actual operator config shipped with the repo
(not a fixture) and runs each scenario in the file's comment block
against the evaluator. The contract: every rule in `configs/rules.yaml`
must be loadable, every rule_id must be unique, and each documented
scenario must reach its expected outcome (auto/suggest/deny).
"""

from __future__ import annotations

from pathlib import Path

from capabledeputy.policy.decision_rules import RuleOutcome, evaluate, load
from capabledeputy.policy.labels import (
    AxisD,
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULES_YAML = _REPO_ROOT / "configs" / "rules.yaml"


def test_repo_rules_yaml_loads() -> None:
    rules = load(_RULES_YAML)
    rule_ids = sorted(r.rule_id for r in rules.rules)
    assert rule_ids == [
        "cron-backup-auto",
        "family-personal-email-suggest",
        "phi-egress-deny",
        "proprietary-share-to-project-p",
        "purchases-under-threshold-auto",
        "send-after-hours-require-approval",
        "work-team-email-suggest",
    ]


def test_cron_backup_scenario_resolves_to_auto_via_yaml() -> None:
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="proprietary_work", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.SYSTEM_INTERNAL)}),
    )
    axis_d = AxisD(
        initiator="cron:backup-job",
        authentication="device-bound",
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="backup_storage",
        target="s3://backups/db",
    )
    assert result.outcome == RuleOutcome.AUTO
    assert result.matched_rule_ids == ("cron-backup-auto",)


def test_unauth_inbound_backup_falls_to_default_suggest() -> None:
    """Same effect_class, different axis_d — no rule matches, default
    SUGGEST holds (FR-011 never-auto)."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="proprietary_work", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.SYSTEM_INTERNAL)}),
    )
    axis_d = AxisD(
        initiator="inbound:unauthenticated",
        authentication="none",
        expectedness="anomalous",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="backup_storage",
        target="s3://backups/db",
    )
    assert result.outcome == RuleOutcome.SUGGEST


def test_share_to_project_p_member_resolves_to_auto() -> None:
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="proprietary_work", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("project-p",),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="share",
        target="bob@acme.example",
    )
    assert result.outcome == RuleOutcome.AUTO
    assert result.matched_rule_ids == ("proprietary-share-to-project-p",)


def test_share_to_non_member_falls_to_suggest() -> None:
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="proprietary_work", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("competitors",),  # NOT project-p
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="share",
        target="rival@example.com",
    )
    assert result.outcome == RuleOutcome.SUGGEST


def test_personal_email_to_family_member_resolves_to_suggest() -> None:
    """Personal mail summary forwarded to a family-group recipient
    matches the `family-personal-email-suggest` rule. Outcome is
    SUGGEST — irreversible egress still requires an approval card,
    but the rule marks the counterparty as recognized rather than
    falling to the default unknown."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("family",),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="spouse@example.com",
        now_hour=14,  # business hours — after-hours rule does not fire
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert "family-personal-email-suggest" in result.matched_rule_ids


def test_personal_email_to_non_family_falls_to_default_suggest() -> None:
    """Personal mail to a non-family recipient finds no rule; the
    default fail-closed SUGGEST cell holds. Distinguishable from the
    family case by the empty matched_rule_ids — the approval card UX
    can use that to surface the counterparty as unknown rather than
    recognized."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=(),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="stranger@example.com",
        now_hour=14,  # business hours — after-hours rule does not fire
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert result.matched_rule_ids == ()


def test_work_email_to_workteam_member_resolves_to_suggest() -> None:
    """Symmetric to the family-personal rule but for work content
    forwarded to a recognized work-team member."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="proprietary_work", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("work-team",),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="coworker@example.com",
        now_hour=14,  # business hours — after-hours rule does not fire
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert "work-team-email-suggest" in result.matched_rule_ids


def test_work_email_to_family_member_falls_to_default_suggest() -> None:
    """Cross-compartment send (work content → family recipient) does
    NOT match work-team-email-suggest because the counterparty group
    differs. Falls to default SUGGEST — operator approval card will
    surface this as an unrecognized counterparty for the work axis."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="proprietary_work", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("family",),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="spouse@example.com",
        now_hour=14,  # business hours — after-hours rule does not fire
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert result.matched_rule_ids == ()


# --- Cookbook §9 stanza tests --------------------------------------------


def test_send_at_night_escalates_to_require_approval() -> None:
    """A send to a family-group recipient at 23:00 (within the
    22-06 after-hours window) composes the family-suggest rule
    with the after-hours rule. Most-restrictive wins —
    REQUIRE_APPROVAL beats SUGGEST."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("family",),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="spouse@example.com",
        now_hour=23,  # within 22-06 after-hours window
    )
    assert result.outcome == RuleOutcome.REQUIRE_APPROVAL
    assert "send-after-hours-require-approval" in result.matched_rule_ids
    assert "family-personal-email-suggest" in result.matched_rule_ids


def test_phi_egress_denied_even_to_family() -> None:
    """PHI never leaves the device without an OverrideGrant. The
    phi-egress-deny rule composes most-restrictive with any other
    rule — even a family-group recipient is denied, preventing
    accidental disclosure of health data to a spouse who isn't
    the patient."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="phi", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("family",),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="send_email",
        target="spouse@example.com",
        now_hour=14,
    )
    assert result.outcome == RuleOutcome.DENY
    assert "phi-egress-deny" in result.matched_rule_ids


def test_small_reversible_purchase_auto() -> None:
    """A queue_purchase whose deterministic reversibility resolver
    returns 'reversible' (returnable consumable within window) hits
    the AUTO rule — no approval card. Operator-set cap on
    QUEUE_PURCHASE max_amount is the dollar-level guard."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        reversibility={"degree": "reversible", "agent": "system"},
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="queue_purchase",
        target="amazon",
        now_hour=14,
    )
    assert result.outcome == RuleOutcome.AUTO
    assert "purchases-under-threshold-auto" in result.matched_rule_ids


def test_irreversible_purchase_falls_to_default_suggest() -> None:
    """A purchase whose reversibility is irreversible (concert
    ticket, custom build) does NOT hit the auto rule — falls to
    the FR-011 SUGGEST default cell. Approval card required."""
    rules = load(_RULES_YAML)
    labels = LabelState(
        a=frozenset({CategoryTag(category="personal", tier=Tier.SENSITIVE)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT)}),
    )
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        reversibility={"degree": "irreversible", "agent": "external"},
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        labels=labels,
        axis_d=axis_d,
        effect_class="queue_purchase",
        target="ticketmaster",
        now_hour=14,
    )
    assert result.outcome == RuleOutcome.SUGGEST
    assert result.matched_rule_ids == ()
