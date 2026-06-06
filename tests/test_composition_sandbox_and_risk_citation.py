"""Composition leaves — sandbox-without-actuator + risk-citation runtime gates.

#6 EXECUTE.sandbox without an actuator port (FR-042 / SC-017): the
   engine refuses with OVERRIDE_REQUIRED so the operator either
   wires a provider (spec 004) or routes through Pattern (3).
#8 Orphan risk-id citation (FR-015 runtime side): any axis_a
   category that cites a risk id not in the register refuses the
   decision with ORPHAN_RISK_CITATION_RULE.
"""

from __future__ import annotations

from datetime import UTC, datetime

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.decision_rules import DecisionRules
from capabledeputy.policy.engine import (
    ORPHAN_RISK_CITATION_RULE,
    SANDBOX_NO_ACTUATOR_RULE,
    decide,
)
from capabledeputy.policy.labels import (
    AxisA,
    AxisB,
    AxisD,
    CategoryTag,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.risk_register import (
    RiskRegister,
    RiskRegisterEntry,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _wide_cap() -> Capability:
    return Capability(
        kind=CapabilityKind.WRITE_FS,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
        allows_destructive=True,
    )


def _axes(category: str = "data", risk_ids: tuple[str, ...] = ()) -> tuple[AxisA, AxisB, AxisD]:
    axis_a = AxisA(
        categories=(CategoryTag(category=category, tier=Tier.SENSITIVE, risk_ids=risk_ids),),
    )
    axis_b = AxisB(entries=(ProvenanceTag(level=ProvenanceLevel.PRINCIPAL_DIRECT),))
    axis_d = AxisD(initiator="principal:alice")
    return axis_a, axis_b, axis_d


# --- Sandbox-without-actuator (#6) ----------------------------------


def test_execute_sandbox_without_actuator_returns_override_required() -> None:
    """The spec is explicit: EXECUTE.sandbox with no actuator port ⇒
    OVERRIDE_REQUIRED. Caller's path: wire a sandbox provider in
    spec 004 OR route through Pattern (3) handles."""
    axis_a, axis_b, axis_d = _axes()
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="EXECUTE.sandbox",
        rules_v2=DecisionRules(rules=()),
        sandbox_actuator_wired=False,
        now=_NOW,
    )
    assert result.decision == Decision.OVERRIDE_REQUIRED
    assert result.rule == SANDBOX_NO_ACTUATOR_RULE


def test_execute_sandbox_with_actuator_runs_normal_path() -> None:
    """When the operator wires a sandbox provider, EXECUTE.sandbox
    falls through to the normal v2 pipeline (here REQUIRE_APPROVAL
    on the never-auto default)."""
    axis_a, axis_b, axis_d = _axes()
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="EXECUTE.sandbox",
        rules_v2=DecisionRules(rules=()),
        sandbox_actuator_wired=True,
        now=_NOW,
    )
    assert result.rule != SANDBOX_NO_ACTUATOR_RULE


def test_non_sandbox_effect_unaffected() -> None:
    """The check only fires for EXECUTE.sandbox effect_class."""
    axis_a, axis_b, axis_d = _axes()
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write",
        rules_v2=DecisionRules(rules=()),
        sandbox_actuator_wired=False,
        now=_NOW,
    )
    assert result.rule != SANDBOX_NO_ACTUATOR_RULE


# --- Devbox-without-manager — parallel gate for EXECUTE.devbox -----


def test_execute_devbox_without_manager_returns_override_required() -> None:
    """Mirror of the sandbox gate for the persistent-container effect
    class. Defense-in-depth: even if a devbox-shaped tool slips through
    registration (custom kind, operator misconfig), the engine refuses
    when no manager is wired."""
    from capabledeputy.policy.engine import DEVBOX_NO_MANAGER_RULE

    axis_a, axis_b, axis_d = _axes()
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="py-dev"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="EXECUTE.devbox",
        rules_v2=DecisionRules(rules=()),
        sandbox_actuator_wired=False,
        devbox_manager_wired=False,
        now=_NOW,
    )
    assert result.decision == Decision.OVERRIDE_REQUIRED
    assert result.rule == DEVBOX_NO_MANAGER_RULE


def test_execute_devbox_with_manager_runs_normal_path() -> None:
    """When the manager is wired, EXECUTE.devbox falls through to the
    normal v2 pipeline (here REQUIRE_APPROVAL on the never-auto
    default)."""
    from capabledeputy.policy.engine import DEVBOX_NO_MANAGER_RULE

    axis_a, axis_b, axis_d = _axes()
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="py-dev"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="EXECUTE.devbox",
        rules_v2=DecisionRules(rules=()),
        sandbox_actuator_wired=False,
        devbox_manager_wired=True,
        now=_NOW,
    )
    assert result.rule != DEVBOX_NO_MANAGER_RULE


def test_devbox_gate_does_not_fire_for_sandbox_effect() -> None:
    """The devbox gate keys on `execute.devbox` prefix; an
    `execute.sandbox` action with the sandbox actuator unwired hits
    the sandbox rule, not the devbox rule."""
    from capabledeputy.policy.engine import DEVBOX_NO_MANAGER_RULE

    axis_a, axis_b, axis_d = _axes()
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="EXECUTE.sandbox",
        rules_v2=DecisionRules(rules=()),
        sandbox_actuator_wired=False,
        devbox_manager_wired=False,
        now=_NOW,
    )
    assert result.rule == SANDBOX_NO_ACTUATOR_RULE
    assert result.rule != DEVBOX_NO_MANAGER_RULE


# --- Orphan risk-id citation (#8) -----------------------------------


def _register() -> RiskRegister:
    return RiskRegister(
        entries={
            "RISK-PII-001": RiskRegisterEntry(
                id="RISK-PII-001",
                summary="PII disclosure",
                framework_refs=("NIST-PR.DS-5",),
            ),
        },
    )


def test_orphan_risk_citation_refuses() -> None:
    """An axis_a category citing a risk-id not in the register refuses
    the decision at runtime, even with valid capabilities (FR-015)."""
    axis_a, axis_b, axis_d = _axes(risk_ids=("RISK-DOES-NOT-EXIST",))
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write",
        rules_v2=DecisionRules(rules=()),
        risk_register=_register(),
        now=_NOW,
    )
    assert result.decision == Decision.DENY
    assert result.rule == ORPHAN_RISK_CITATION_RULE


def test_known_risk_id_passes() -> None:
    """A cited id that exists in the register doesn't trigger refusal."""
    axis_a, axis_b, axis_d = _axes(risk_ids=("RISK-PII-001",))
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write",
        rules_v2=DecisionRules(rules=()),
        risk_register=_register(),
        now=_NOW,
    )
    assert result.rule != ORPHAN_RISK_CITATION_RULE


def test_no_risk_register_skips_check() -> None:
    """Without a register wired, no orphan check fires — back-compat."""
    axis_a, axis_b, axis_d = _axes(risk_ids=("RISK-DOES-NOT-EXIST",))
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write",
        rules_v2=DecisionRules(rules=()),
        # risk_register intentionally omitted
        now=_NOW,
    )
    assert result.rule != ORPHAN_RISK_CITATION_RULE


def test_empty_risk_ids_does_not_refuse() -> None:
    """Categories without any risk_ids pass the orphan check (they're
    a separate SC-001 lint problem caught at CI, not runtime)."""
    axis_a, axis_b, axis_d = _axes(risk_ids=())
    result = decide(
        frozenset(),
        frozenset({_wide_cap()}),
        Action(kind=CapabilityKind.WRITE_FS, target="/x"),
        axis_a=axis_a,
        axis_b=axis_b,
        axis_d=axis_d,
        effect_class="data.write",
        rules_v2=DecisionRules(rules=()),
        risk_register=_register(),
        now=_NOW,
    )
    assert result.rule != ORPHAN_RISK_CITATION_RULE
