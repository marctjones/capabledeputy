"""Compliance audit replay pipeline."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from capabledeputy.compliance.oscal import build_control_mapping
from capabledeputy.compliance.ssp import build_evidence_bundle


def load_audit_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"event_type": "audit.invalid_json", "payload": {"line": line[:200]}})
    return events


def build_audit_replay_report(
    audit_events: list[dict[str, Any]],
    *,
    custom_mapping: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    mapping = {
        impl.capdep_rule: impl.nist_controls for impl in build_control_mapping(custom_mapping)
    }
    event_counts = Counter(str(e.get("event_type") or "") for e in audit_events)
    policy_decisions: list[dict[str, Any]] = []
    unmapped_rules: Counter[str] = Counter()
    created_regions: dict[str, dict[str, Any]] = {}
    discarded_regions: set[str] = set()

    for event in audit_events:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == "policy.decided":
            rule = str(payload.get("rule") or "")
            policy_decisions.append(event)
            if rule and rule not in mapping:
                unmapped_rules[rule] += 1
        elif event_type == "isolation_region.created":
            region_id = str(payload.get("region_id") or "")
            if region_id:
                created_regions[region_id] = event
        elif event_type == "isolation_region.discarded":
            region_id = str(payload.get("region_id") or "")
            if region_id:
                discarded_regions.add(region_id)

    open_regions = sorted(set(created_regions) - discarded_regions)
    evidence = build_evidence_bundle(audit_events, custom_mapping=custom_mapping)
    return {
        "audit-replay": {
            "generated-at": datetime.now(UTC).isoformat(),
            "ok": not unmapped_rules and not open_regions,
            "summary": {
                "total_events": len(audit_events),
                "event_counts": dict(sorted(event_counts.items())),
                "policy_decisions": len(policy_decisions),
                "controls_with_evidence": len(evidence),
                "open_isolation_regions": len(open_regions),
                "unmapped_policy_rules": sum(unmapped_rules.values()),
            },
            "unmapped_rules": dict(sorted(unmapped_rules.items())),
            "open_isolation_regions": open_regions,
            "evidence_controls": sorted(evidence),
        },
    }


def emit_audit_replay_report(
    output_path: Path,
    audit_events: list[dict[str, Any]],
    *,
    custom_mapping: dict[str, list[str]] | None = None,
) -> None:
    report = build_audit_replay_report(audit_events, custom_mapping=custom_mapping)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
