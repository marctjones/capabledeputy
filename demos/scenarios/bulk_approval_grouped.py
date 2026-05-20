"""Bulk approval — one prompt for many gated steps.

A workflow of N gated steps becomes ONE WorkflowImpact for review.
`approve_all()` flips every gate to APPROVED. `execute_with_approved_bundle`
re-runs the program (source-hash pinned) and pre-applies the approved
gates so each step dispatches as if individually approved.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.programmatic import (
    LabeledValue,
    dry_run_for_bundle,
    execute_with_approved_bundle,
)
from demos.scenarios._helpers import (
    ai,
    demo_header,
    make_app,
    note,
    policy,
    step,
    tool,
    user,
)


@pytest.mark.asyncio
async def test_bulk_approval_demo(tmp_path: Any) -> None:
    demo_header(
        "Bulk Approval — one prompt, many gates",
        blurb=(
            "Five purchases. Without batching, five prompts. With the "
            "bundle path, ONE review — approve as a unit, execute byte-"
            "identically to what was previewed."
        ),
        models=(
            "approval-bundle program_hash pin",
            "source-pinned re-execution",
        ),
        patterns=("dry-run + approve_all + execute",),
    )

    app = make_app(tmp_path)
    await app.startup()
    s = await app.graph.new(intent="bulk purchase test")
    caps = frozenset(
        {Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=10_000)},
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)
    await app.graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_FINANCIAL}))

    src = """
a = call("purchase.queue", vendor="vendor-a", item="GPU",     amount=8000)
b = call("purchase.queue", vendor="vendor-b", item="RAM",     amount=600)
c = call("purchase.queue", vendor="vendor-c", item="Cables",  amount=80)
d = call("purchase.queue", vendor="vendor-d", item="UPS",     amount=400)
e = call("purchase.queue", vendor="vendor-e", item="Monitor", amount=1200)
"""
    initial_scope = {
        "_taint": LabeledValue(
            raw=None,
            labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        ),
    }

    step(1, "Dry-run the workflow; collect all approval gates")
    ai("dry_run_for_bundle(workflow_source, initial_scope=…)")
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial_scope)
    assert len(impact.gates) == 5
    assert impact.is_approvable
    policy(
        "pending",
        rule=f"{len(impact.gates)} gates",
        rationale=(
            f"is_approvable={impact.is_approvable}, has_blocking_deny={impact.has_blocking_deny}."
        ),
    )

    step(2, "Operator approves the bundle once")
    user("review the impact tree → approve_all()")
    approved = impact.approve_all()
    states = sorted({g.state.value for g in approved.gates})
    assert states == ["approved"]
    policy("approved", rationale=f"every gate flipped: states={states}")

    step(3, "Re-execute, source-hash pinned")
    note(
        "If the source changed between preview and execution the runner "
        "would refuse — program_hash mismatch."
    )
    ai("execute_with_approved_bundle(...)")
    await execute_with_approved_bundle(
        src,
        approved,
        session_id=s.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
        audit=app.audit,
    )
    n = len(app.purchase_queue.all())
    tool(f"5 purchase.queue dispatches; {n} purchases queued. Zero prompts.")
    assert n == 5
