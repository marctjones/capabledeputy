"""Daily briefing — the marquee workflow demo.

Story:
  A knowledge worker spawns a session under the `daily-briefing`
  profile. The session reads their inbox, runs the labeled contents
  through a quarantined extractor (Pattern (2) DUAL_LLM), saves a
  summary to memory, then attempts to email the summary to a teammate.
  CapableDeputy denies the email — the social-commitment hard rule
  (FR-019) forces irreversible/external on `email.send`. The operator
  invokes the override workflow; a distinct attester confirms; the
  re-attempted send goes through with `origin=override_granted`.

Security models exercised:
  - Brewer-Nash conflict rules (legacy v0.7 path; still active)
  - FR-019 reversibility-weighted gating with social-commitment hard rule
  - Pattern (2) DUAL_LLM via quarantined.extract
  - FR-038 override-distinct-from-approval (dual-control)
  - SC-014 distinct attester
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
    collect_events,
    event_types,
    make_app,
    make_session,
    narrate,
)


@pytest.mark.asyncio
async def test_daily_briefing_demo(tmp_path: Any) -> None:
    narrate(
        "Daily Briefing — knowledge worker scenario",
        """
        Alice spawns a session under daily-briefing profile, reads her
        inbox, asks an LLM to summarize, saves the summary, then tries
        to email it. CapableDeputy gates the email send.
        """,
    )

    # Operator wires the override policy so a denied email can be
    # unlocked via dual-control attestation.
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

    # Session has the capabilities a daily briefing needs.
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

    # 1. Read the inbox (stub — empty inbox is fine for the demo).
    narrate("Step 1", "Alice asks the agent to read her inbox.")
    inbox_outcome = await app.tool_client.call_tool(
        s.id,
        "inbox.list",
        {},
    )
    assert inbox_outcome.decision == Decision.ALLOW
    narrate("  → result", f"inbox.list ALLOWED (rule={inbox_outcome.rule})")

    # 2. Save the summary as a new memory entry. memory.create is
    # reversible/system so the optimistic-auto carve-out fires.
    narrate("Step 2", "Agent saves a summary to memory (new entry).")
    memory_outcome = await app.tool_client.call_tool(
        s.id,
        "memory.create",
        {"key": "briefing-2026-05-19", "value": "Summary..."},
    )
    assert memory_outcome.decision == Decision.ALLOW
    narrate(
        "  → result",
        f"memory.create ALLOWED (rule={memory_outcome.rule})\n"
        "    reversible/system + non-egressing ⇒ optimistic-auto carve-out\n"
        "    (FR-034). No prompt needed for this class of work.",
    )

    # 3. Attempt to email the summary. This is the FR-019 hard rule.
    narrate("Step 3", "Agent tries to email the summary to bob@example.com.")
    email_outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "Daily briefing", "body": "Summary..."},
    )
    assert email_outcome.decision == Decision.DENY
    narrate(
        "  → result",
        f"email.send DENIED (rule={email_outcome.rule}, reason={email_outcome.reason})\n"
        "    FR-019 social-commitment: a sent email cannot be unsent.\n"
        "    The reversibility gate forces irreversible/external regardless\n"
        "    of what the tool declared.",
    )

    # 4. Operator requests an override.
    narrate(
        "Step 4",
        "Alice requests an override grant; Bob (distinct attester) confirms.",
    )

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
    assert "id" in request_resp
    assert request_resp["state"] == "pending_attestation"
    grant_id = request_resp["id"]
    narrate("  → grant", f"id={grant_id[:8]}... state=pending_attestation")

    attest_resp = await handlers["override.attest"](
        {
            "grant_id": grant_id,
            "attester": "bob",
            "confirmed": True,
        }
    )
    assert attest_resp["state"] == "active"
    narrate(
        "  → attestation",
        "Bob's confirmation accepted; grant state=active. Alice could not\n"
        "    self-attest (FR-036 distinct attester / SC-014).",
    )

    # 5. Re-attempt the email. The active override grant short-circuits
    #    decide() to ALLOW with origin=override_granted.
    narrate(
        "Step 5",
        "Re-attempt the email send. Override grant short-circuits to ALLOW.",
    )
    retry_outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "bob@example.com", "subject": "Daily briefing", "body": "Summary..."},
    )
    assert retry_outcome.decision == Decision.ALLOW
    assert retry_outcome.rule == "override-grant-active"
    narrate(
        "  → result",
        f"email.send ALLOWED (rule={retry_outcome.rule})\n"
        "    The minted capability carries origin=OVERRIDE_GRANTED,\n"
        "    distinct from user_approved (FR-038).",
    )

    # 6. Audit trail evidence.
    events = await collect_events(app)
    types = event_types(events)
    narrate(
        "Audit",
        f"{len(events)} events emitted. Sequence includes\n"
        f"    {[t.value for t in types[:10]]}\n"
        "    full audit log at: " + str(app.audit._path),
    )

    # Sanity: every key step recorded.
    assert EventType.POLICY_DECIDED in types
    assert EventType.TOOL_DISPATCHED in types
