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
    AxisA,
    AxisACategory,
    AxisB,
    AxisBEntry,
    AxisD,
    ProvenanceLevel,
)
from capabledeputy.policy.tiers import Tier

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RULES_YAML = _REPO_ROOT / "configs" / "rules.yaml"


def test_repo_rules_yaml_loads() -> None:
    rules = load(_RULES_YAML)
    rule_ids = sorted(r.rule_id for r in rules.rules)
    assert rule_ids == [
        "block-personal-email",
        "cron-backup-auto",
        "proprietary-share-to-project-p",
    ]


def test_cron_backup_scenario_resolves_to_auto_via_yaml() -> None:
    rules = load(_RULES_YAML)
    axis_a = AxisA(
        categories=(AxisACategory(category="proprietary_work", tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(entries=(AxisBEntry(level=ProvenanceLevel.SYSTEM_INTERNAL),))
    axis_d = AxisD(
        initiator="cron:backup-job",
        authentication="device-bound",
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
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
    axis_a = AxisA(
        categories=(AxisACategory(category="proprietary_work", tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(entries=(AxisBEntry(level=ProvenanceLevel.SYSTEM_INTERNAL),))
    axis_d = AxisD(
        initiator="inbound:unauthenticated",
        authentication="none",
        expectedness="anomalous",
    )
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="backup_storage",
        target="s3://backups/db",
    )
    assert result.outcome == RuleOutcome.SUGGEST


def test_share_to_project_p_member_resolves_to_auto() -> None:
    rules = load(_RULES_YAML)
    axis_a = AxisA(
        categories=(AxisACategory(category="proprietary_work", tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("project-p",),
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="share",
        target="bob@acme.example",
    )
    assert result.outcome == RuleOutcome.AUTO
    assert result.matched_rule_ids == ("proprietary-share-to-project-p",)


def test_share_to_non_member_falls_to_suggest() -> None:
    rules = load(_RULES_YAML)
    axis_a = AxisA(
        categories=(AxisACategory(category="proprietary_work", tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        relationship_group_ids=("competitors",),  # NOT project-p
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="share",
        target="rival@example.com",
    )
    assert result.outcome == RuleOutcome.SUGGEST


def test_block_personal_email_kill_switch_denies() -> None:
    """The DENY rule fires for any personal-data email egress;
    composes most-restrictive with anything else that might match."""
    rules = load(_RULES_YAML)
    axis_a = AxisA(
        categories=(AxisACategory(category="personal", tier=Tier.SENSITIVE),),
    )
    axis_b = AxisB(entries=(AxisBEntry(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(
        initiator="principal:alice",
        authentication="device-bound",
        expectedness="expected",
    )
    result = evaluate(
        rules=rules,
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="send_email",
        target="alice@example.com",
    )
    assert result.outcome == RuleOutcome.DENY
    assert "block-personal-email" in result.matched_rule_ids
