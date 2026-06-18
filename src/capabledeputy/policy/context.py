"""Runtime policy wiring shared by the daemon, app, and tool chokepoint."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from capabledeputy.patterns.reference_handle import ReferenceHandleStore
from capabledeputy.policy.assurance import ResidualRiskThresholds
from capabledeputy.policy.bindings import BindingSet
from capabledeputy.policy.decision_rules import DecisionRules
from capabledeputy.policy.envelope import EnvelopeSet, RiskPreference
from capabledeputy.policy.overrides import OverrideGrantStore, OverridePolicies
from capabledeputy.policy.tiers import Tier


@dataclass(frozen=True)
class PolicyContext:
    """Operator-curated runtime context for policy decisions.

    The dispatcher receives this once at startup and threads the configured
    policy registries, hooks, stores, and substrate providers into each tool
    call. All fields are optional so tests and scoped deployments can activate
    one primitive at a time without constructing the full daemon graph.
    """

    rules_v2: DecisionRules | None = None
    bindings: BindingSet | None = None
    override_policies: OverridePolicies | None = None
    override_grants: OverrideGrantStore | None = None
    handle_store: ReferenceHandleStore | None = None
    envelope_set: EnvelopeSet | None = None
    risk_preference: RiskPreference | None = None
    clearance_max_tier: Tier | None = None
    integrity_floor_level: str | None = None
    residual_risk_thresholds: ResidualRiskThresholds | None = None
    risk_register: Any = None
    sandbox_actuator: Any = None
    devbox_manager: Any = None
    inspectors: tuple[Any, ...] = ()
    decision_inspectors: tuple[Any, ...] = ()
    egress_override_categories: frozenset[str] = field(default_factory=frozenset)
    egress_override_tiers: frozenset[str] = field(default_factory=frozenset)
    declassifiers: tuple[Any, ...] = ()
    profiles: dict[str, Any] = field(default_factory=dict)
    purposes: Any = None
    relationship_groups: Any = None
    relationship_groups_path: Any = None

    @property
    def sandbox_actuator_wired(self) -> bool:
        """True iff an EXECUTE.sandbox substrate provider is configured."""
        return self.sandbox_actuator is not None

    @property
    def devbox_manager_wired(self) -> bool:
        """True iff a long-lived devbox manager is configured."""
        return self.devbox_manager is not None
