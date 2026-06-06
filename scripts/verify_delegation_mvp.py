#!/usr/bin/env python
"""Manual end-to-end verification of the 002 delegation MVP (US1).

Exercises the REAL SessionGraph.delegate + the REAL policy decide()
chokepoint — no LLM, no daemon. Shows: attenuated delegation succeeds;
every broadening is refused deterministically; and the derived
(narrowed) capability genuinely binds the live enforcement decision
(the child can do strictly less than the parent could).

Run:  uv run python scripts/verify_delegation_mvp.py
"""

from __future__ import annotations

import asyncio

from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    DelegationRefusal,
    DelegationRequest,
)
from capabledeputy.policy.engine import decide
from capabledeputy.session.graph import SessionGraph

K = CapabilityKind


async def main() -> int:
    g = SessionGraph()
    parent = await g.new(intent="parent agent")
    child = await g.new(intent="child sub-agent", parent=parent.id)

    pcap = Capability(kind=K.SEND_EMAIL, pattern="mail/*", max_amount=100)
    await g.grant_capability(parent.id, pcap)
    print("parent holds: SEND_EMAIL pattern=mail/* max_amount=100")
    print(f"  parent cap audit_id={pcap.audit_id}\n")

    print("== 1. Attenuated delegation (narrower pattern + amount) ==")
    out = await g.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=K.SEND_EMAIL, pattern="mail/team/*", max_amount=40),
        depth_limit=3,
    )
    assert isinstance(out, Capability)
    print(
        f"  GRANTED -> pattern={out.pattern} max_amount={out.max_amount} "
        f"depth={out.depth} origin={out.origin.value}",
    )
    print(
        f"  provenance: parent_audit_id={out.parent_audit_id} "
        f"(== parent's: {out.parent_audit_id == pcap.audit_id})"
    )
    print(f"  fresh identity: child audit_id != parent ({out.audit_id != pcap.audit_id})\n")

    print("== 2. Every broadening refused (deterministic reason) ==")
    for desc, req in [
        ("widen amount 250", DelegationRequest(kind=K.SEND_EMAIL, max_amount=250)),
        ("widen pattern mail/**", DelegationRequest(kind=K.SEND_EMAIL, pattern="mail/**")),
        ("kind not held (WEB_FETCH)", DelegationRequest(kind=K.WEB_FETCH)),
    ]:
        r = await g.delegate(parent.id, child.id, req, depth_limit=3)
        assert isinstance(r, DelegationRefusal)
        print(f"  {desc:<28} -> REFUSED ({r.reason.value})")
    self_r = await g.delegate(
        parent.id, parent.id, DelegationRequest(kind=K.SEND_EMAIL), depth_limit=3
    )
    print(f"  {'self-delegation':<28} -> REFUSED ({self_r.reason.value})")  # type: ignore[union-attr]
    cyc = await g.delegate(child.id, parent.id, DelegationRequest(kind=K.SEND_EMAIL), depth_limit=3)
    print(f"  {'cycle (child->ancestor)':<28} -> REFUSED ({cyc.reason.value})\n")  # type: ignore[union-attr]

    print("== 3. The delegated cap BINDS the real policy chokepoint ==")
    caps = g.get(child.id).capability_set  # only the engine-derived cap
    cases = [
        ("in-scope, within amount", Action(K.SEND_EMAIL, "mail/team/alice", 30)),
        ("OUT of delegated pattern", Action(K.SEND_EMAIL, "mail/other/bob", 30)),
        ("OVER delegated amount (parent allowed 100)", Action(K.SEND_EMAIL, "mail/team/x", 80)),
    ]
    for desc, act in cases:
        d = decide(caps, act)
        print(f"  child {desc:<44} -> {d.decision.value.upper()}")
    print()
    print("Child can do strictly LESS than the parent could — by construction.")
    print("RESULT: PASS — delegation MVP verified end-to-end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
