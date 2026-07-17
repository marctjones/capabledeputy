"""#384 — one format: the risk-preference and risk-register loaders are
format-agnostic (YAML, a JSON superset) and resolve a `.json`↔`.yaml` sibling so
files can migrate without touching call sites."""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.policy.config_format import resolve_config_path
from capabledeputy.policy.envelope import (
    EnvelopeError,
    RiskPreference,
    load_risk_preference,
)
from capabledeputy.policy.risk_register import load as load_risk_register


def test_resolve_config_path_prefers_existing_then_sibling(tmp_path: Path) -> None:
    j = tmp_path / "x.json"
    y = tmp_path / "x.yaml"
    # neither exists -> returns the asked-for path unchanged.
    assert resolve_config_path(j) == j
    # only the yaml sibling exists -> a .json request resolves to it.
    y.write_text("value: cautious\n", encoding="utf-8")
    assert resolve_config_path(j) == y
    # the exact path wins when it exists.
    j.write_text("{}", encoding="utf-8")
    assert resolve_config_path(j) == j


def test_risk_preference_loads_yaml(tmp_path: Path) -> None:
    p = tmp_path / "risk_preference.yaml"
    p.write_text("value: balanced\nversion: 3\n", encoding="utf-8")
    prof = load_risk_preference(p)
    assert prof.value == RiskPreference.BALANCED
    assert prof.version == 3


def test_risk_preference_still_loads_json(tmp_path: Path) -> None:
    p = tmp_path / "risk_preference.json"
    p.write_text('{"value": "permissive"}', encoding="utf-8")
    assert load_risk_preference(p).value == RiskPreference.PERMISSIVE


def test_risk_preference_json_path_falls_back_to_yaml(tmp_path: Path) -> None:
    (tmp_path / "risk_preference.yaml").write_text("value: cautious\n", encoding="utf-8")
    # caller asks for .json; loader finds the .yaml sibling.
    assert load_risk_preference(tmp_path / "risk_preference.json").value == RiskPreference.CAUTIOUS


def test_risk_preference_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(EnvelopeError, match="missing"):
        load_risk_preference(tmp_path / "nope.json")
    bad = tmp_path / "risk_preference.yaml"
    bad.write_text("value: not_a_dial\n", encoding="utf-8")
    with pytest.raises(EnvelopeError, match="value"):
        load_risk_preference(bad)


def test_risk_register_loads_yaml(tmp_path: Path) -> None:
    p = tmp_path / "risk_register.yaml"
    p.write_text(
        "entries:\n  - id: RISK-X\n    summary: X\n    framework_refs: [OWASP-LLM01]\n",
        encoding="utf-8",
    )
    reg = load_risk_register(p)
    assert reg.exists("RISK-X")


def test_risk_register_json_path_falls_back_to_yaml(tmp_path: Path) -> None:
    (tmp_path / "risk_register.yaml").write_text(
        "entries:\n  - id: RISK-Y\n    summary: Y\n    framework_refs: [OWASP-LLM01]\n",
        encoding="utf-8",
    )
    reg = load_risk_register(tmp_path / "risk_register.json")
    assert reg.exists("RISK-Y")
