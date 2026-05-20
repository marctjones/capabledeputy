"""Calendar with external invites — untrusted invite handling.

Workflow: an external party sends a calendar invite. The agent reads
the invite (UNTRUSTED_EXTERNAL via inbox.read), accepts it by writing
a calendar event, and is later prevented from forwarding the invite
metadata to a third party — the session is now tainted from reading
external content.

This is the canonical "calendar invite from someone outside the org"
flow that every personal assistant has to handle. Without the labeled
chokepoint the agent would happily forward the invite (and any
injection text in it) onward.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
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
    policy_outcome,
    step,
    tool,
    user,
)


@pytest.mark.asyncio
async def test_calendar_with_invites_demo(tmp_path: Any) -> None:
    demo_header(
        "Calendar With Invites — untrusted invite propagation",
        blurb=(
            "An external party sends a calendar invite. The agent reads "
            "it (UNTRUSTED_EXTERNAL), creates the event, then tries to "
            "forward the invite — refused. Untrusted-external taint "
            "carries forward through the workflow."
        ),
        models=("Brewer-Nash untrusted-meets-egress", "FR-019 social-commitment"),
        patterns=("untrusted-external label propagation",),
    )

    ctx = PolicyContext()
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()
    now = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
    app.inbox.add(
        InboundMessage(
            id="invite-conf",
            sender="conf-orga@external.example",
            subject="Invite: Industry Conference Lunch",
            body=(
                "You're invited to the conference lunch on 2026-06-15 "
                "12:00-13:30. RSVP by replying."
            ),
            received_at=now,
        ),
    )

    s = await make_session(
        app,
        axis_a_categories=(("calendar", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.READ_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.CREATE_CAL,
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

    step(1, "Read the invite — carries UNTRUSTED_EXTERNAL")
    user('"check what just came in"')
    ai('call inbox.read(id="invite-conf")')
    invite = await app.tool_client.call_tool(s.id, "inbox.read", {"id": "invite-conf"})
    assert invite.decision is Decision.ALLOW
    policy_outcome(invite)
    tool("inbox.read → invite body; session now tainted UNTRUSTED_EXTERNAL.")

    step(2, "Accept by creating a calendar event")
    user('"add it to my calendar"')
    ai('call calendar.create_event(title="Conf Lunch", starts_at=…, ends_at=…)')
    created = await app.tool_client.call_tool(
        s.id,
        "calendar.create_event",
        {
            "title": "Industry Conference Lunch",
            "starts_at": "2026-06-15T12:00:00+00:00",
            "ends_at": "2026-06-15T13:30:00+00:00",
        },
    )
    policy_outcome(
        created,
        rationale=(
            "create_event is reversible-with-friction/human; the operator "
            "approves via the calendar UI in a real deployment. In this "
            "demo we proceed."
        ),
    )
    if created.decision is Decision.ALLOW:
        tool("calendar.create_event → event scheduled")
    else:
        tool("(deferred until operator approves)")

    step(3, "Try to forward the invite text to a teammate")
    note(
        "An assistant that didn't track taint would happily forward the "
        "invite body — including any injection text it carried. Here "
        "the session is UNTRUSTED_EXTERNAL, so social.send_email is "
        "Brewer-Nash refused."
    )
    ai('call email.send(to="team@example.com", body="…invite text…")')
    forward = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {
            "to": "team@example.com",
            "subject": "FYI: conference invite",
            "body": "(invite body would go here)",
        },
    )
    assert forward.decision is Decision.DENY
    policy_outcome(
        forward,
        rationale=(
            "Brewer-Nash untrusted-meets-egress: an untrusted-external "
            "session cannot egress via email. The fix is to either "
            "summarize via quarantined.extract (Pattern ②) or to forward "
            "from a fresh session that never read the raw invite."
        ),
    )
    tool("(skipped)")
