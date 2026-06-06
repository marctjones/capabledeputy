"""The runtime registers approvals at the policy chokepoint.

Previously the REPL client called approval.submit after observing a
REQUIRE_APPROVAL outcome — so capdep send / MCP never queued anything.
Now LabeledToolClient registers it in the queue itself and the
outcome carries the approval_id. These tests prove that, plus dedup.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalStatus
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import AxisA, AxisB, CategoryTag, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.tiers import Tier


@pytest.fixture
async def app(tmp_path: Path) -> App:
    a = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await a.startup()
    return a


def _financial_axis_a() -> AxisA:
    return AxisA(
        categories=(
            CategoryTag("financial", Tier.REGULATED, assignment_provenance="source-declared"),
        )
    )


def _external_untrusted_axis_b() -> AxisB:
    return AxisB(entries=(ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED),))


async def test_require_approval_auto_registers_in_queue(app: App) -> None:
    """No client involved — just the tool client. The approval must
    already be in the queue and the outcome must carry its id."""
    s = await app.graph.new()
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=10_000,
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({cap}),
        axis_a=_financial_axis_a(),
    )

    assert len(app.approval_queue.list()) == 0

    outcome = await app.tool_client.call_tool(
        s.id,
        "purchase.queue",
        {"vendor": "amazon", "item": "x", "amount": 9},
    )

    assert outcome.decision.value == "require_approval"
    assert outcome.approval_id is not None
    queued = app.approval_queue.list(status=ApprovalStatus.PENDING)
    assert len(queued) == 1
    assert queued[0].id == outcome.approval_id
    assert queued[0].from_session == s.id
    assert queued[0].target == "amazon"


async def test_identical_repeat_call_dedups_to_same_approval(app: App) -> None:
    """The agent loop can re-attempt the same gated call within a
    turn. The runtime must not pile up duplicate pending requests."""
    s = await app.graph.new()
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=10_000,
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({cap}),
        axis_a=_financial_axis_a(),
    )

    args = {"vendor": "amazon", "item": "towels", "amount": 50}
    o1 = await app.tool_client.call_tool(s.id, "purchase.queue", args)
    o2 = await app.tool_client.call_tool(s.id, "purchase.queue", args)

    assert o1.approval_id == o2.approval_id
    assert len(app.approval_queue.list(status=ApprovalStatus.PENDING)) == 1


async def test_distinct_targets_get_distinct_approvals(app: App) -> None:
    s = await app.graph.new()
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=10_000,
    )
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({cap}),
        axis_a=_financial_axis_a(),
    )

    o1 = await app.tool_client.call_tool(
        s.id,
        "purchase.queue",
        {"vendor": "amazon", "item": "a", "amount": 1},
    )
    o2 = await app.tool_client.call_tool(
        s.id,
        "purchase.queue",
        {"vendor": "ebay", "item": "b", "amount": 2},
    )
    assert o1.approval_id != o2.approval_id
    assert len(app.approval_queue.list(status=ApprovalStatus.PENDING)) == 2


async def test_deny_outcome_registers_nothing(app: App) -> None:
    """untrusted-meets-egress is DENY, not REQUIRE_APPROVAL — no
    queue entry, no approval_id."""
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    app.graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({cap}),
        axis_b=_external_untrusted_axis_b(),
    )
    outcome = await app.tool_client.call_tool(
        s.id,
        "email.send",
        {"to": "x@y.com", "subject": "s", "body": "b"},
    )
    assert outcome.decision.value == "deny"
    assert outcome.approval_id is None
    assert len(app.approval_queue.list()) == 0


async def test_no_queue_wired_degrades_gracefully(tmp_path: Path) -> None:
    """Unit-test construction (no approval_queue) must still work —
    outcome carries the resolved submission but no id."""
    from capabledeputy.audit.writer import AuditWriter
    from capabledeputy.session.graph import SessionGraph
    from capabledeputy.tools.client import LabeledToolClient
    from capabledeputy.tools.native.purchase import PurchaseQueue, make_purchase_tools
    from capabledeputy.tools.registry import ToolRegistry

    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)
    registry = ToolRegistry()
    for t in make_purchase_tools(PurchaseQueue()):
        registry.register(t)
    client = LabeledToolClient(registry, graph, writer)  # no queue

    s = await graph.new()
    cap = Capability(
        kind=CapabilityKind.QUEUE_PURCHASE,
        pattern="*",
        max_amount=999,
    )
    graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({cap}),
        axis_a=_financial_axis_a(),
    )
    outcome = await client.call_tool(
        s.id,
        "purchase.queue",
        {"vendor": "amazon", "item": "x", "amount": 5},
    )
    assert outcome.decision.value == "require_approval"
    assert outcome.approval_submission is not None
    assert outcome.approval_id is None
