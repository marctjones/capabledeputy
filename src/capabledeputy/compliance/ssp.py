"""OSCAL System Security Plan emission + audit-evidence bundle.

A System Security Plan (SSP) is the OSCAL artifact that ties:
  - One or more Component Definitions (CapableDeputy itself)
  - Operator-specific system characteristics (deployment context)
  - Implemented controls (NIST 800-53 references)
  - Implementation evidence (audit log queries)

Together with the Component Definition (oscal.py), this gives
compliance teams a complete view of how CapableDeputy implements
NIST controls at the operator's specific installation.

Operators run `capdep compliance emit-ssp --output ./ssp.json` to
produce the SSP. The audit-evidence bundle (audit log filtered to
control-implementation events) is a separate `emit-evidence`
artifact tagged with each control id.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from capabledeputy.compliance.oscal import build_control_mapping


def build_system_security_plan(
    *,
    system_name: str = "CapableDeputy Personal Agent",
    organization: str = "operator",
    component_definition_uuid: str | None = None,
    capdep_version: str = "unknown",
    custom_mapping: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build an OSCAL System Security Plan v1.1.2.

    Returns a dict in OSCAL SSP shape pointing at the Component
    Definition that lists CapableDeputy's control implementations.
    """
    if component_definition_uuid is None:
        component_definition_uuid = str(uuid.uuid4())

    ssp_uuid = str(uuid.uuid4())
    impls = build_control_mapping(custom_mapping)

    # Collect all unique NIST control ids implemented
    all_controls: set[str] = set()
    for impl in impls:
        all_controls.update(impl.nist_controls)

    return {
        "system-security-plan": {
            "uuid": ssp_uuid,
            "metadata": {
                "title": f"{system_name} — System Security Plan",
                "last-modified": datetime.now(UTC).isoformat(),
                "version": capdep_version,
                "oscal-version": "1.1.2",
                "parties": [
                    {
                        "uuid": str(uuid.uuid4()),
                        "type": "organization",
                        "name": organization,
                    },
                ],
            },
            "import-profile": {
                "href": (
                    "https://raw.githubusercontent.com/usnistgov/oscal-content/"
                    "main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_LOW-baseline_profile.json"
                ),
            },
            "system-characteristics": {
                "system-name": system_name,
                "description": (
                    "Personal AI agent runtime mediating tool calls "
                    "through a deterministic chokepoint with capability- "
                    "and label-based information flow control."
                ),
                "security-sensitivity-level": "moderate",
                "system-information": {
                    "information-types": [
                        {
                            "uuid": str(uuid.uuid4()),
                            "title": "Personal data",
                            "description": "User-private data the agent processes.",
                            "confidentiality-impact": {"base": "fips-199-moderate"},
                            "integrity-impact": {"base": "fips-199-moderate"},
                            "availability-impact": {"base": "fips-199-low"},
                        },
                    ],
                },
                "security-impact-level": {
                    "security-objective-confidentiality": "fips-199-moderate",
                    "security-objective-integrity": "fips-199-moderate",
                    "security-objective-availability": "fips-199-low",
                },
                "status": {"state": "operational"},
            },
            "system-implementation": {
                "users": [
                    {
                        "uuid": str(uuid.uuid4()),
                        "title": "Operator",
                        "description": (
                            "Single-user operator of the CapableDeputy "
                            "installation; configures policy + reviews "
                            "approvals."
                        ),
                        "role-ids": ["operator"],
                    },
                ],
                "components": [
                    {
                        "uuid": component_definition_uuid,
                        "type": "software",
                        "title": "CapableDeputy",
                        "description": (
                            "See Component Definition for control implementation details."
                        ),
                        "status": {"state": "operational"},
                    },
                ],
            },
            "control-implementation": {
                "description": (
                    "CapableDeputy chokepoint rules implement NIST 800-53 "
                    "controls deterministically. See the Component "
                    "Definition for the rule-to-control mapping."
                ),
                "implemented-requirements": [
                    {
                        "uuid": str(uuid.uuid4()),
                        "control-id": control_id,
                        "statements": [
                            {
                                "statement-id": f"{control_id}_smt",
                                "uuid": str(uuid.uuid4()),
                                "description": (
                                    "Implemented by CapableDeputy chokepoint rules. "
                                    "See Component Definition for the specific rules."
                                ),
                            },
                        ],
                    }
                    for control_id in sorted(all_controls)
                ],
            },
        },
    }


def emit_system_security_plan(
    output_path: Path,
    *,
    system_name: str = "CapableDeputy Personal Agent",
    organization: str = "operator",
    capdep_version: str = "unknown",
    custom_mapping: dict[str, list[str]] | None = None,
) -> None:
    """Write the SSP to ``output_path`` as JSON."""
    ssp = build_system_security_plan(
        system_name=system_name,
        organization=organization,
        capdep_version=capdep_version,
        custom_mapping=custom_mapping,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(ssp, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_evidence_bundle(
    audit_events: list[dict[str, Any]],
    *,
    custom_mapping: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Filter the audit log to events that are evidence for control
    implementations, grouped by NIST control id.

    Each policy.decided event with a known rule contributes evidence
    for the NIST controls that rule implements. Auditors see for each
    control:
      - The chokepoint rule that satisfies it
      - Concrete runtime decisions (timestamps, sessions, rule firings)

    Returns:
        {
          "AC-3": [
            {"event_type": "policy.decided", "timestamp": ..., "rule": ...},
            ...
          ],
          "AC-4": [...],
          ...
        }
    """
    impls = build_control_mapping(custom_mapping)
    rule_to_controls: dict[str, tuple[str, ...]] = {
        impl.capdep_rule: impl.nist_controls for impl in impls
    }

    evidence: dict[str, list[dict[str, Any]]] = {}
    for event in audit_events:
        if event.get("event_type") != "policy.decided":
            continue
        payload = event.get("payload", {})
        rule = payload.get("rule")
        if not rule or rule not in rule_to_controls:
            continue
        for control_id in rule_to_controls[rule]:
            evidence.setdefault(control_id, []).append(
                {
                    "event_type": event.get("event_type"),
                    "timestamp": event.get("timestamp"),
                    "session_id": event.get("session_id"),
                    "rule": rule,
                    "decision": payload.get("decision"),
                    "reason": payload.get("reason"),
                    "tool": payload.get("tool"),
                },
            )
    return evidence


def emit_evidence_bundle(
    output_path: Path,
    audit_events: list[dict[str, Any]],
    *,
    custom_mapping: dict[str, list[str]] | None = None,
) -> None:
    """Write the evidence bundle to ``output_path`` as JSON."""
    bundle = build_evidence_bundle(
        audit_events,
        custom_mapping=custom_mapping,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "evidence-bundle": {
                    "generated-at": datetime.now(UTC).isoformat(),
                    "controls": bundle,
                    "summary": {
                        "total_events": sum(len(v) for v in bundle.values()),
                        "controls_with_evidence": len(bundle),
                    },
                },
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
