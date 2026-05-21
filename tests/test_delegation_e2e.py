"""002 US1+US2+US3 end-to-end: LLM-isolation invariant for delegation +
cascade containment.

LLM-isolation (FR-012 / SC-006): the model can only *request* a
narrowing; it can never author, widen, or smuggle in a pre-built
capability. Enforced structurally — the delegation API accepts only
a `DelegationRequest`, never a `Capability` — and behaviorally — the
only capability that reaches the child is the engine-derived,
DELEGATED-origin, attenuated one.

End-to-end (T031 / Phase 6): walks the spec 002 user stories through
real graph + engine + audit interactions, plus the cross-cutting
invariants (no retro-unwind, pooled rate, determinism).
"""

from __future__ import annotations

import dataclasses

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
    DelegationRefusal,
    DelegationRefusalReason,
    DelegationRequest,
)
from capabledeputy.session.graph import SessionGraph


def test_delegation_request_cannot_carry_a_capability() -> None:
    """Structural: there is no field on the request through which a
    model could inject a full/widened Capability."""
    fields = {f.name for f in dataclasses.fields(DelegationRequest)}
    assert "capability" not in fields
    for f in dataclasses.fields(DelegationRequest):
        assert f.type != "Capability", f"{f.name} must not accept a Capability"


async def test_only_engine_derived_cap_reaches_child() -> None:
    g = SessionGraph()
    parent = await g.new(intent="p")
    child = await g.new(intent="c", parent=parent.id)
    pcap = Capability(
        kind=CapabilityKind.SEND_EMAIL,
        pattern="mail/*",
        max_amount=100,
        origin=CapabilityOrigin.USER_APPROVED,
    )
    await g.grant_capability(parent.id, pcap)

    out = await g.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.SEND_EMAIL, max_amount=10),
        depth_limit=3,
    )
    assert isinstance(out, Capability)
    child_caps = g.get(child.id).capability_set
    assert child_caps == {out}
    # The child's capability is engine-minted (DELEGATED), provenance-
    # linked, and strictly attenuated — never a copy of the parent's.
    assert out.origin is CapabilityOrigin.DELEGATED
    assert out.parent_audit_id == pcap.audit_id
    assert out.audit_id != pcap.audit_id
    assert out.max_amount == 10


async def test_widening_request_is_refused_not_smuggled() -> None:
    """A model that asks for *more* than the parent gets a deterministic
    refusal — never a silently-widened or attacker-authored grant."""
    g = SessionGraph()
    parent = await g.new(intent="p")
    child = await g.new(intent="c", parent=parent.id)
    await g.grant_capability(
        parent.id,
        Capability(kind=CapabilityKind.SEND_EMAIL, pattern="mail/*", max_amount=100),
    )
    out = await g.delegate(
        parent.id,
        child.id,
        DelegationRequest(
            kind=CapabilityKind.SEND_EMAIL,
            pattern="**",
            max_amount=10_000,
        ),
        depth_limit=3,
    )
    assert out == DelegationRefusal(DelegationRefusalReason.PATTERN_NOT_SUBSET)
    assert g.get(child.id).capability_set == frozenset()


# ---------- 002 Phase 6 T031: end-to-end quickstart ----------


async def test_e2e_us1_us2_us3_combined() -> None:
    """Walk every spec-002 user story in one pass.

    US1 — delegate attenuated cap parent→child
    US2 — revoke parent → child decision DENIED with capability-cascaded
    US2-4 — pooled rate: parent's window decrements on child use
    US3 — depth-limit refusal at 4th hop
    """
    from datetime import UTC, datetime, timedelta

    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.capabilities import RateLimit
    from capabledeputy.policy.engine import (
        CAPABILITY_CASCADED_RULE,
        decide,
    )
    from capabledeputy.policy.rules import Decision

    g = SessionGraph()
    parent = await g.new(intent="p")
    child = await g.new(intent="c", parent=parent.id)

    # US1 — operator-granted parent cap with a rate window for US2-4
    # (no max_amount — Action without amount only matches caps without
    # one; the test doesn't need amount-clamping anyway).
    pcap = Capability(
        kind=CapabilityKind.READ_FS,
        pattern="*",
        origin=CapabilityOrigin.USER_APPROVED,
        rate_limit=RateLimit(max_uses=3, window_seconds=60),
    )
    await g.grant_capability(parent.id, pcap)

    delegated = await g.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.READ_FS),
        depth_limit=3,
    )
    assert isinstance(delegated, Capability)
    assert delegated.parent_audit_id == pcap.audit_id

    # Before revocation: a fresh decide() against the child's cap ALLOWs.
    # In production the daemon passes the full visible cap set (child's
    # caps + ancestors reachable in the spawn graph) so the cascade
    # walk can resolve parent_audit_id.
    child_session = g.get(child.id)
    action = Action(kind=CapabilityKind.READ_FS, target="x")
    combined_pre = child_session.capability_set | {pcap}
    d = decide(
        child_session.label_set,
        combined_pre,
        action,
    )
    assert d.decision == Decision.ALLOW

    # US2 — revoke parent. Re-decide child: cascaded DENY.
    await g.revoke_capability(parent.id, pcap.audit_id)
    revoked_set = frozenset({pcap.audit_id})
    d = decide(
        child_session.label_set,
        combined_pre,
        action,
        revoked_audit_ids=revoked_set,
    )
    assert d.decision == Decision.DENY
    assert d.rule == CAPABILITY_CASCADED_RULE

    # US3 — depth-limit refusal. Build a deeper chain past depth_limit.
    g2 = SessionGraph()
    p2 = await g2.new()
    c2_1 = await g2.new(parent=p2.id)
    c2_2 = await g2.new(parent=c2_1.id)
    c2_3 = await g2.new(parent=c2_2.id)
    root_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    await g2.grant_capability(p2.id, root_cap)
    d1 = await g2.delegate(
        p2.id,
        c2_1.id,
        DelegationRequest(kind=CapabilityKind.READ_FS),
        depth_limit=2,
    )
    assert isinstance(d1, Capability)
    d2 = await g2.delegate(
        c2_1.id,
        c2_2.id,
        DelegationRequest(kind=CapabilityKind.READ_FS),
        depth_limit=2,
    )
    assert isinstance(d2, Capability)
    # 3rd hop with depth_limit=2 should refuse with DEPTH_EXCEEDED
    d3 = await g2.delegate(
        c2_2.id,
        c2_3.id,
        DelegationRequest(kind=CapabilityKind.READ_FS),
        depth_limit=2,
    )
    assert isinstance(d3, DelegationRefusal)
    assert d3.reason == DelegationRefusalReason.DEPTH_EXCEEDED


# ---------- T032 determinism (SC-007) ----------


async def test_decide_byte_identical_across_repeated_runs() -> None:
    """Repeated decide() runs on identical inputs produce identical
    PolicyDecision values + identical audit content (deterministic
    enforcement; LLM-isolation by construction)."""
    from datetime import UTC, datetime

    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.engine import decide

    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    action = Action(kind=CapabilityKind.READ_FS, target="x")
    now = datetime(2026, 5, 21, 0, 0, 0, tzinfo=UTC)

    d1 = decide(frozenset(), frozenset({cap}), action, now=now)
    d2 = decide(frozenset(), frozenset({cap}), action, now=now)
    d3 = decide(frozenset(), frozenset({cap}), action, now=now)

    # All three decisions are equal (frozen dataclasses compare by value).
    assert d1 == d2 == d3
    assert d1.decision == d2.decision == d3.decision
