"""Compliance emission — OSCAL artifacts from CapableDeputy state.

NIST OSCAL (Open Security Controls Assessment Language) is a
standardized JSON/YAML/XML format for representing security controls,
component capabilities, and system security plans.

CapableDeputy's policy + audit state maps cleanly to OSCAL's
component-definition and SSP shapes:

  - Each chokepoint rule (Brewer-Nash conflict, BLP clearance, etc.)
    maps to an OSCAL "control implementation" referencing the NIST
    800-53 (or other framework) catalog entry it satisfies.
  - The audit log provides "implementation evidence" — concrete
    runtime decisions auditors can trace back to specific controls.
  - Sessions + capabilities map to OSCAL "system-component" entries.

Operators run `capdep compliance emit-oscal` to produce these
artifacts on demand. Compliance teams consume the standard JSON
without re-mapping CapableDeputy's internal vocabulary.
"""

from capabledeputy.compliance.oscal import (
    build_component_definition,
    build_control_mapping,
    emit_oscal_bundle,
)
from capabledeputy.compliance.ssp import (
    build_evidence_bundle,
    build_system_security_plan,
    emit_evidence_bundle,
    emit_system_security_plan,
)

__all__ = [
    "build_component_definition",
    "build_control_mapping",
    "build_evidence_bundle",
    "build_system_security_plan",
    "emit_evidence_bundle",
    "emit_oscal_bundle",
    "emit_system_security_plan",
]
