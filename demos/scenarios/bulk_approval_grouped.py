"""Bulk approval — one approval for many gated steps.

Story:
  An agent queues five purchases as part of a workflow. Each purchase
  individually would require approval (the session is tainted with
  CONFIDENTIAL_FINANCIAL). Without batching, the user faces five
  prompts in sequence.

  CapableDeputy's `dry_run_for_bundle` collects every approval gate
  the workflow would trip into ONE WorkflowImpact. The user reviews
  the entire impact tree, approves once with `approve_all()`, and
  `execute_with_approved_bundle` runs each step as if pre-approved.

  This pattern keeps the operator in informed control without making
  them click through a wall of prompts.

Security models exercised:
  - Approval bundle (program_hash-protected one-shot review)
  - Source-pinned re-execution (the source must be byte-identical
    between preview and execution)
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
)
from capabledeputy.policy.labels import Label
from capabledeputy.programmatic import (
    LabeledValue,
    dry_run_for_bundle,
    execute_with_approved_bundle,
)
from demos.scenarios._helpers import make_app, narrate


@pytest.mark.asyncio
async def test_bulk_approval_demo(tmp_path: Any) -> None:
    narrate(
        "Bulk Approval — one prompt, many gates",
        """
        Five purchases. Without batching, five prompts. With the
        bundle path, ONE prompt — reviewed as a single impact tree,
        approved as a unit, executed byte-identically to what was
        previewed.
        """,
    )

    app = make_app(tmp_path)
    await app.startup()
    s = await app.graph.new(intent="bulk purchase test")
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=10_000),
        },
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)
    # The session is already tainted (e.g., the agent read financial
    # records as part of building the order). Each purchase would
    # therefore route through REQUIRE_APPROVAL.
    await app.graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_FINANCIAL}))

    src = """
a = call("purchase.queue", vendor="vendor-a", item="GPU",      amount=8000)
b = call("purchase.queue", vendor="vendor-b", item="RAM",      amount=600)
c = call("purchase.queue", vendor="vendor-c", item="Cables",   amount=80)
d = call("purchase.queue", vendor="vendor-d", item="UPS",      amount=400)
e = call("purchase.queue", vendor="vendor-e", item="Monitor",  amount=1200)
"""
    initial_scope = {
        "_taint": LabeledValue(
            raw=None,
            labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        ),
    }

    narrate("Step 1", "Dry-run the workflow. Collect all approval gates.")
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial_scope)
    narrate(
        "  → bundle",
        f"{len(impact.steps)} steps. {len(impact.gates)} pending gate(s).\n"
        f"    is_approvable = {impact.is_approvable}, has_blocking_deny = "
        f"{impact.has_blocking_deny}.",
    )
    assert len(impact.gates) == 5
    assert impact.is_approvable

    narrate("Step 2", "User approves the bundle once — every gate flips to APPROVED.")
    approved = impact.approve_all()
    states = sorted({g.state.value for g in approved.gates})
    narrate("  → states", f"gate states = {states}")
    assert states == ["approved"]

    narrate("Step 3", "Re-run with the approved bundle. Source-hash pinned.")
    await execute_with_approved_bundle(
        src,
        approved,
        session_id=s.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
        audit=app.audit,
    )

    n_queued = len(app.purchase_queue.all())
    narrate("  → result", f"{n_queued} purchases queued. Zero individual prompts.")
    assert n_queued == 5
