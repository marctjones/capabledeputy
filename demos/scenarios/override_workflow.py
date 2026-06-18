"""Override workflow — dual-control + single-use grant FSM.

Exercises the override grant state machine: request → refused
self-attest → refused wrong attester → distinct authorized attester
ALLOWS → first use consumes the grant → second attempt falls back to
the normal policy.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.overrides import (
    HardFloor,
    OverrideGrantStore,
    OverridePolicies,
    OverridePolicy,
    OverridePolicyEntry,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.policy.context import PolicyContext
from demos.scenarios._helpers import (
    ai,
    demo_header,
    make_app,
    make_session,
    note,
    policy,
    policy_outcome,
    step,
    tool,
    user,
)


@pytest.mark.asyncio
async def test_override_workflow_demo(tmp_path: Any) -> None:
    demo_header(
        "Override Workflow — dual-control + single-use",
        blurb=(
            "A denied email is unlocked via DUAL_CONTROL. We show three "
            "refusal paths plus the successful path, then confirm "
            "single-use consumption."
        ),
        models=("FR-036 distinct-attester", "FR-038 override origin"),
        patterns=("dual-control FSM", "single-use grant"),
    )

    override_policies = OverridePolicies(
        by_floor={
            HardFloor.MAX_TIER_CLEARANCE: OverridePolicyEntry(
                floor=HardFloor.MAX_TIER_CLEARANCE,
                policy=OverridePolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"security-officer", "manager"}),
                expiry_seconds=300,
            ),
        },
    )
    override_grants = OverrideGrantStore()
    ctx = PolicyContext(
        override_policies=override_policies,
        override_grants=override_grants,
    )
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("work", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@example.com",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    from capabledeputy.daemon.override_handlers import make_override_handlers

    handlers = make_override_handlers(override_grants, override_policies)

    step(1, "Alice requests an override")
    user("override.request  →  SEND_EMAIL  bob@example.com")
    req = await handlers["override.request"](
        {
            "session_id": str(s.id),
            "action_kind": "SEND_EMAIL",
            "target": "bob@example.com",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "category": "work",
            "tier": "sensitive",
            "friction_confirmed": True,
        }
    )
    grant_id = req["id"]
    assert req["state"] == "pending_attestation"
    policy("pending_attestation", rationale=f"grant id={grant_id[:8]}…")

    step(2, "Alice tries to self-attest — refused")
    user("override.attest  --attester alice  --confirmed")
    r = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "alice",
            "confirmed": True,
        }
    )
    assert r.get("refused")
    policy(
        "refused",
        rule="attester_same_as_invoker",
        rationale="FR-036 / SC-014: one principal cannot satisfy both halves.",
    )

    step(3, "Mallory (not on attester roster) tries — refused")
    user("override.attest  --attester mallory  --confirmed")
    r = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "mallory",
            "confirmed": True,
        }
    )
    assert r.get("refused")
    policy(
        "refused",
        rule="attester_unauthorized",
        rationale="mallory is not in the operator-configured attester roster.",
    )

    step(4, "security-officer (authorized + distinct) attests")
    user("override.attest  --attester security-officer  --confirmed")
    r = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "security-officer",
            "confirmed": True,
        }
    )
    assert r["state"] == "active"
    policy(
        "active",
        rule="FR-036 distinct-attester",
        rationale="Grant transitions to ACTIVE; Alice can now retry.",
    )

    step(5, "First retry — override grant short-circuits to ALLOW")
    ai('call email.send(to="bob@example.com", …)')
    out1 = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "x", "body": "y"},
    )
    assert out1.decision == Decision.ALLOW
    assert out1.rule == "override-grant-active"
    policy_outcome(
        out1,
        rationale="Capability minted with origin=OVERRIDE_GRANTED (FR-038).",
    )
    tool("email.send → sent. Grant now CONSUMED.")

    step(6, "Second retry — grant is single-use; policy falls back")
    ai('call email.send(to="bob@example.com", …) — second time')
    out2 = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "x2", "body": "y2"},
    )
    assert out2.decision == Decision.REQUIRE_APPROVAL
    policy_outcome(
        out2,
        rationale=(
            "Single-use property (FR-036): the consumed grant no longer "
            "matches, so the send is re-gated — irreversible communication "
            "egress falls back to the normal approval gate (amended FR-019)."
        ),
    )
    note("Alice approves the second send at the moment, or files a fresh override.")
