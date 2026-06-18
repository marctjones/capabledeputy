"""Optimistic-execution burn — FR-034 carve-out for reversible/system.

100 reversible/system writes proceed with zero prompts. Then a single
delete attempt — irreversible/external — refuses. The carve-out is
bright-line; the gate ratchets the moment the action leaves the
reversible/system corner.
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
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from demos.scenarios._helpers import (
    ai,
    demo_header,
    make_app,
    make_session,
    note,
    policy_outcome,
    step,
    tool,
)


@pytest.mark.asyncio
async def test_optimistic_burn_demo(tmp_path: Any) -> None:
    demo_header(
        "Optimistic Burn — reversible/system + non-egressing → AUTO",
        blurb=(
            "100 reminders, 100 ALLOWs, zero prompts. Then one delete "
            "attempt — the carve-out does not extend that far."
        ),
        models=("FR-034 optimistic-auto", "SC-013 carve-out boundary"),
        patterns=("non-egressing reversible/system",),
    )

    ctx = PolicyContext()
    app = make_app(tmp_path, policy_context=ctx)
    await app.startup()
    s = await make_session(
        app,
        axis_a_categories=(("work", Tier.SENSITIVE),),
        capabilities=frozenset(
            {
                Capability(
                    kind=CapabilityKind.CREATE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                ),
                Capability(
                    kind=CapabilityKind.DELETE_FS,
                    pattern="*",
                    origin=CapabilityOrigin.USER_APPROVED,
                    allows_destructive=True,
                ),
            },
        ),
    )

    step(1, "Burn: 100 x memory.create")
    ai("loop 100x: call memory.create(key=f'reminder-{i}', value=…)")
    rules_hit: list[str | None] = []
    for i in range(100):
        out = await app.tool_client.call_tool(
            s.id,
            "memory.create",
            {"key": f"reminder-{i}", "value": f"task {i}"},
        )
        assert out.decision is Decision.ALLOW
        rules_hit.append(out.rule)
    tool("memory.create → ok x 100 (no prompts, no rule fired)")
    note(f"Distinct rule values across the 100 calls: {sorted({r or '<none>' for r in rules_hit})}")

    step(2, "memory.delete — irreversible/external")
    ai('call memory.delete(key="reminder-0")')
    out_del = await app.tool_client.call_tool(s.id, "memory.delete", {"key": "reminder-0"})
    assert out_del.decision is Decision.DENY
    policy_outcome(
        out_del,
        rationale=(
            "Carve-out does not extend to irreversible. Reversibility gate refuses (FR-019)."
        ),
    )
    tool("(skipped)")
