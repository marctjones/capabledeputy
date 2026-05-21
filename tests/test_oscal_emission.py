"""Tests for OSCAL emission — spec 004 P2."""

from __future__ import annotations

import json
from pathlib import Path

from capabledeputy.compliance.oscal import (
    CAPDEP_TO_NIST_800_53,
    ControlImplementation,
    build_component_definition,
    build_control_mapping,
    emit_oscal_bundle,
)


def test_default_mapping_includes_core_rules() -> None:
    """Every Brewer-Nash + BLP + capability rule maps to NIST controls."""
    assert "untrusted-meets-egress" in CAPDEP_TO_NIST_800_53
    assert "clearance-exceeded" in CAPDEP_TO_NIST_800_53
    assert "capability-expired" in CAPDEP_TO_NIST_800_53
    assert "capability-cascaded" in CAPDEP_TO_NIST_800_53
    # All map to at least one NIST control
    for rule, controls in CAPDEP_TO_NIST_800_53.items():
        assert len(controls) > 0


def test_build_control_mapping_default() -> None:
    impls = build_control_mapping()
    assert len(impls) == len(CAPDEP_TO_NIST_800_53)
    rules = {impl.capdep_rule for impl in impls}
    assert "untrusted-meets-egress" in rules


def test_build_control_mapping_custom_overrides() -> None:
    """Operator can extend / override the default mapping."""
    custom = {
        "my-custom-rule": ["CIS-1.1", "CIS-1.2"],
        # Override default
        "untrusted-meets-egress": ["AC-4", "SC-7", "AC-16"],
    }
    impls = build_control_mapping(custom_mapping=custom)
    by_rule = {impl.capdep_rule: impl for impl in impls}
    assert "my-custom-rule" in by_rule
    assert by_rule["my-custom-rule"].nist_controls == ("CIS-1.1", "CIS-1.2")
    # Override applied
    assert "AC-16" in by_rule["untrusted-meets-egress"].nist_controls


def test_control_implementation_oscal_shape() -> None:
    impl = ControlImplementation(
        capdep_rule="test-rule",
        nist_controls=("AC-3", "AC-4"),
        description="test rule",
    )
    d = impl.to_oscal_dict()
    assert "uuid" in d
    # OSCAL implementation-of is a list of {control-id: ...}
    assert len(d["implementation-of"]) == 2
    assert d["implementation-of"][0]["control-id"] == "AC-3"
    assert d["description"] == "test rule"
    # Custom prop carries the CapDep rule name
    assert any(p["name"] == "capdep-rule" for p in d["props"])


def test_build_component_definition_shape() -> None:
    bundle = build_component_definition(capdep_version="0.9.6")
    assert "component-definition" in bundle
    cd = bundle["component-definition"]
    assert "uuid" in cd
    assert cd["metadata"]["version"] == "0.9.6"
    assert cd["metadata"]["oscal-version"] == "1.1.2"
    components = cd["components"]
    assert len(components) == 1
    comp = components[0]
    assert comp["title"] == "CapableDeputy"
    assert comp["type"] == "software"
    # Has control implementations
    ci = comp["control-implementations"][0]
    assert "implemented-requirements" in ci
    assert len(ci["implemented-requirements"]) == len(CAPDEP_TO_NIST_800_53)


def test_component_definition_source_is_nist_catalog_url() -> None:
    """The control-implementation source should reference the NIST 800-53 catalog."""
    bundle = build_component_definition()
    cd = bundle["component-definition"]
    ci = cd["components"][0]["control-implementations"][0]
    assert "NIST_SP-800-53" in ci["source"]


def test_emit_oscal_bundle_writes_valid_json(tmp_path: Path) -> None:
    out = tmp_path / "oscal-bundle.json"
    emit_oscal_bundle(out, capdep_version="0.9.6")
    assert out.is_file()
    # Loads as valid JSON
    text = out.read_text(encoding="utf-8")
    bundle = json.loads(text)
    assert "component-definition" in bundle


def test_emit_oscal_bundle_creates_parent_dir(tmp_path: Path) -> None:
    """Parent directories are created if missing."""
    out = tmp_path / "nested" / "deeply" / "oscal.json"
    emit_oscal_bundle(out)
    assert out.is_file()


def test_statement_evidence_query_links_to_audit() -> None:
    """Each control implementation has an audit-log query an auditor
    can run to surface evidence."""
    impls = build_control_mapping()
    for impl in impls:
        # Each has a non-empty query referencing the rule
        assert impl.statement_evidence_query
        assert impl.capdep_rule in impl.statement_evidence_query
