"""Multi-tenant labels: per-tenant policy scoping.

Two tenants reading their own confidential data must not be able to
combine into a single egress without each tenant's compartment
approving. The point of per-tenant labels is that Alice's
confidential.health does not fire health-meets-egress when Bob is the
one egressing — because Bob carries no health labels.
"""

from __future__ import annotations

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
)
from capabledeputy.policy.labels import Label
from capabledeputy.policy.multi_tenant_engine import decide_multi_tenant
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tenancy import (
    DEFAULT_TENANT,
    Tenant,
    TenantLabel,
    labels_for_tenant,
    lift,
    project,
    tenants_in,
)


def test_tenant_default_is_stable() -> None:
    a = Tenant.default()
    b = Tenant.default()
    assert a == b
    assert a.id == DEFAULT_TENANT


def test_tenant_label_str_repr() -> None:
    alice = Tenant(id="alice", display_name="Alice")
    tl = TenantLabel(label=Label.CONFIDENTIAL_HEALTH, tenant=alice)
    assert str(tl) == "confidential.health@alice"


def test_default_tenant_str_omits_at_suffix() -> None:
    tl = TenantLabel(label=Label.CONFIDENTIAL_HEALTH)
    assert str(tl) == "confidential.health"


def test_lift_round_trips_under_default_tenant() -> None:
    flat = frozenset({Label.CONFIDENTIAL_HEALTH, Label.EGRESS_EMAIL})
    lifted = lift(flat)
    assert project(lifted) == flat


def test_labels_for_tenant_filters() -> None:
    alice = Tenant(id="alice")
    bob = Tenant(id="bob")
    tls = frozenset(
        {
            TenantLabel(Label.CONFIDENTIAL_HEALTH, alice),
            TenantLabel(Label.CONFIDENTIAL_HEALTH, bob),
            TenantLabel(Label.EGRESS_EMAIL, alice),
        },
    )
    assert labels_for_tenant(tls, alice) == frozenset(
        {Label.CONFIDENTIAL_HEALTH, Label.EGRESS_EMAIL},
    )
    assert labels_for_tenant(tls, bob) == frozenset({Label.CONFIDENTIAL_HEALTH})
    assert tenants_in(tls) == {alice, bob}


def test_per_tenant_health_does_not_fire_other_tenant_egress() -> None:
    """The headline correctness property:

    Alice has confidential.health. Bob attempts an email egress.
    Bob's tenant carries no health labels, so health-meets-egress
    does not fire — the action is allowed against Bob's compartment.
    """
    alice = Tenant(id="alice")
    bob = Tenant(id="bob")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="someone@example.com")
    tls = frozenset({TenantLabel(Label.CONFIDENTIAL_HEALTH, alice)})

    decision = decide_multi_tenant(
        tls,
        frozenset({cap}),
        action,
        target_tenant=bob,
    )
    assert decision.decision == Decision.ALLOW


def test_per_tenant_health_blocks_same_tenant_egress() -> None:
    """The dual: Alice has confidential.health, Alice attempts an email
    egress under her own tenant — health-meets-egress fires."""
    alice = Tenant(id="alice")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="someone@example.com")
    tls = frozenset({TenantLabel(Label.CONFIDENTIAL_HEALTH, alice)})

    decision = decide_multi_tenant(
        tls,
        frozenset({cap}),
        action,
        target_tenant=alice,
    )
    assert decision.decision == Decision.DENY
    assert decision.rule == "health-meets-egress"


def test_no_target_tenant_checks_every_tenant() -> None:
    """Without a target tenant, the engine checks every tenant present
    and the strictest decision wins. Alice's health blocks; Bob is
    clean; overall is DENY."""
    alice = Tenant(id="alice")
    bob = Tenant(id="bob")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com")
    tls = frozenset(
        {
            TenantLabel(Label.CONFIDENTIAL_HEALTH, alice),
            TenantLabel(Label.TRUSTED_USER_DIRECT, bob),
        },
    )

    decision = decide_multi_tenant(tls, frozenset({cap}), action)
    assert decision.decision == Decision.DENY


def test_strictness_order_deny_beats_require_approval() -> None:
    """When per-tenant decisions disagree, DENY wins over
    REQUIRE_APPROVAL wins over ALLOW."""
    alice = Tenant(id="alice")
    bob = Tenant(id="bob")
    cap = Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*")
    action = Action(kind=CapabilityKind.QUEUE_PURCHASE, target="amazon")
    # alice: financial → require_approval on purchase egress
    # bob:   health    → deny on egress
    tls = frozenset(
        {
            TenantLabel(Label.CONFIDENTIAL_FINANCIAL, alice),
            TenantLabel(Label.CONFIDENTIAL_HEALTH, bob),
        },
    )
    decision = decide_multi_tenant(tls, frozenset({cap}), action)
    assert decision.decision == Decision.DENY


def test_multi_tenant_decision_carries_per_tenant_record() -> None:
    """The MultiTenantDecision exposes the per-tenant decisions for
    audit and trace surfaces. A reviewer needs to see WHY the
    overall decision came out the way it did."""
    alice = Tenant(id="alice")
    bob = Tenant(id="bob")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    action = Action(kind=CapabilityKind.SEND_EMAIL, target="x@example.com")
    tls = frozenset(
        {
            TenantLabel(Label.CONFIDENTIAL_HEALTH, alice),
            TenantLabel(Label.TRUSTED_USER_DIRECT, bob),
        },
    )
    decision = decide_multi_tenant(tls, frozenset({cap}), action)
    assert alice in decision.per_tenant
    assert bob in decision.per_tenant
    assert decision.per_tenant[alice].decision == Decision.DENY
    assert decision.per_tenant[bob].decision == Decision.ALLOW
