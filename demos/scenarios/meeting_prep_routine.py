"""Meeting prep routine — calendar.* + fs.* + inbox.* + bundle.

The morning-prep workflow every desktop assistant has to run cleanly:

  1. Read today's calendar events.
  2. Pull related inbox threads (UNTRUSTED_EXTERNAL — invited
     attendees may have sent fresh notes).
  3. Read related on-disk docs (REAL fs.read).
  4. Compose an agenda markdown to disk (REAL fs.create).
  5. Try to revise the agenda (REAL fs.modify) — REQUIRE_APPROVAL
     because fs.modify is reversible-with-friction/human.
  6. Update the calendar event with a note pointing at the agenda
     (calendar.update_event — MODIFY_CAL, destructive-op gate fires
     unless the cap declared allows_destructive).
  7. Bundle: draft notification emails to all attendees and present
     them as ONE approval bundle the operator can review at once.
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
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.programmatic import (
    LabeledValue,
    dry_run_for_bundle,
    execute_with_approved_bundle,
)
from capabledeputy.tools.native.calendar import CalendarEvent
from capabledeputy.tools.native.inbox import InboundMessage
from demos.scenarios._helpers import (
    ai,
    audit,
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
async def test_meeting_prep_routine_demo(tmp_path: Any) -> None:
    demo_header(
        "Meeting Prep Routine — calendar + fs + inbox + bundle",
        blurb=(
            "Run the morning prep workflow end-to-end. Real fs.* tools "
            "against real files; calendar update gated by the "
            "destructive-op gate; attendee notifications dispatched as "
            "a single approval bundle."
        ),
        models=(
            "FR-019 reversibility (fs.modify, calendar.update)",
            "Brewer-Nash untrusted-meets-egress",
            "destructive-op gate on MODIFY_CAL",
        ),
        patterns=(
            "approval bundle (dry-run + approve_all + execute)",
            "operator standing cap for low-risk mutations",
        ),
    )

    ctx = PolicyContext()
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()

    # Stage today's calendar with two events.
    now = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
    event_a = CalendarEvent(
        id=__import__("uuid").uuid4(),
        title="Roadmap sync",
        starts_at=now.replace(hour=10),
        ends_at=now.replace(hour=11),
        notes="",
    )
    event_b = CalendarEvent(
        id=__import__("uuid").uuid4(),
        title="Vendor call",
        starts_at=now.replace(hour=14),
        ends_at=now.replace(hour=15),
        notes="",
    )
    app.calendar.add(event_a)
    app.calendar.add(event_b)
    # Inbox: a fresh note from an external attendee.
    app.inbox.add(
        InboundMessage(
            id="vendor-note",
            sender="vendor@external.example",
            subject="Re: vendor call",
            body="Please come prepared with Q3 numbers.",
            received_at=now,
        ),
    )
    # On-disk source notes.
    docs = tmp_path / "docs"
    docs.mkdir()
    prior = docs / "prior-decisions.txt"
    prior.write_text(
        "Decision 2026-05-15: postpone vendor switch until Q4.\n",
        encoding="utf-8",
    )
    agenda = docs / "agenda-2026-05-20.md"

    s = await make_session(
        app,
        axis_a_categories=(("work", Tier.SENSITIVE),),
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
                Capability(
                    kind=CapabilityKind.MODIFY_FS,
                    pattern=str(agenda),
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
                Capability(
                    kind=CapabilityKind.CALENDAR_READ,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.MODIFY_CAL,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )

    step(1, "Read today's calendar")
    user('"what\'s on my calendar today?"')
    ai('call calendar.events_today(date="2026-05-20")')
    events = await app.tool_client.call_tool(
        s.id,
        "calendar.events_today",
        {"date": "2026-05-20"},
    )
    assert events.decision is Decision.ALLOW
    policy_outcome(events)
    tool(f"calendar.events_today → {len(events.output['events'])} events")

    step(2, "Pull related inbox thread for the vendor call")
    user('"check what the vendor sent ahead of the call"')
    ai('call inbox.read(id="vendor-note")')
    inbox_out = await app.tool_client.call_tool(s.id, "inbox.read", {"id": "vendor-note"})
    assert inbox_out.decision is Decision.ALLOW
    policy_outcome(inbox_out)
    tool("inbox.read → ok; session now UNTRUSTED_EXTERNAL-tainted.")

    step(3, "Read prior decisions doc from disk")
    ai(f'call fs.read(path="{prior}")')
    prior_out = await app.tool_client.call_tool(s.id, "fs.read", {"path": str(prior)})
    assert prior_out.decision is Decision.ALLOW
    policy_outcome(prior_out)
    tool(f"fs.read → ok ({len(prior_out.output['text'])} chars).")

    step(4, "Compose the agenda markdown (fs.create)")
    ai(f'call fs.create(path="{agenda}", content="# Agenda 2026-05-20 …")')
    created = await app.tool_client.call_tool(
        s.id,
        "fs.create",
        {
            "path": str(agenda),
            "content": (
                "# Agenda 2026-05-20\n\n"
                "## Roadmap sync (10:00)\n"
                "- Q3 priorities\n\n"
                "## Vendor call (14:00)\n"
                "- Bring Q3 numbers (per vendor note)\n"
                "- Reference prior decision: postpone switch to Q4\n"
            ),
        },
    )
    assert created.decision is Decision.ALLOW
    assert created.output["ok"]
    policy_outcome(created)
    tool(f"fs.create → wrote {created.output['bytes_written']} bytes")

    step(5, "Revise the agenda (fs.modify) — REQUIRE_APPROVAL by design")
    note(
        "fs.modify on an existing file is reversible-with-friction/"
        "human; even with allows_destructive on the cap, the "
        "reversibility gate forces an explicit operator approval."
    )
    ai(f'call fs.modify(path="{agenda}", content="…revised…")')
    revised = await app.tool_client.call_tool(
        s.id,
        "fs.modify",
        {"path": str(agenda), "content": "# Agenda 2026-05-20 (rev 2)\n…\n"},
    )
    policy_outcome(revised)
    tool("(deferred — operator would approve via the queue)")

    step(6, "Add a note to the calendar event (calendar.update_event)")
    note(
        "MODIFY_CAL with allows_destructive on the cap bypasses the "
        "destructive-op gate. The tool is reversible-with-friction/"
        "human so the reversibility gate would normally fire — "
        "envelope dial / operator-curated path can adjust."
    )
    ai(f'call calendar.update_event(id={str(event_b.id)[:8]}…, notes="see agenda")')
    update = await app.tool_client.call_tool(
        s.id,
        "calendar.update_event",
        {"id": str(event_b.id), "notes": f"See agenda: {agenda}"},
    )
    policy_outcome(update)
    if update.decision is Decision.ALLOW:
        tool("calendar.update_event → ok")
    else:
        tool("(deferred — operator approves via queue)")

    step(7, "Re-scope: spawn fresh session for the outbound bundle")
    note(
        "The prep session is now UNTRUSTED_EXTERNAL-tainted from the "
        "inbox.read. Sending notifications from it would Brewer-Nash "
        "deny. The operator explicitly re-scopes — a fresh session "
        "has no inherited taint and can dispatch the notifications "
        "as a single bundle."
    )
    fresh = await make_session(
        app,
        axis_a_categories=(("work", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.SEND_EMAIL,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
            },
        ),
    )
    # Add a financial taint to the fresh session so the bundle's
    # email sends route through REQUIRE_APPROVAL (financial-meets-
    # email Brewer-Nash). That's what makes the bundle interesting:
    # several gates to review at once.
    await app.graph.add_tags(
        fresh.id,
        LabelState(a=frozenset({CategoryTag("financial", Tier.RESTRICTED)})),
    )

    src = """
to_a = call("email.send", to="alice@example.com", subject="Today's agenda", body="See agenda.")
to_b = call("email.send", to="bob@example.com",   subject="Today's agenda", body="See agenda.")
to_c = call("email.send", to="carol@example.com", subject="Today's agenda", body="See agenda.")
"""
    initial_scope = {
        "_taint": LabeledValue(
            raw=None,
            label_state=LabelState(a=frozenset({CategoryTag("financial", Tier.RESTRICTED)})),
        ),
    }
    user("dry_run_for_bundle(notifications_source, …)")
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial_scope)
    policy(
        "pending",
        rule=f"{len(impact.gates)} gates collected",
        rationale=f"bundle={impact.bundle_id} · program_hash={impact.program_hash[:12]}…",
    )
    if impact.has_blocking_deny:
        tool("(bundle contains blocking deny; would need override)")
        return

    user("approve_all()")
    approved = impact.approve_all()
    states = sorted({g.state.value for g in approved.gates})
    policy("approved", rationale=f"gate states = {states}")

    await execute_with_approved_bundle(
        src,
        approved,
        session_id=fresh.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
        audit=app.audit,
    )
    n_sent = len(app.email_outbox.all())
    audit("Bundle executed: 3 notifications dispatched in one decision.")
    tool(f"email.send x 3 dispatched; outbox now has {n_sent} item(s).")
    assert n_sent >= 3
