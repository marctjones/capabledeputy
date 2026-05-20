"""Override workflow — FR-036 / FR-038 / SC-014 dual-control.

Story:
  An operator denied at the policy chokepoint requests a grant. We
  exercise three control rules in one run:
    - self-attestation refused (FR-036 distinct attester / SC-014)
    - wrong attester refused (only listed attesters can confirm)
    - distinct authorized attester ALLOWS, grant transitions to ACTIVE
    - grant is single-use: the FIRST decide() consumes it; a SECOND
      attempt falls back to the normal policy (FR-036)

Security models exercised:
  - FR-036 single-use, distinct-attester override grants
  - FR-038 override-distinct-from-approval (origin=OVERRIDE_GRANTED)
  - SC-014 dual-control guarantee
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
from capabledeputy.tools.client import PolicyContext
from demos.scenarios._helpers import make_app, make_session, narrate


@pytest.mark.asyncio
async def test_override_workflow_demo(tmp_path: Any) -> None:
    narrate(
        "Override Workflow — dual-control + single-use",
        """
        A denied email send is unlocked via DUAL_CONTROL. We show the
        FSM's refusal of self-attestation, refusal of a non-authorized
        attester, and single-use consumption of the grant.
        """,
    )

    override_policies = OverridePolicies(
        by_floor={
            HardFloor.MAX_TIER_CLEARANCE: OverridePolicyEntry(
                floor=HardFloor.MAX_TIER_CLEARANCE,
                policy=OverridePolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"bob", "carol"}),
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

    narrate("Step 1", "Alice requests an override for SEND_EMAIL → bob@example.com.")
    request_resp = await handlers["override.request"](
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
    grant_id = request_resp["id"]
    assert request_resp["state"] == "pending_attestation"
    narrate("  → grant", f"id={grant_id[:8]}... state=pending_attestation")

    narrate(
        "Step 2",
        "Alice tries to self-attest. The FSM refuses (FR-036 / SC-014):\n"
        "    a single principal cannot satisfy both halves of dual control.",
    )
    self_attest = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "alice",
            "confirmed": True,
        }
    )
    assert self_attest.get("error") is not None or self_attest.get("state") != "active"
    narrate("  → refusal", f"attest result = {self_attest}")

    narrate(
        "Step 3",
        "Mallory (not in attester roster) tries to attest. Also refused.",
    )
    bad_attest = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "mallory",
            "confirmed": True,
        }
    )
    assert bad_attest.get("error") is not None or bad_attest.get("state") != "active"
    narrate("  → refusal", f"attest result = {bad_attest}")

    narrate("Step 4", "Bob (authorized) attests. Grant transitions to ACTIVE.")
    ok_attest = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "bob",
            "confirmed": True,
        }
    )
    assert ok_attest["state"] == "active"
    narrate("  → attestation", "Grant active. Alice can now retry the action.")

    narrate("Step 5", "First email.send under the grant. ALLOW with override-grant-active.")
    out1 = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "x", "body": "y"},
    )
    assert out1.decision == Decision.ALLOW
    assert out1.rule == "override-grant-active"
    narrate("  → first attempt", f"ALLOWED (rule={out1.rule}); grant consumed.")

    narrate(
        "Step 6",
        "Second email.send — the grant is single-use, so the policy now\n"
        "    falls back to the normal path (DENY by reversibility gate).",
    )
    out2 = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "x2", "body": "y2"},
    )
    assert out2.decision == Decision.DENY
    narrate(
        "  → second attempt",
        f"DENIED (rule={out2.rule}). Single-use property (FR-036) holds.",
    )
