"""Tests for OSCAL System Security Plan + evidence bundle emission."""

from __future__ import annotations

import json
from pathlib import Path

from capabledeputy.compliance.ssp import (
    build_evidence_bundle,
    build_system_security_plan,
    emit_evidence_bundle,
    emit_system_security_plan,
)


def test_ssp_has_required_oscal_sections() -> None:
    ssp = build_system_security_plan(capdep_version="0.9.6")
    root = ssp["system-security-plan"]
    assert "uuid" in root
    assert root["metadata"]["oscal-version"] == "1.1.2"
    # All four major OSCAL SSP sections present
    assert "system-characteristics" in root
    assert "system-implementation" in root
    assert "control-implementation" in root
    assert "import-profile" in root


def test_ssp_metadata_carries_version_and_org() -> None:
    ssp = build_system_security_plan(
        capdep_version="0.9.6",
        organization="ACME Corp",
    )
    md = ssp["system-security-plan"]["metadata"]
    assert md["version"] == "0.9.6"
    parties = md["parties"]
    assert any(p["name"] == "ACME Corp" for p in parties)


def test_ssp_includes_all_implemented_controls() -> None:
    ssp = build_system_security_plan()
    implemented = ssp["system-security-plan"]["control-implementation"]["implemented-requirements"]
    # The default mapping should yield controls including AC-3, AC-4
    control_ids = {ir["control-id"] for ir in implemented}
    assert "AC-3" in control_ids
    assert "AC-4" in control_ids


def test_ssp_each_requirement_has_statement() -> None:
    ssp = build_system_security_plan()
    implemented = ssp["system-security-plan"]["control-implementation"]["implemented-requirements"]
    for ir in implemented:
        assert "statements" in ir
        assert len(ir["statements"]) > 0
        # Statement carries the standard {control-id}_smt id
        assert ir["statements"][0]["statement-id"].endswith("_smt")


def test_emit_ssp_writes_valid_json(tmp_path: Path) -> None:
    out = tmp_path / "ssp.json"
    emit_system_security_plan(out, capdep_version="0.9.6")
    assert out.is_file()
    loaded = json.loads(out.read_text())
    assert "system-security-plan" in loaded


def test_emit_ssp_creates_parent_dir(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "deeply" / "ssp.json"
    emit_system_security_plan(out)
    assert out.is_file()


# ---------- evidence bundle ----------


def test_evidence_bundle_groups_events_by_nist_control() -> None:
    """A policy.decided event with rule='untrusted-meets-egress'
    contributes evidence for AC-4 and SC-7 per the default mapping."""
    audit_events = [
        {
            "event_type": "policy.decided",
            "timestamp": "2026-05-21T10:00:00Z",
            "session_id": "session-1",
            "payload": {
                "rule": "untrusted-meets-egress",
                "decision": "deny",
                "tool": "email.send",
                "reason": "blocked by Brewer-Nash",
            },
        },
    ]
    bundle = build_evidence_bundle(audit_events)
    # AC-4 and SC-7 both have evidence
    assert "AC-4" in bundle
    assert "SC-7" in bundle
    # Each has one event entry
    assert len(bundle["AC-4"]) == 1
    assert bundle["AC-4"][0]["rule"] == "untrusted-meets-egress"


def test_evidence_bundle_filters_non_policy_events() -> None:
    """Only policy.decided events count as evidence."""
    audit_events = [
        {
            "event_type": "session.created",
            "timestamp": "2026-05-21T10:00:00Z",
            "session_id": "session-1",
            "payload": {},
        },
        {
            "event_type": "policy.decided",
            "timestamp": "2026-05-21T10:01:00Z",
            "session_id": "session-1",
            "payload": {"rule": "capability-expired", "decision": "deny"},
        },
    ]
    bundle = build_evidence_bundle(audit_events)
    # session.created isn't evidence; capability-expired (→ AC-2(2), AC-3, AC-6) is
    total_evidence_events = sum(len(v) for v in bundle.values())
    assert total_evidence_events >= 3  # the one event mapped to 3 controls


def test_evidence_bundle_skips_unknown_rules() -> None:
    """Events with rules NOT in the mapping are skipped (no extra controls)."""
    audit_events = [
        {
            "event_type": "policy.decided",
            "timestamp": "x",
            "payload": {"rule": "some-custom-rule", "decision": "deny"},
        },
    ]
    bundle = build_evidence_bundle(audit_events)
    assert bundle == {}


def test_emit_evidence_bundle_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "evidence.json"
    audit_events = [
        {
            "event_type": "policy.decided",
            "timestamp": "2026-05-21T10:00:00Z",
            "session_id": "s1",
            "payload": {"rule": "untrusted-meets-egress", "decision": "deny"},
        },
    ]
    emit_evidence_bundle(out, audit_events)
    assert out.is_file()
    loaded = json.loads(out.read_text())
    assert "evidence-bundle" in loaded
    eb = loaded["evidence-bundle"]
    assert "controls" in eb
    assert "summary" in eb
    assert eb["summary"]["controls_with_evidence"] >= 2  # AC-4, SC-7


def test_evidence_bundle_summary_counts() -> None:
    audit_events = [
        {
            "event_type": "policy.decided",
            "timestamp": "x",
            "payload": {"rule": "untrusted-meets-egress", "decision": "deny"},
        },
        {
            "event_type": "policy.decided",
            "timestamp": "x",
            "payload": {"rule": "capability-expired", "decision": "deny"},
        },
    ]
    bundle = build_evidence_bundle(audit_events)
    # untrusted-meets-egress → AC-4, SC-7 (2 controls x 1 event each = 2)
    # capability-expired → AC-3, AC-6, AC-2(2) (3 controls x 1 event each = 3)
    # Total evidence records = 5
    total = sum(len(v) for v in bundle.values())
    assert total == 5


def test_evidence_bundle_custom_mapping_extends() -> None:
    """Operator's custom mapping adds new controls."""
    audit_events = [
        {
            "event_type": "policy.decided",
            "timestamp": "x",
            "payload": {"rule": "my-custom-rule", "decision": "deny"},
        },
    ]
    bundle = build_evidence_bundle(
        audit_events,
        custom_mapping={"my-custom-rule": ["CIS-1.1"]},
    )
    assert "CIS-1.1" in bundle
