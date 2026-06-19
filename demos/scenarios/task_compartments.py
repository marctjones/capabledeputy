"""Task compartments — Brewer-Nash compartmentalization on personal data.

Workflow: a personal assistant juggles to-do items AND occasionally
peeks at a personal finance summary. Reading the finance summary
tags the session CONFIDENTIAL_FINANCIAL. Attempting to email a combined
"my week" summary after that read fires the Brewer-Nash
financial-meets-email rule and refuses.

This is the canonical "the assistant accidentally combined two
compartments and tried to leak across them" demo. The structural
guarantee: combining compartments doesn't itself trigger anything —
the egress attempt does.
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from demos.scenarios._helpers import (
    ai,
    audit,
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
async def test_task_compartments_demo(tmp_path: Any) -> None:
    demo_header(
        "Task Compartments — Brewer-Nash on personal categories",
        blurb=(
            "An assistant juggles personal tasks AND a glance at a "
            "finance summary. When it tries to email a combined "
            "'my week' note, the financial-meets-email rule refuses. "
            "Compartment combining is allowed; egress across them isn't."
        ),
        models=("Brewer-Nash financial-meets-email", "label accumulation across reads"),
        patterns=("category mixing → egress refusal",),
    )

    ctx = PolicyContext()
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()

    # Pre-load a personal finance summary in the memory store with the
    # appropriate label, so memory.read propagates CONFIDENTIAL_FINANCIAL
    # onto the session.
    financial_tag = CategoryTag("financial", Tier.REGULATED)
    app.memory.write(
        "checking-balance",
        "$3,420.16 as of 2026-05-20",
        LabelState(a=frozenset({financial_tag})),
    )

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

    step(1, "Add three personal tasks")
    user('"add: buy groceries, renew passport, call dentist"')
    for title in ("buy groceries", "renew passport", "call dentist"):
        ai(f'call tasks.add(title="{title}")')
        out = await app.tool_client.call_tool(s.id, "tasks.add", {"title": title})
        assert out.decision is Decision.ALLOW
    tool("tasks.add x 3 → all ok; session tagged CONFIDENTIAL_PERSONAL.")

    step(2, "List the tasks")
    user('"what\'s on my todo?"')
    ai("call tasks.list()")
    listed = await app.tool_client.call_tool(s.id, "tasks.list", {})
    assert listed.decision is Decision.ALLOW
    policy_outcome(listed)
    tool(f"tasks.list → {len(listed.output['tasks'])} items.")

    step(3, "Peek at the checking-account balance")
    user('"what\'s my checking balance?"')
    ai('call memory.read(key="checking-balance")')
    bal = await app.tool_client.call_tool(s.id, "memory.read", {"key": "checking-balance"})
    assert bal.decision is Decision.ALLOW
    policy_outcome(bal)
    tool("memory.read → ok; session now ALSO tagged CONFIDENTIAL_FINANCIAL.")

    s_after = app.graph.get(s.id)
    # R4b.4: label_set → label_state with .a (CategoryTag) and .b (ProvenanceTag)
    a_cats = sorted(tag.category for tag in s_after.label_state.a)
    audit(f"session.label_state.a categories: {a_cats}")

    step(4, "Try to email 'my week' summary combining the two")
    note(
        "Combining personal + financial on the session is fine. The "
        "rule fires on the EGRESS attempt, not the combination itself."
    )
    ai('call email.send(to="me@example.com", body="…tasks + balance…")')
    out = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {
            "to": "me@example.com",
            "subject": "My week",
            "body": "Tasks pending; balance noted.",
        },
    )
    assert out.decision is Decision.DENY
    policy_outcome(
        out,
        rationale=(
            "Brewer-Nash financial-meets-email: any session that touched "
            "financial data cannot egress via email. The fix is to "
            "summarize-and-forget through Pattern ② or to send from a "
            "fresh session that never read the balance."
        ),
    )
    tool("(skipped)")
