"""Typed policy pipeline seam for tool-call decisions.

The legacy engine is still the authoritative evaluator, but callers should
build a DecisionRequest and pass it through a PolicyPipeline. That gives the
runtime a stable architecture boundary while the monolithic engine is split
into smaller stages over time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from capabledeputy.policy.actions import Action
from capabledeputy.policy.bindings import BindingSet
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.decision_rules import DecisionRules, RelaxInput, RuleOutcome
from capabledeputy.policy.engine import PolicyDecision, decide
from capabledeputy.policy.envelope import EnvelopeSet, RiskPreference
from capabledeputy.policy.labels import AxisD, LabelState
from capabledeputy.policy.overrides import OverrideGrantStore
from capabledeputy.policy.reversibility import ReversibilityLabel


@dataclass(frozen=True)
class DecisionRequest:
    """Complete policy-decision input for one attempted action.

    This intentionally mirrors the legacy ``decide`` surface today. The value
    of the type is architectural: dispatcher code can construct, inspect, and
    test one request object while policy internals evolve behind the pipeline.
    """

    capabilities: frozenset[Capability]
    action: Action
    used_kinds: frozenset[CapabilityKind] = frozenset()
    now: datetime | None = None
    cap_uses: dict[str, tuple[datetime, ...]] | None = None
    axis_d: AxisD | None = None
    effect_class: str | None = None
    rules_v2: DecisionRules | None = None
    default_v2_outcome: RuleOutcome = RuleOutcome.SUGGEST
    relax_inputs: tuple[RelaxInput, ...] = ()
    override_grants: OverrideGrantStore | None = None
    session_id: Any = None
    bindings: BindingSet | None = None
    effective_reversibility: ReversibilityLabel | None = None
    envelope_set: EnvelopeSet | None = None
    risk_preference: RiskPreference | None = None
    clearance_max_tier: Any = None
    integrity_floor_level: str | None = None
    risk_register: Any = None
    sandbox_actuator_wired: bool = False
    devbox_manager_wired: bool = False
    revoked_audit_ids: frozenset[UUID] = frozenset()
    first_use_prompt_enabled: bool = False
    rate_limit_escalation: bool = False
    labels: LabelState | None = None
    egress_override_categories: frozenset[str] = frozenset()
    egress_override_tiers: frozenset[str] = frozenset()
    trust_profile_is_personal: bool = False


@dataclass(frozen=True)
class DecisionFrame:
    """A decision plus the pipeline stages that produced it."""

    request: DecisionRequest
    decision: PolicyDecision
    stages: tuple[str, ...] = field(default_factory=tuple)


class PolicyPipeline(Protocol):
    """Decision pipeline interface used by the policy chokepoint."""

    def decide(self, request: DecisionRequest) -> DecisionFrame:
        """Evaluate a request and return a traced decision frame."""
        ...


@dataclass(frozen=True)
class LegacyEnginePolicyPipeline:
    """Compatibility pipeline backed by the existing monolithic engine."""

    stage_name: str = "legacy-engine"

    def decide(self, request: DecisionRequest) -> DecisionFrame:
        decision = decide(
            request.capabilities,
            request.action,
            used_kinds=request.used_kinds,
            now=request.now,
            cap_uses=request.cap_uses,
            axis_d=request.axis_d,
            effect_class=request.effect_class,
            rules_v2=request.rules_v2,
            default_v2_outcome=request.default_v2_outcome,
            relax_inputs=request.relax_inputs,
            override_grants=request.override_grants,
            session_id=request.session_id,
            bindings=request.bindings,
            effective_reversibility=request.effective_reversibility,
            envelope_set=request.envelope_set,
            risk_preference=request.risk_preference,
            clearance_max_tier=request.clearance_max_tier,
            integrity_floor_level=request.integrity_floor_level,
            risk_register=request.risk_register,
            sandbox_actuator_wired=request.sandbox_actuator_wired,
            devbox_manager_wired=request.devbox_manager_wired,
            revoked_audit_ids=request.revoked_audit_ids,
            first_use_prompt_enabled=request.first_use_prompt_enabled,
            rate_limit_escalation=request.rate_limit_escalation,
            labels=request.labels,
            egress_override_categories=request.egress_override_categories,
            egress_override_tiers=request.egress_override_tiers,
            trust_profile_is_personal=request.trust_profile_is_personal,
        )
        return DecisionFrame(
            request=request,
            decision=decision,
            stages=(self.stage_name,),
        )


DefaultPolicyPipeline = LegacyEnginePolicyPipeline
