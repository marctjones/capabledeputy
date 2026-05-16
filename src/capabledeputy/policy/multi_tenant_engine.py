"""Multi-tenant policy decisions (v0.4 additive).

Wraps `policy.engine.decide` with per-tenant scoping. The wrapper
takes a frozenset[TenantLabel] (instead of a flat Label set), runs the
existing conflict-rule engine PER TENANT, and combines the per-tenant
decisions:

  - If any per-tenant decision is DENY, the overall decision is DENY.
  - Else if any is REQUIRE_APPROVAL, the overall is REQUIRE_APPROVAL.
  - Else ALLOW.

A `target_tenant` parameter scopes the action to one tenant; if you
ask "send email *as bob*", only Bob's labels can authorize the egress.
"""

from __future__ import annotations

from dataclasses import dataclass

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.engine import PolicyDecision, decide
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tenancy import (
    Tenant,
    TenantLabel,
    labels_for_tenant,
    tenants_in,
)


@dataclass(frozen=True)
class MultiTenantDecision:
    decision: Decision
    rule: str | None
    reason: str
    per_tenant: dict[Tenant, PolicyDecision]


def decide_multi_tenant(
    tenant_label_set: frozenset[TenantLabel],
    capabilities: frozenset[Capability],
    action: Action,
    *,
    target_tenant: Tenant | None = None,
    used_kinds: frozenset[CapabilityKind] = frozenset(),
) -> MultiTenantDecision:
    """Run the policy engine per tenant and combine the results.

    `target_tenant` constrains the action to a single tenant; if set,
    only labels under that tenant's compartment contribute. If None,
    every tenant's labels are checked independently.
    """
    tenants = (
        {target_tenant}
        if target_tenant is not None
        else (tenants_in(tenant_label_set) or {Tenant.default()})
    )

    per_tenant: dict[Tenant, PolicyDecision] = {}
    for tenant in tenants:
        scoped_labels = labels_for_tenant(tenant_label_set, tenant)
        per_tenant[tenant] = decide(
            scoped_labels, capabilities, action, used_kinds=used_kinds,
        )

    if any(d.decision == Decision.DENY for d in per_tenant.values()):
        denying = next(
            (t, d) for t, d in per_tenant.items() if d.decision == Decision.DENY
        )
        return MultiTenantDecision(
            decision=Decision.DENY,
            rule=denying[1].rule,
            reason=(
                f"tenant {denying[0].id}: {denying[1].reason}"
                if denying[1].reason
                else f"tenant {denying[0].id} denies"
            ),
            per_tenant=per_tenant,
        )
    if any(d.decision == Decision.REQUIRE_APPROVAL for d in per_tenant.values()):
        gating = next(
            (t, d) for t, d in per_tenant.items() if d.decision == Decision.REQUIRE_APPROVAL
        )
        return MultiTenantDecision(
            decision=Decision.REQUIRE_APPROVAL,
            rule=gating[1].rule,
            reason=f"tenant {gating[0].id} requires approval: {gating[1].reason}",
            per_tenant=per_tenant,
        )
    return MultiTenantDecision(
        decision=Decision.ALLOW,
        rule=None,
        reason="all tenants allow",
        per_tenant=per_tenant,
    )
