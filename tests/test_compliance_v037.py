"""v0.37 compliance assessment, OTLP, and replay tests."""

from __future__ import annotations

import json
from pathlib import Path

from capabledeputy.compliance.assessment import build_assessment_plan, emit_assessment_plan
from capabledeputy.compliance.otlp import audit_events_to_otlp_traces, emit_otlp_traces_json
from capabledeputy.compliance.replay import build_audit_replay_report, emit_audit_replay_report


def _events() -> list[dict]:
    return [
        {
            "audit_id": "a1",
            "timestamp": "2026-07-01T12:00:00+00:00",
            "event_type": "policy.decided",
            "session_id": "s1",
            "payload": {
                "rule": "untrusted-meets-egress",
                "decision": "deny",
                "tool": "email.send",
            },
        },
        {
            "audit_id": "a2",
            "timestamp": "2026-07-01T12:00:01+00:00",
            "event_type": "isolation_region.created",
            "session_id": "s1",
            "payload": {"region_id": "r1", "spec_id": "scratch"},
        },
        {
            "audit_id": "a3",
            "timestamp": "2026-07-01T12:00:02+00:00",
            "event_type": "isolation_region.discarded",
            "session_id": "s1",
            "payload": {"region_id": "r1", "spec_id": "scratch"},
        },
    ]


def test_assessment_plan_references_replay_and_evidence() -> None:
    plan = build_assessment_plan(
        system_security_plan_href="./ssp.json",
        evidence_bundle_href="./evidence.json",
        audit_replay_href="./replay.json",
        capdep_version="0.37.0",
    )
    root = plan["assessment-plan"]
    assert root["metadata"]["oscal-version"] == "1.1.2"
    assert root["import-ssp"]["href"] == "./ssp.json"
    methods = root["local-definitions"]["objectives-and-methods"]
    assert methods
    props = methods[0]["props"]
    assert any(p["value"] == "./evidence.json" for p in props)
    assert any(p["value"] == "./replay.json" for p in props)


def test_emit_assessment_plan_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "assessment.json"
    emit_assessment_plan(out, capdep_version="0.37.0")
    assert "assessment-plan" in json.loads(out.read_text(encoding="utf-8"))


def test_audit_replay_ok_when_rules_mapped_and_regions_discarded() -> None:
    report = build_audit_replay_report(_events())
    root = report["audit-replay"]
    assert root["ok"] is True
    assert root["summary"]["policy_decisions"] == 1
    assert root["summary"]["open_isolation_regions"] == 0
    assert "AC-4" in root["evidence_controls"]


def test_audit_replay_flags_unmapped_rule_and_open_region() -> None:
    events = _events()[:2]
    events[0]["payload"]["rule"] = "unknown-rule"
    report = build_audit_replay_report(events)
    root = report["audit-replay"]
    assert root["ok"] is False
    assert root["unmapped_rules"] == {"unknown-rule": 1}
    assert root["open_isolation_regions"] == ["r1"]


def test_emit_audit_replay_report_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "replay.json"
    emit_audit_replay_report(out, _events())
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["audit-replay"]["ok"] is True


def test_otlp_exporter_builds_resource_spans() -> None:
    payload = audit_events_to_otlp_traces(_events(), service_name="capdep-test")
    resource = payload["resourceSpans"][0]["resource"]
    assert {"key": "service.name", "value": {"stringValue": "capdep-test"}} in resource[
        "attributes"
    ]
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [span["name"] for span in spans] == [
        "policy.decided",
        "isolation_region.created",
        "isolation_region.discarded",
    ]
    attrs = spans[0]["attributes"]
    assert {"key": "capdep.rule", "value": {"stringValue": "untrusted-meets-egress"}} in attrs


def test_emit_otlp_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "otlp.json"
    emit_otlp_traces_json(out, _events())
    assert "resourceSpans" in json.loads(out.read_text(encoding="utf-8"))
