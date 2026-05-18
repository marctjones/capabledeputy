"""002 US1 (T019): LLM-isolation invariant for delegation (SC-006 /
FR-012). The model can only *request* a narrowing; it can never author,
widen, or smuggle in a pre-built capability. Enforced structurally —
the delegation API accepts only a `DelegationRequest`, never a
`Capability` — and behaviorally — the only capability that reaches the
child is the engine-derived, DELEGATED-origin, attenuated one.
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
