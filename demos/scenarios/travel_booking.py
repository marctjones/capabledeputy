"""Travel booking — bundle vs. one-at-a-time, on a realistic workflow.

The classic personal-assistant trip-planning workflow: book a flight,
hotel, car, and conference registration. Each is a high-stakes
social-commitment purchase. Two paths shown:

  Path A — one-at-a-time. Each `purchase.queue` requires its own
           approval. Four purchases ⇒ four prompts. Operator fatigue.

  Path B — bundle. The dry-run collects all four as gates; the
           operator reviews the whole trip as one impact tree; one
           approve_all() and the executor runs all four. The audit
           log records a single bundle decision plus the four pre-
           applied dispatches.

The structural property: bundle execution is source-hash pinned. If
the agent (or anyone) tries to swap the source between preview and
execution, the executor refuses (program_hash mismatch).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.programmatic import (
    LabeledValue,
    dry_run_for_bundle,
    execute_with_approved_bundle,
)
from demos.scenarios._helpers import (
    ai,
    audit,
    demo_header,
    make_app,
    note,
    policy,
    policy_outcome,
    step,
    tool,
    user,
)


@pytest.mark.asyncio
async def test_travel_booking_demo(tmp_path: Any) -> None:
    demo_header(
        "Travel Booking — one-at-a-time vs. bundle",
        blurb=(
            "Four purchases for one trip. Path A: four separate prompts. "
            "Path B: one bundle review, four executions, program-hash "
            "pinned so the source can't be swapped between preview and "
            "execution."
        ),
        models=(
            "approval bundle program_hash pin",
            "FR-019 social-commitment",
        ),
        patterns=("dry-run + approve_all + execute",),
    )

    app = make_app(tmp_path)
    await app.startup()
    s = await app.graph.new(intent="plan trip")
    # Generous max_amount so the bundle path doesn't trip on individual
    # caps — that's a separate FR. The cap shape mirrors what an operator
    # would scope ('purchases up to $5000 per item').
    caps = frozenset(
        {Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=5000)},
    )
    # The trip planning session has a financial-confidential label
    # because it touched the operator's card details (in a real flow
    # the wallet read would add this); we model that here to make the
    # bundle demonstration apples-to-apples with bulk_approval_grouped.
    # R4b.4: add label via label_state
    financial_tag = CategoryTag("financial", Tier.RESTRICTED)
    label_state = LabelState(a=frozenset({financial_tag}))
    app.graph._sessions[s.id] = replace(s, capability_set=caps, label_state=label_state)

    step(1, "Path A — one-at-a-time. Each purchase trips REQUIRE_APPROVAL.")
    user('"book my trip in pieces"')
    for vendor, item, amount in (
        ("united", "Flight SFO-NYC", 450),
        ("hilton", "Hotel 3 nights", 800),
    ):
        ai(f"call purchase.queue(vendor={vendor!r}, item={item!r}, amount={amount})")
        out = await app.tool_client.call_tool(
            s.id,
            "purchase.queue",
            {"vendor": vendor, "item": item, "amount": amount},
        )
        policy_outcome(
            out,
            rationale=(
                "Financial + EGRESS_PURCHASE: Brewer-Nash routes via "
                "REQUIRE_APPROVAL. One prompt per purchase."
            ),
        )
    note("With 2 purchases done, the operator already has 2 pending prompts.")

    step(2, "Path B — bundle. Dry-run collects all FOUR gates.")
    user('"plan the whole trip and show me everything to approve"')
    ai("dry_run_for_bundle(trip_source, …)")
    src = """
flight = call("purchase.queue", vendor="united", item="Flight SFO-NYC", amount=450)
hotel  = call("purchase.queue", vendor="hilton", item="Hotel 3 nights", amount=800)
car    = call("purchase.queue", vendor="hertz",  item="Compact 3 days", amount=180)
conf   = call("purchase.queue", vendor="confco", item="Reg + workshop", amount=1200)
"""
    initial_scope = {
        "_taint": LabeledValue(
            raw=None,
            label_state=label_state,
        ),
    }
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial_scope)
    assert len(impact.gates) == 4
    assert impact.is_approvable
    policy(
        "pending",
        rule=f"{len(impact.gates)} gates collected",
        rationale=f"bundle={impact.bundle_id} · program_hash={impact.program_hash[:12]}…",
    )

    step(3, "Operator reviews the impact tree and approves the whole trip")
    user("approve_all()")
    approved = impact.approve_all()
    states = sorted({g.state.value for g in approved.gates})
    policy("approved", rationale=f"gate states = {states}")
    assert states == ["approved"]

    step(4, "Execute, program-hash pinned")
    note(
        "If the source had changed between preview and execution (e.g., "
        "agent attempted to swap the vendor), the runner would refuse "
        "with BundleMismatchError. That's the structural guarantee."
    )
    ai("execute_with_approved_bundle(trip_source, approved, …)")
    await execute_with_approved_bundle(
        src,
        approved,
        session_id=s.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
        audit=app.audit,
    )
    queued = app.purchase_queue.all()
    tool(
        f"4 purchase.queue dispatches executed; {len(queued)} queued in "
        "total (including Path A's 2)."
    )
    audit(
        "approval.approved emitted for each pre-applied gate with the bundle id as decision_scope."
    )

    # Mandatory assertions for the structural claim.
    assert len(queued) >= 4  # at least the 4 from the bundle landed
    # Verify Path A's outcomes were REQUIRE_APPROVAL not auto-allow.
    out_again = await app.tool_client.call_tool(
        s.id,
        "purchase.queue",
        {"vendor": "amex", "item": "extra ticket", "amount": 200},
    )
    assert out_again.decision is Decision.REQUIRE_APPROVAL
