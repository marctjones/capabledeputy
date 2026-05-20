"""Email drafting workflow — draft → list → send (refused → override).

Realistic flow: the agent reads inbound mail (tainting the session
UNTRUSTED_EXTERNAL), composes a reply, saves it as a draft (LOCAL —
non-egressing, no gate fires), lists drafts so the operator can see
what's queued, and finally tries to send the draft. The send is
refused (Brewer-Nash + FR-019). The operator requests an override;
a distinct attester signs off; the draft-send succeeds.

This demonstrates the structural separation between **composing** an
email and **sending** one. Drafts are cheap; sends are committed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from capabledeputy.daemon.override_handlers import make_override_handlers
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
from capabledeputy.tools.native.inbox import InboundMessage
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
async def test_email_drafting_workflow_demo(tmp_path: Any) -> None:
    demo_header(
        "Email Drafting — compose locally, send across the chokepoint",
        blurb=(
            "Draft is local + non-egressing, so it ALLOWs. The send "
            "is the boundary-crossing action; the policy chokepoint "
            "decides there. Override clears the gate exactly once."
        ),
        models=(
            "FR-019 social-commitment on send",
            "FR-038 override-distinct-from-approval",
        ),
        patterns=("draft-then-send separation",),
    )

    override_policies = OverridePolicies(
        by_floor={
            HardFloor.MAX_TIER_CLEARANCE: OverridePolicyEntry(
                floor=HardFloor.MAX_TIER_CLEARANCE,
                policy=OverridePolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"security-officer"}),
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

    # Pre-load an inbound email so reading taints the session.
    app.inbox.add(
        InboundMessage(
            id="anna-hello",
            sender="anna@partner.com",
            subject="Quick favor",
            body="Could you confirm Friday's slot?",
            received_at=datetime(2026, 5, 20, 9, 30, tzinfo=UTC),
        ),
    )

    s = await make_session(
        app,
        axis_a_categories=(("inbox", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.CREATE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                # Two SEND_EMAIL caps: recipient-pattern for direct
                # sends, wildcard for the coarser draft_send path.
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*@partner.com",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Read the inbound — session now UNTRUSTED_EXTERNAL")
    user('"reply to Anna saying yes"')
    ai('call inbox.read(id="anna-hello")')
    incoming = await app.tool_client.call_tool(s.id, "inbox.read", {"id": "anna-hello"})
    assert incoming.decision is Decision.ALLOW
    policy_outcome(incoming)
    tool("inbox.read → ok; UNTRUSTED_EXTERNAL on session.")

    step(2, "Save a draft reply — LOCAL, non-egressing, ALLOWs")
    note(
        "email.draft_save is CREATE_FS / data.create_local — it persists "
        "the reply to a local DraftBox. No social-commitment gate; the "
        "session's taint doesn't matter here."
    )
    ai(
        'call email.draft_save(to="anna@partner.com", '
        'subject="Re: Quick favor", body="Yes, Friday works.")'
    )
    draft = await app.tool_client.call_tool(
        s.id,
        "email.draft_save",
        {
            "to": "anna@partner.com",
            "subject": "Re: Quick favor",
            "body": "Yes, Friday works.",
        },
    )
    assert draft.decision is Decision.ALLOW
    draft_id = draft.output["id"]
    policy_outcome(draft)
    tool(f"email.draft_save → draft id = {draft_id[:8]}…")

    step(3, "List drafts so the operator can review")
    ai("call email.draft_list()")
    drafts = await app.tool_client.call_tool(s.id, "email.draft_list", {})
    assert drafts.decision is Decision.ALLOW
    policy_outcome(drafts)
    tool(f"email.draft_list → {len(drafts.output['drafts'])} pending")

    step(4, "Attempt to send the draft — refused")
    note(
        "Now we cross the boundary. email.draft_send is SEND_EMAIL / "
        "social.send_email — same policy gates as a fresh send. The "
        "session is UNTRUSTED_EXTERNAL-tainted from inbox.read, so "
        "Brewer-Nash untrusted-meets-egress refuses."
    )
    ai(f"call email.draft_send(id={draft_id[:8]}…)")
    send_try = await app.tool_client.call_tool(
        s.id,
        "email.draft_send",
        {"id": draft_id},
    )
    assert send_try.decision is Decision.DENY
    policy_outcome(send_try)
    tool("(skipped — draft remains in DraftBox)")

    step(5, "Override flow: distinct attester signs off")
    handlers = make_override_handlers(override_grants, override_policies)
    user("override.request  →  SEND_EMAIL  anna@partner.com")
    # The override grant target must match the action target the
    # dispatcher will compute. email.draft_send uses no target_arg, so
    # the action target is the empty string.
    req = await handlers["override.request"](
        {
            "session_id": str(s.id),
            "action_kind": "SEND_EMAIL",
            "target": "",
            "floor": "max-tier-clearance",
            "invoker": "alice",
            "category": "inbox",
            "tier": "sensitive",
            "friction_confirmed": True,
        }
    )
    grant_id = req["id"]
    policy("pending_attestation", rationale=f"grant id={grant_id[:8]}…")
    user("override.attest  --attester security-officer")
    attest = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "security-officer",
            "confirmed": True,
        }
    )
    assert attest["state"] == "active"
    policy("active", rule="FR-036 distinct-attester", rationale="grant active.")

    step(6, "Retry the draft send — override short-circuits to ALLOW")
    ai(f"call email.draft_send(id={draft_id[:8]}…) — retry")
    final_send = await app.tool_client.call_tool(
        s.id,
        "email.draft_send",
        {"id": draft_id},
    )
    assert final_send.decision is Decision.ALLOW
    assert final_send.rule == "override-grant-active"
    policy_outcome(final_send)
    tool(
        f"email.draft_send → sent ({final_send.output['id'][:8]}…); "
        "draft removed from DraftBox; grant CONSUMED."
    )

    # Verify the draft is gone and the outbox has it.
    assert app.email_drafts.get(__import__("uuid").UUID(draft_id)) is None
    assert any(s.to == "anna@partner.com" for s in app.email_outbox.all())
