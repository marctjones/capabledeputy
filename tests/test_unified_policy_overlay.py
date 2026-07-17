"""Migration (epic #377): the unified `capdep.yaml` overlay at daemon start.

A present configs/capdep.yaml compiles + validates and overlays the decision
structures it declares (rules, envelopes) onto the per-file PolicyContext; an
absent file is a no-op; an invalid one refuses start (fail-closed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.daemon.lifecycle import overlay_unified_policy_from_config
from capabledeputy.policy.authoring import ConfigError
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.decision_rules import RuleOutcome


def _base_context() -> PolicyContext:
    return PolicyContext()


def test_absent_capdep_yaml_is_a_noop(tmp_path: Path) -> None:
    pc = _base_context()
    out, messages = overlay_unified_policy_from_config(pc, configs_dir=tmp_path)
    assert out is pc
    assert messages == []


def test_overlays_rules_from_capdep_yaml(tmp_path: Path) -> None:
    (tmp_path / "capdep.yaml").write_text(
        "rules:\n  - id: no-ext-fin\n    when: financial + send_email + external\n    then: deny\n",
        encoding="utf-8",
    )
    out, messages = overlay_unified_policy_from_config(_base_context(), configs_dir=tmp_path)
    assert out.rules_v2 is not None
    assert len(out.rules_v2.rules) == 1
    assert out.rules_v2.rules[0].rule_id == "no-ext-fin"
    assert out.rules_v2.rules[0].outcome == RuleOutcome.DENY
    assert any("unified policy active" in m for m in messages)


def test_overlays_envelopes_from_capdep_yaml(tmp_path: Path) -> None:
    when = "financial + send_email + initiator:principal:owner + reversibility:irreversible"
    (tmp_path / "capdep.yaml").write_text(
        f"envelopes:\n  - when: {when}\n    range: [approve, allow]\n",
        encoding="utf-8",
    )
    out, _ = overlay_unified_policy_from_config(_base_context(), configs_dir=tmp_path)
    assert out.envelope_set is not None
    assert len(out.envelope_set.by_cell) == 1


def test_empty_sections_leave_context_unchanged(tmp_path: Path) -> None:
    (tmp_path / "capdep.yaml").write_text(
        "posture:\n  id: strict\n  dial: cautious\n", encoding="utf-8"
    )
    pc = _base_context()
    out, messages = overlay_unified_policy_from_config(pc, configs_dir=tmp_path)
    # No rules/envelopes declared -> nothing to overlay onto rules_v2/envelope_set.
    assert out.rules_v2 is None
    assert messages == []


def test_invalid_capdep_yaml_refuses_start(tmp_path: Path) -> None:
    # A posture naming an unknown inspector is an error-severity check problem.
    (tmp_path / "capdep.yaml").write_text(
        "posture:\n  id: p\n  inspectors: [made_up]\n"
        "rules:\n  - id: r\n    when: news\n    then: deny\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="failed policy check"):
        overlay_unified_policy_from_config(_base_context(), configs_dir=tmp_path)


def test_uncompilable_capdep_yaml_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "capdep.yaml").write_text(
        "rules:\n  - id: r\n    when: news\n    then: not_an_outcome\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        overlay_unified_policy_from_config(_base_context(), configs_dir=tmp_path)
