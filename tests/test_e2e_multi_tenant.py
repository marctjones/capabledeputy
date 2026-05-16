"""Demo 14 — multi-tenant household.

Alice and Bob share a CapableDeputy install. Each has their own
labeled compartments. Alice's `confidential.health@alice` does NOT
fire `health-meets-egress` when Bob attempts an outbound action under
his own tenant — because Bob's compartment is separate from Alice's.

The decision engine runs the conflict-rule loop per tenant; the
strictest result wins (DENY > REQUIRE_APPROVAL > ALLOW). With a
target-tenant scope, only that tenant's labels contribute.
"""

from __future__ import annotations

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.policy.multi_tenant_engine import decide_multi_tenant
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tenancy import Tenant, TenantLabel


def test_alice_health_does_not_block_bob_egress() -> None:
    alice = Tenant(id="alice", display_name="Alice")
    bob = Tenant(id="bob", display_name="Bob")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    action = Action(
        kind=CapabilityKind.SEND_EMAIL,
        target="bob-friend@example.com",
    )
    # Alice has read health; Bob's tenant is clean.
    tls = frozenset(
        {
            TenantLabel(Label.CONFIDENTIAL_HEALTH, alice),
            TenantLabel(Label.TRUSTED_USER_DIRECT, bob),
        },
    )
    # Bob is the actor.
    decision = decide_multi_tenant(tls, frozenset({cap}), action, target_tenant=bob)
    assert decision.decision == Decision.ALLOW


def test_alice_health_blocks_alice_egress() -> None:
    """Same scenario but Alice is the actor — health-meets-egress fires
    in Alice's compartment."""
    alice = Tenant(id="alice")
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*@example.com")
    action = Action(
        kind=CapabilityKind.SEND_EMAIL,
        target="alice-doc@example.com",
    )
    tls = frozenset({TenantLabel(Label.CONFIDENTIAL_HEALTH, alice)})
    decision = decide_multi_tenant(tls, frozenset({cap}), action, target_tenant=alice)
    assert decision.decision == Decision.DENY
    assert decision.rule == "health-meets-egress"


def test_no_target_tenant_checks_every_tenant() -> None:
    """Without a target tenant, the engine checks every tenant present
    and the strictest decision wins. Alice has health; Bob has clean
    state; overall is DENY."""
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


def test_per_tenant_record_in_decision() -> None:
    """The MultiTenantDecision exposes per-tenant decisions for audit."""
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
    assert decision.per_tenant[alice].decision == Decision.DENY
    assert decision.per_tenant[bob].decision == Decision.ALLOW


def test_two_tenants_with_distinct_compartments() -> None:
    """Alice's financial summary doesn't cross to Bob's compartment.
    Bob doing a purchase doesn't get gated by Alice's labels."""
    alice = Tenant(id="alice")
    bob = Tenant(id="bob")
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=1_000,
    )
    action = Action(kind=CapabilityKind.QUEUE_PURCHASE, target="amazon", amount=20)
    tls = frozenset(
        {
            TenantLabel(Label.CONFIDENTIAL_FINANCIAL, alice),
        },
    )
    # Bob's purchase under his own tenant — no labels in his compartment.
    bob_decision = decide_multi_tenant(tls, frozenset({cap}), action, target_tenant=bob)
    assert bob_decision.decision == Decision.ALLOW
    # Alice's purchase under her own tenant — fires financial-meets-purchase.
    alice_decision = decide_multi_tenant(
        tls,
        frozenset({cap}),
        action,
        target_tenant=alice,
    )
    assert alice_decision.decision == Decision.REQUIRE_APPROVAL
    assert alice_decision.rule == "financial-meets-purchase"


# --- Time/rate constraints enforce under the multi-tenant engine --------

from datetime import UTC, datetime, timedelta  # noqa: E402
from uuid import UUID  # noqa: E402

from capabledeputy.policy.capabilities import RateLimit  # noqa: E402

_MT_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)
_MT_AID = "33333333-3333-3333-3333-333333333333"


def test_expired_capability_denies_under_multi_tenant() -> None:
    alice = Tenant(id="alice")
    tls = frozenset({TenantLabel(Label.TRUSTED_USER_DIRECT, alice)})
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=_MT_NOW,
    )
    action = Action(kind=CapabilityKind.READ_FS, target="/x")
    d = decide_multi_tenant(
        tls,
        frozenset({cap}),
        action,
        target_tenant=alice,
        now=_MT_NOW + timedelta(seconds=1),
    )
    assert d.decision == Decision.DENY
    assert d.per_tenant[alice].rule == "capability-expired"


def test_future_deadline_allows_under_multi_tenant() -> None:
    alice = Tenant(id="alice")
    tls = frozenset({TenantLabel(Label.TRUSTED_USER_DIRECT, alice)})
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        expires_at=_MT_NOW + timedelta(hours=1),
    )
    action = Action(kind=CapabilityKind.READ_FS, target="/x")
    d = decide_multi_tenant(
        tls,
        frozenset({cap}),
        action,
        target_tenant=alice,
        now=_MT_NOW,
    )
    assert d.decision == Decision.ALLOW


def test_rate_limit_enforced_under_multi_tenant() -> None:
    alice = Tenant(id="alice")
    tls = frozenset({TenantLabel(Label.TRUSTED_USER_DIRECT, alice)})
    cap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        audit_id=UUID(_MT_AID),
        rate_limit=RateLimit(max_uses=1, window_seconds=3600),
    )
    action = Action(kind=CapabilityKind.READ_FS, target="/x")
    uses = {_MT_AID: (_MT_NOW - timedelta(seconds=1),)}
    d = decide_multi_tenant(
        tls,
        frozenset({cap}),
        action,
        target_tenant=alice,
        now=_MT_NOW,
        cap_uses=uses,
    )
    assert d.decision == Decision.DENY
    assert d.per_tenant[alice].rule == "rate-limit-exceeded"


def test_multi_tenant_without_now_is_backward_compatible() -> None:
    """Omitting now/cap_uses keeps prior behavior (non-expiring,
    unlimited) — existing callers unaffected."""
    alice = Tenant(id="alice")
    tls = frozenset({TenantLabel(Label.TRUSTED_USER_DIRECT, alice)})
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    action = Action(kind=CapabilityKind.READ_FS, target="/x")
    d = decide_multi_tenant(tls, frozenset({cap}), action, target_tenant=alice)
    assert d.decision == Decision.ALLOW
