"""Daily briefing — the marquee workflow demo.

Story:
  Alice spawns a session under the `daily-briefing` profile. She
  asks the agent to read her inbox, save a one-line summary to
  memory, and email it to a teammate. The policy engine gates each
  step. The email send is denied; Alice requests an override; a
  distinct authorized attester signs off; the re-attempted send is
  ALLOWED with origin=OVERRIDE_GRANTED.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.audit.events import EventType
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
from demos.scenarios._helpers import (
    ai,
    audit,
    collect_events,
    demo_header,
    event_types,
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
async def test_daily_briefing_demo(tmp_path: Any) -> None:
    demo_header(
        "Daily Briefing — knowledge-worker scenario",
        blurb=(
            "Alice asks the agent to read her inbox, summarize, save the "
            "summary, then email it to a teammate. CapableDeputy gates each "
            "step; the send is denied and only proceeds after a distinct "
            "authorized attester signs off."
        ),
        models=("Brewer-Nash", "FR-019 social-commitment", "FR-038 override"),
        patterns=("dual-control attester", "FR-034 optimistic-auto"),
    )

    # security-officer is the attester role here — distinct from
    # bob@example.com (the email recipient). The roster is operator
    # config; we name it by role, not by accident-of-shared-prefix.
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
        axis_a_categories=(("personal", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.WRITE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
                Capability(
                    kind=CapabilityKind.CREATE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@example.com",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Read the inbox")
    user('"check my inbox"')
    ai("call inbox.list()")
    inbox_outcome = await app.tool_client.call_tool(s.id, "inbox.list", {})
    assert inbox_outcome.decision == Decision.ALLOW
    policy_outcome(inbox_outcome)
    tool("inbox.list → 0 messages (empty stub)")

    step(2, "Save the day's summary to memory")
    ai('call memory.create(key="briefing-2026-05-19", value=…)')
    memory_outcome = await app.tool_client.call_tool(
        s.id,
        "memory.create",
        {"key": "briefing-2026-05-19", "value": "Summary..."},
    )
    assert memory_outcome.decision == Decision.ALLOW
    policy_outcome(
        memory_outcome,
        rationale="reversible/system + non-egressing ⇒ optimistic-auto carve-out (FR-034).",
    )
    tool("memory.create → ok")

    step(3, "Email the summary to bob@example.com")
    ai('call email.send(to="bob@example.com", subject="Daily briefing", body=…)')
    email_outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "Daily briefing", "body": "Summary..."},
    )
    assert email_outcome.decision == Decision.DENY
    policy_outcome(
        email_outcome,
        rationale=(
            "FR-019 social-commitment: a sent email cannot be unsent. The "
            "reversibility gate forces irreversible/external regardless of "
            "what the tool declared."
        ),
    )
    tool("(skipped)")

    step(4, "Alice requests an override; security-officer attests")
    user("override.request  →  SEND_EMAIL  bob@example.com")

    from capabledeputy.daemon.override_handlers import make_override_handlers

    handlers = make_override_handlers(override_grants, override_policies)
    request_resp = await handlers["override.request"](
        {
            "session_id": str(s.id),
            "action_kind": "SEND_EMAIL",
            "target": "bob@example.com",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "category": "personal",
            "tier": "regulated",
            "friction_confirmed": True,
        }
    )
    assert request_resp["state"] == "pending_attestation"
    policy("pending_attestation", rationale=f"grant id={request_resp['id'][:8]}…")

    note(
        "security-officer (distinct human, not bob@example.com) reviews and "
        "calls override.attest from their own terminal."
    )
    user("override.attest  --attester security-officer  --confirmed")
    attest_resp = await handlers["override.attest"](
        {
            "grant_id": request_resp["id"],
            "attester": "security-officer",
            "confirmed": True,
        }
    )
    assert attest_resp["state"] == "active"
    policy(
        "active",
        rule="FR-036 distinct-attester",
        rationale="SC-014: attester ≠ invoker satisfied. Grant active.",
    )

    step(5, "Re-attempt the email — override grant short-circuits to ALLOW")
    ai('call email.send(to="bob@example.com", …) — retry')
    retry_outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "Daily briefing", "body": "Summary..."},
    )
    assert retry_outcome.decision == Decision.ALLOW
    assert retry_outcome.rule == "override-grant-active"
    policy_outcome(
        retry_outcome,
        rationale=(
            "Minted capability carries origin=OVERRIDE_GRANTED, distinct from "
            "user_approved (FR-038). Grant is now CONSUMED — single use."
        ),
    )
    tool("email.send → sent")

    events = await collect_events(app)
    types = event_types(events)
    audit(f"{len(events)} events emitted. First 6: " + ", ".join(t.value for t in types[:6]))
    note(f"full audit log at {app.audit._path}")
    assert EventType.POLICY_DECIDED in types
    assert EventType.TOOL_DISPATCHED in types
