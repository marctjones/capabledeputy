"""Approval bundles — minimum-approvals workflow.

The bundle collector dry-runs a programmatic workflow with deferred
approval gates. The user reviews one impact tree showing every step,
approves the bundle once, then the workflow executes for real with
all gates pre-applied.

Verified properties:
  - A workflow with N approval gates surfaces as ONE bundle, not N.
  - DENY decisions can't be approved away (security floor).
  - Source changes between preview and execution are caught (the
    bundle's program_hash mismatches and BundleMismatchError fires).
  - Pre-approved gates emit `approval.approved` audit events at
    execution time so the audit trail still holds end-to-end.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.approval.bundle import GateState, render_impact_tree
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.programmatic import (
    BundleMismatchError,
    dry_run_for_bundle,
    execute_with_approved_bundle,
)
from capabledeputy.programmatic.value import LabeledValue


async def test_dry_run_collects_multiple_approval_gates_into_one_bundle(
    tmp_path: Path,
) -> None:
    """Three purchases in a financial-tainted session each fire
    `financial-meets-purchase` (REQUIRE_APPROVAL). The bundle has
    three gates; the user makes ONE decision."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()

    src = """
gym = call("purchase.queue", vendor="planet-fitness", item="monthly", amount=30)
streaming = call("purchase.queue", vendor="netflix", item="monthly", amount=20)
groceries = call("purchase.queue", vendor="amazon-fresh", item="weekly", amount=180)
"""
    initial_scope = {
        # Simulates the session having read financial data before
        # entering programmatic mode.
        "_session_taint": LabeledValue(
            raw=None,
            labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        ),
    }

    impact = await dry_run_for_bundle(
        src,
        app.registry,
        initial_scope=initial_scope,
    )

    assert impact.parse_error is None
    assert impact.runtime_error is None
    assert len(impact.steps) == 3
    # All three are REQUIRE_APPROVAL because the session carries
    # confidential.financial and each call asks for QUEUE_PURCHASE
    # (egress.purchase) — fires `financial-meets-purchase`.
    assert all(s.decision == "require_approval" for s in impact.steps)
    assert len(impact.gates) == 3
    assert all(g.state == GateState.PENDING for g in impact.gates)
    assert impact.is_approvable


async def test_blocking_deny_makes_bundle_not_approvable(tmp_path: Path) -> None:
    """If any step would unconditionally DENY, the bundle is non-
    approvable — the user can't approve away a rule they explicitly
    set to DENY."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()

    src = 'sent = call("email.send", to="alice@example.com", subject="x", body="x")\n'
    initial_scope = {
        "_taint": LabeledValue(raw=None, labels=frozenset({Label.CONFIDENTIAL_HEALTH})),
    }
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial_scope)
    assert impact.has_blocking_deny is True
    assert impact.is_approvable is False


async def test_approve_all_then_execute(tmp_path: Path) -> None:
    """Round-trip: dry-run collects gates → user approves bundle →
    real execution runs each pre-approved gate. The outbox gets
    exactly the steps from the bundle."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new(intent="bundle test")
    caps = frozenset(
        {
            Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=10_000),
        },
    )
    app.graph._sessions[s.id] = replace(s, capability_set=caps)
    # Simulate the session has been tainted with financial data.
    await app.graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_FINANCIAL}))

    src = """
a = call("purchase.queue", vendor="vendor-a", item="x", amount=10)
b = call("purchase.queue", vendor="vendor-b", item="y", amount=20)
"""
    initial_scope = {
        "_taint": LabeledValue(
            raw=None,
            labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        ),
    }
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial_scope)
    assert len(impact.gates) == 2
    approved = impact.approve_all()
    assert all(g.state == GateState.APPROVED for g in approved.gates)

    await execute_with_approved_bundle(
        src,
        approved,
        session_id=s.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
        audit=app.audit,
    )

    # Both purchases queued for real.
    assert len(app.purchase_queue.all()) == 2

    # Audit log has approval.approved events for each pre-applied gate.
    events = await app.audit.read_all()
    bundled_approvals = [
        e for e in events
        if e.event_type.value == "approval.approved"
        and e.payload.get("decision_scope", {}).get("bundle_id") is not None
    ]
    assert len(bundled_approvals) == 2


async def test_program_hash_mismatch_aborts(tmp_path: Path) -> None:
    """Editing the source between preview and execution is caught
    by the program_hash check."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=1_000)
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    await app.graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_FINANCIAL}))

    src_v1 = 'a = call("purchase.queue", vendor="benign", item="x", amount=10)\n'
    src_v2 = 'a = call("purchase.queue", vendor="EVIL", item="x", amount=10)\n'

    impact = await dry_run_for_bundle(
        src_v1,
        app.registry,
        initial_scope={
            "_taint": LabeledValue(raw=None, labels=frozenset({Label.CONFIDENTIAL_FINANCIAL})),
        },
    )
    approved = impact.approve_all()

    with pytest.raises(BundleMismatchError, match="hash"):
        await execute_with_approved_bundle(
            src_v2,  # different source!
            approved,
            session_id=s.id,
            tool_client=app.tool_client,
            graph=app.graph,
            registry=app.registry,
        )
    # Critically: nothing executed.
    assert len(app.purchase_queue.all()) == 0


async def test_bundle_with_blocking_deny_refuses_execution(tmp_path: Path) -> None:
    """Even if the user marks a non-negotiable DENY gate as approved
    (which approve_all does NOT do — DENY stays DENY), execution
    refuses to run."""
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new()

    src = 'call("email.send", to="x@example.com", subject="s", body="b")\n'
    initial = {
        "_taint": LabeledValue(raw=None, labels=frozenset({Label.CONFIDENTIAL_HEALTH})),
    }
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial)
    assert impact.has_blocking_deny is True
    approved = impact.approve_all()  # WOULD_DENY stays unchanged.

    with pytest.raises(BundleMismatchError, match="DENY"):
        await execute_with_approved_bundle(
            src,
            approved,
            session_id=s.id,
            tool_client=app.tool_client,
            graph=app.graph,
            registry=app.registry,
        )


async def test_dry_run_reports_unknown_tool_as_would_deny(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()

    impact = await dry_run_for_bundle('call("nonexistent.tool", x=1)\n', app.registry)
    assert impact.has_blocking_deny
    assert impact.gates[0].rule == "tool-not-found"


async def test_render_impact_tree_human_readable(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()

    src = 'call("purchase.queue", vendor="v", item="i", amount=10)\n'
    initial = {
        "_taint": LabeledValue(raw=None, labels=frozenset({Label.CONFIDENTIAL_FINANCIAL})),
    }
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial)
    rendered = render_impact_tree(impact)
    assert "Bundle " in rendered
    assert "purchase.queue" in rendered
    assert "1 approval gate(s) pending" in rendered


async def test_partial_approval_executes_only_approved_gates(tmp_path: Path) -> None:
    """Approving a subset (one gate, not the other) means only the
    approved gates run as the user intended; unapproved gates are
    dispatched normally — they'll hit the policy engine and either
    REQUIRE_APPROVAL again or DENY. This keeps approve_all from being
    the only granularity."""
    from dataclasses import replace as _replace

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=1_000)
    app.graph._sessions[s.id] = _replace(s, capability_set=frozenset({cap}))
    await app.graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_FINANCIAL}))

    src = """
a = call("purchase.queue", vendor="approved-vendor", item="x", amount=10)
b = call("purchase.queue", vendor="rejected-vendor", item="y", amount=20)
"""
    initial = {
        "_taint": LabeledValue(
            raw=None,
            labels=frozenset({Label.CONFIDENTIAL_FINANCIAL}),
        ),
    }
    impact = await dry_run_for_bundle(src, app.registry, initial_scope=initial)
    assert len(impact.gates) == 2

    # Hand-approve only the first gate; leave the second PENDING.
    approved_first = _replace(impact.gates[0], state=GateState.APPROVED)
    partial = _replace(impact, gates=[approved_first, impact.gates[1]])

    # Execution: gate 1 is pre-approved → runs; gate 2 is still
    # PENDING → falls through to the live policy engine which returns
    # REQUIRE_APPROVAL; the program halts and the policy reason is
    # captured in result.error.
    result = await execute_with_approved_bundle(
        src,
        partial,
        session_id=s.id,
        tool_client=app.tool_client,
        graph=app.graph,
        registry=app.registry,
    )
    assert result.error is not None
    assert "financial-meets-purchase" in result.error
    # First purchase ran; second did not.
    queued = app.purchase_queue.all()
    assert len(queued) == 1
    assert queued[0].vendor == "approved-vendor"
