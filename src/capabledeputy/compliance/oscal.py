"""OSCAL emission — build NIST OSCAL component-definition + control-mapping
artifacts from CapableDeputy's policy + audit state.

Focused subset of OSCAL: Component Definition (one Component per
CapableDeputy daemon installation) + Control Implementation
(per-chokepoint-rule mapping to NIST 800-53). Statement evidence
is sourced from the audit log on demand.

Operator usage:
  capdep compliance emit-oscal --output ./oscal-bundle.json
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Static mapping: CapableDeputy chokepoint rules → NIST 800-53 controls
# Operators with different frameworks (CIS, ISO 27001) can extend by
# providing additional mappings in their config.
CAPDEP_TO_NIST_800_53: dict[str, list[str]] = {
    # Brewer-Nash: prevents information flow between conflict sets
    "untrusted-meets-egress": ["AC-4", "SC-7"],
    "health-meets-egress": ["AC-4", "AC-16", "SI-12"],
    "financial-meets-email": ["AC-4", "AC-16"],
    "financial-meets-purchase": ["AC-4", "AC-16"],
    # Bell-LaPadula: clearance-based read-up refusal
    "clearance-exceeded": ["AC-3", "AC-6"],
    # Biba: integrity-floor refusal
    "integrity-floor-violated": ["SI-7", "AC-3"],
    # Capability framework: object-capability enforcement
    "no-matching-capability": ["AC-3", "AC-6"],
    "capability-expired": ["AC-3", "AC-6", "AC-2(2)"],
    "rate-limit-exceeded": ["AC-3", "AC-7"],
    "capability-revoked-by-prior-use": ["AC-3"],
    "capability-cascaded": ["AC-3", "AC-2(2)"],
    # Approval flow: dual-control + override discipline
    "social-commitment-irreversible": ["AC-3", "CM-3", "SI-10"],
    "reversibility-irreversible": ["AC-3", "CM-3"],
    "override-grant-active": ["AC-3", "AU-2", "AC-2(3)"],
    # FR-031 relax refusal — asymmetric composition
    "RELAX_REFUSED": ["AC-3", "AU-2"],
}


@dataclass(frozen=True)
class ControlImplementation:
    """One OSCAL control-implementation entry.

    Maps a CapableDeputy enforcement to a NIST 800-53 control via
    the chokepoint rule that implements it.
    """

    capdep_rule: str
    nist_controls: tuple[str, ...]
    description: str
    statement_evidence_query: str = ""

    def to_oscal_dict(self) -> dict[str, Any]:
        return {
            "uuid": str(uuid.uuid4()),
            "implementation-of": [{"control-id": cid} for cid in self.nist_controls],
            "description": self.description,
            "props": [
                {
                    "name": "capdep-rule",
                    "value": self.capdep_rule,
                    "ns": "https://capabledeputy.io/ns/oscal/v1",
                },
            ],
            "statement-evidence-query": self.statement_evidence_query,
        }


def build_control_mapping(
    custom_mapping: dict[str, list[str]] | None = None,
) -> tuple[ControlImplementation, ...]:
    """Build the full set of ControlImplementation entries from the
    standard CapableDeputy → NIST mapping, plus any operator overrides.

    Operator's custom_mapping is merged onto the default — operators
    add their own rules + their target controls (CIS, ISO 27001, etc.).
    """
    mapping = dict(CAPDEP_TO_NIST_800_53)
    if custom_mapping:
        mapping.update({k: list(v) for k, v in custom_mapping.items()})

    descriptions: dict[str, str] = {
        "untrusted-meets-egress": (
            "Brewer-Nash: a session tainted with untrusted-external content "
            "cannot perform egress-class actions (email send, web fetch to "
            "external) until the taint is structurally cleared. Implements "
            "AC-4 (information flow enforcement) and SC-7 (boundary protection)."
        ),
        "health-meets-egress": (
            "Brewer-Nash: a session with confidential.health data cannot egress "
            "to non-health-classified destinations. Implements AC-4, AC-16, SI-12."
        ),
        "clearance-exceeded": (
            "Bell-LaPadula: an action whose target tier exceeds the session's "
            "clearance ceiling is refused. Implements AC-3, AC-6."
        ),
        "capability-expired": (
            "Time-bounded capabilities: an action requested through an expired "
            "capability is refused. Implements AC-3, AC-6, AC-2(2)."
        ),
        "capability-cascaded": (
            "Delegation cascade: a child capability whose parent has been "
            "revoked/expired/exhausted is treated as inert at the next decision. "
            "Implements AC-3, AC-2(2)."
        ),
        "rate-limit-exceeded": (
            "Sliding-window rate limit on capability use; protects from "
            "automation-driven enumeration. Implements AC-3, AC-7."
        ),
        "social-commitment-irreversible": (
            "Irreversible action with social commitment (email send, purchase) "
            "requires explicit approval. Implements AC-3, CM-3, SI-10."
        ),
        "override-grant-active": (
            "Override grants are dual-control attested and audited. "
            "Implements AC-3, AC-2(3) (separation of duties), AU-2 (audit)."
        ),
    }

    out: list[ControlImplementation] = []
    for rule, controls in mapping.items():
        out.append(
            ControlImplementation(
                capdep_rule=rule,
                nist_controls=tuple(controls),
                description=descriptions.get(
                    rule,
                    f"CapableDeputy chokepoint rule '{rule}' implements {', '.join(controls)}.",
                ),
                statement_evidence_query=(f'event_type == "policy.decided" AND rule == "{rule}"'),
            ),
        )
    return tuple(out)


def build_component_definition(
    *,
    component_name: str = "CapableDeputy",
    component_description: str | None = None,
    capdep_version: str = "unknown",
    custom_mapping: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build a complete OSCAL Component Definition for the running
    CapableDeputy installation.

    Returns a dict in OSCAL Component Definition v1.1.2 shape.
    """
    component_uuid = str(uuid.uuid4())
    if component_description is None:
        component_description = (
            "CapableDeputy: capability-based AI agent runtime with "
            "deterministic chokepoint policy, four-axis labeling, "
            "and audit-by-construction. Enforces information-flow "
            "control on every tool dispatch."
        )

    implementations = build_control_mapping(custom_mapping)
    implementation_uuid = str(uuid.uuid4())

    return {
        "component-definition": {
            "uuid": str(uuid.uuid4()),
            "metadata": {
                "title": "CapableDeputy Component Definition",
                "last-modified": datetime.now(UTC).isoformat(),
                "version": capdep_version,
                "oscal-version": "1.1.2",
            },
            "components": [
                {
                    "uuid": component_uuid,
                    "type": "software",
                    "title": component_name,
                    "description": component_description,
                    "props": [
                        {
                            "name": "version",
                            "value": capdep_version,
                            "ns": "https://capabledeputy.io/ns/oscal/v1",
                        },
                    ],
                    "control-implementations": [
                        {
                            "uuid": implementation_uuid,
                            "source": "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json",
                            "description": (
                                "Mapping of CapableDeputy chokepoint rules to "
                                "NIST 800-53 rev5 controls. Each implementation "
                                "below is implemented by a deterministic rule "
                                "in src/capabledeputy/policy/engine.py."
                            ),
                            "implemented-requirements": [
                                impl.to_oscal_dict() for impl in implementations
                            ],
                        },
                    ],
                },
            ],
        },
    }


def emit_oscal_bundle(
    output_path: Path,
    *,
    component_name: str = "CapableDeputy",
    capdep_version: str = "unknown",
    custom_mapping: dict[str, list[str]] | None = None,
) -> None:
    """Write the OSCAL Component Definition to ``output_path`` as JSON."""
    bundle = build_component_definition(
        component_name=component_name,
        capdep_version=capdep_version,
        custom_mapping=custom_mapping,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True),
        encoding="utf-8",
    )
