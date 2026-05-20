"""Optimistic-execution burn — FR-034 carve-out for reversible/system.

Story:
  A bot writes a hundred reminders to its local memory store. Each
  write is reversible (delete the key) and the agent of reversal is
  the system (no human action needed). None of the writes egress.

  Under FR-034 the engine carves these out: ALLOW with rule
  `optimistic-auto`, no approval prompt. This is the bright-line
  test for "where can the agent move fast without a human in the
  loop?" — the answer is: reversible/system + non-egressing.

  Counter-example: the same agent then tries a `memory.delete`,
  which is irreversible/external. The carve-out does NOT apply; the
  reversibility gate fires and refuses.

Security models exercised:
  - FR-034 / SC-013 optimistic execution boundary
  - Compose composition vs reversibility gate (gate trumps optimistic
    once reversibility leaves the reversible/system corner)
"""

from __future__ import annotations

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
from demos.scenarios._helpers import make_app, make_session, narrate


@pytest.mark.asyncio
async def test_optimistic_burn_demo(tmp_path: Any) -> None:
    narrate(
        "Optimistic Burn — reversible/system + non-egressing → AUTO",
        """
        Hundred reminders, hundred ALLOWs, zero prompts. Then one
        delete attempt — the carve-out doesn't extend that far.
        """,
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

    rules_hit: list[str | None] = []
    for i in range(100):
        out = await app.tool_client.call_tool(
            s.id,
            "memory.create",
            {"key": f"reminder-{i}", "value": f"task {i}"},
        )
        assert out.decision is Decision.ALLOW
        rules_hit.append(out.rule)

    narrate(
        "Bulk writes",
        f"100 x memory.create. All ALLOW. Distinct rules seen = "
        f"{sorted({r or '<none>' for r in rules_hit})}",
    )

    narrate("Delete attempt", "Now try memory.delete — irreversible/external.")
    out_del = await app.tool_client.call_tool(s.id, "memory.delete", {"key": "reminder-0"})
    narrate(
        "  → result",
        f"memory.delete → {out_del.decision.value} (rule={out_del.rule})\n"
        f"    reason: {out_del.reason}",
    )
    # The carve-out does NOT extend to irreversible. The reversibility
    # gate refuses.
    assert out_del.decision is Decision.DENY
