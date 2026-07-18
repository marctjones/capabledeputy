"""#387 — mutation commands: write-through with validation. Editing policy via
`capdep posture use / rule add / label add` compiles + validates before writing,
and refuses (without touching the file) when the result would be invalid."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from capabledeputy.cli.authoring_cmds import (
    MutationRefusedError,
    label_app,
    mutate_document,
    posture_app,
    rule_app,
)
from capabledeputy.policy.authoring import ConfigError, compile_posture
from capabledeputy.policy.posture import BUILTIN_POSTURES

runner = CliRunner()


# --- grammar: posture: {use: <preset>} ------------------------------------


def test_compile_posture_use_reference_resolves_preset() -> None:
    p = compile_posture({"use": "strict"})
    assert p is BUILTIN_POSTURES["strict"]


def test_compile_posture_use_unknown_preset_fails_closed() -> None:
    with pytest.raises(ConfigError, match="unknown posture"):
        compile_posture({"use": "nope"})


# --- the pure write-through core ------------------------------------------


def test_mutate_document_applies_and_validates() -> None:
    new = mutate_document({}, lambda d: d.__setitem__("posture", {"use": "strict"}))
    assert new["posture"] == {"use": "strict"}


def test_mutate_document_refuses_invalid_result_without_mutating_input() -> None:
    current: dict = {}

    def _bad(doc: dict) -> None:
        doc["posture"] = {"id": "p", "inspectors": ["made_up_inspector"]}

    with pytest.raises(MutationRefusedError):
        mutate_document(current, _bad)
    assert current == {}  # input untouched


def test_mutate_document_compile_error_propagates() -> None:
    # a sub-floor posture won't compile -> ConfigError (not MutationRefusedError).
    with pytest.raises(ConfigError):
        mutate_document(
            {},
            lambda d: d.__setitem__(
                "posture", {"id": "x", "flow_patterns": {"restricted": "turn_level"}}
            ),
        )


# --- CLI: capdep posture use ----------------------------------------------


def test_posture_use_writes_reference(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(posture_app, ["use", "strict", "--file", str(p)])
    assert result.exit_code == 0
    assert yaml.safe_load(p.read_text())["posture"] == {"use": "strict"}


def test_posture_use_unknown_preset_refuses_and_does_not_write(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(posture_app, ["use", "bogus", "--file", str(p)])
    assert result.exit_code != 0
    assert not p.exists()  # nothing written on refusal


# --- CLI: capdep rule add -------------------------------------------------


def test_rule_add_appends_and_validates(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(
        rule_app,
        ["add", "no-ext-fin", "financial + send_email -> deny", "--file", str(p)],
    )
    assert result.exit_code == 0
    doc = yaml.safe_load(p.read_text())
    assert doc["rules"][0]["id"] == "no-ext-fin"
    assert doc["rules"][0]["when"] == "financial + send_email"
    assert doc["rules"][0]["then"] == "deny"


def test_rule_add_rejects_duplicate_id(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    p.write_text("rules:\n  - id: r\n    when: news\n    then: deny\n", encoding="utf-8")
    result = runner.invoke(rule_app, ["add", "r", "health -> deny", "--file", str(p)])
    assert result.exit_code == 1
    # the original single rule is preserved (no write on refusal).
    assert len(yaml.safe_load(p.read_text())["rules"]) == 1


def test_rule_add_bad_spec_exits(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(rule_app, ["add", "r", "no arrow here", "--file", str(p)])
    assert result.exit_code == 2


def test_rule_add_accepts_unicode_arrow(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(rule_app, ["add", "r", "news → deny", "--file", str(p)])
    assert result.exit_code == 0
    assert yaml.safe_load(p.read_text())["rules"][0]["then"] == "deny"


# --- CLI: capdep label add ------------------------------------------------


def test_label_add_declares_category(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(
        label_app, ["add", "financial", "--tier", "restricted", "--file", str(p)]
    )
    assert result.exit_code == 0
    doc = yaml.safe_load(p.read_text())
    assert doc["labels"][0] == {"category": "financial", "tier": "restricted"}


def test_label_add_rejects_duplicate(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    p.write_text("labels:\n  - category: financial\n    tier: restricted\n", encoding="utf-8")
    result = runner.invoke(label_app, ["add", "financial", "--file", str(p)])
    assert result.exit_code == 1


def test_label_add_bad_tier_refuses(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(label_app, ["add", "x", "--tier", "nonsense", "--file", str(p)])
    assert result.exit_code == 2  # compile error on the bad tier
    assert not p.exists()


# --- CLI: capdep label bind (source -> label) -----------------------------


def test_label_bind_path_writes_rule(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(
        label_app, ["bind", "/home/op/tax", "confidential.financial", "--file", str(p)]
    )
    assert result.exit_code == 0
    doc = yaml.safe_load(p.read_text())
    assert doc["label_rules"][0] == {"path": "/home/op/tax", "label": "confidential.financial"}


def test_label_bind_glob_detected(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    result = runner.invoke(label_app, ["bind", "*.key", "untrusted.external", "--file", str(p)])
    assert result.exit_code == 0
    doc = yaml.safe_load(p.read_text())
    assert doc["label_rules"][0] == {"glob": "*.key", "label": "untrusted.external"}


def test_label_bind_duplicate_refused(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    args = ["bind", "/home/op/tax", "confidential.financial", "--file", str(p)]
    assert runner.invoke(label_app, args).exit_code == 0
    assert runner.invoke(label_app, args).exit_code == 1  # identical binding refused


# --- end-to-end: a sequence of mutations composes and stays valid ---------


def test_mutation_sequence_composes(tmp_path: Path) -> None:
    p = tmp_path / "capdep.yaml"
    assert runner.invoke(posture_app, ["use", "strict", "--file", str(p)]).exit_code == 0
    assert (
        runner.invoke(
            label_app, ["add", "financial", "--tier", "restricted", "--file", str(p)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            rule_app, ["add", "r", "financial + send_email -> deny", "--file", str(p)]
        ).exit_code
        == 0
    )
    doc = yaml.safe_load(p.read_text())
    assert doc["posture"] == {"use": "strict"}
    assert doc["labels"][0]["category"] == "financial"
    assert doc["rules"][0]["id"] == "r"


# --- #309: posture list / explain (plain-language selection UX) ------------


def test_posture_list_shows_all_presets_and_floor_note() -> None:
    result = runner.invoke(posture_app, ["list"])
    assert result.exit_code == 0
    for pid in ("strict", "high-security-useful", "low-friction-practical"):
        assert pid in result.stdout
    assert "SAME security floor" in result.stdout
    assert "capdep posture use" in result.stdout


def test_posture_explain_one_preset() -> None:
    result = runner.invoke(posture_app, ["explain", "low-friction-practical"])
    assert result.exit_code == 0
    assert "Fewest approvals" in result.stdout
    assert "floor holds" in result.stdout


def test_posture_explain_unknown_exits_2() -> None:
    result = runner.invoke(posture_app, ["explain", "nope"])
    assert result.exit_code == 2
    assert "unknown preset" in result.stdout
