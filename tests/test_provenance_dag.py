from __future__ import annotations

from pathlib import Path
from typing import Any

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.queue import ApprovalQueue
from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.reference_handle import ReferenceHandleStore, ResolvedLabels
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    DelegationRequest,
)
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult


async def _ok_handler(args: dict[str, Any], _context: ToolContext) -> ToolResult:
    return ToolResult(output={"received": args})


async def test_tool_result_records_materialized_provenance(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(registry, graph, writer)
    registry.register(
        ToolDefinition(
            name="fs.read",
            description="read",
            capability_kind=CapabilityKind.READ_FS,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="path",
        ),
    )
    session = await graph.new()
    await graph.grant_capability(session.id, Capability(kind=CapabilityKind.READ_FS, pattern="*"))

    outcome = await client.call_tool(session.id, "fs.read", {"path": "/tmp/readme.md"})

    assert outcome.decision is Decision.ALLOW
    events = await writer.read_all()
    node_payloads = [e.payload for e in events if e.event_type is EventType.PROVENANCE_NODE]
    edge_payloads = [e.payload for e in events if e.event_type is EventType.PROVENANCE_EDGE]
    assert any(p["kind"] == "capability" for p in node_payloads)
    tool_nodes = [p for p in node_payloads if p["kind"] == "tool_result"]
    assert tool_nodes
    assert any(
        p["kind"] == "authorized" and p["to_node_id"] == tool_nodes[-1]["node_id"]
        for p in edge_payloads
    )


async def test_reference_handle_bind_records_input_edges(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    store = ReferenceHandleStore()
    policy_context = PolicyContext(handle_store=store)
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(registry, graph, writer, policy_context=policy_context)
    registry.register(
        ToolDefinition(
            name="fs.modify",
            description="write handle value",
            capability_kind=CapabilityKind.MODIFY_FS,
            handler=_ok_handler,
            operations=(Operation(EffectClass.MUTATE_LOCAL),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            target_arg="path",
            surfaces_destination_id=True,
            accepts_handles=True,
            handle_arg_names=("body",),
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["path", "body"],
            },
        ),
    )
    session = await graph.new()
    await graph.grant_capability(
        session.id,
        Capability(
            kind=CapabilityKind.MODIFY_FS,
            pattern="/tmp/out.txt",
            allows_destructive=True,
        ),
    )
    handle = store.issue(
        session.id,
        "secret body",
        ResolvedLabels(axis_a=("health:restricted",)),
    )

    outcome = await client.call_tool(
        session.id,
        "fs.modify",
        {"path": "/tmp/out.txt", "body": str(handle.id)},
    )

    assert outcome.decision is Decision.ALLOW
    events = await writer.read_all()
    edges = [e.payload for e in events if e.event_type is EventType.PROVENANCE_EDGE]
    assert any(p["kind"] == "bound" for p in edges)
    assert any(
        p["kind"] == "input" and p["from_node_id"] == f"reference_handle:{handle.id}" for p in edges
    )


async def test_delegation_records_authority_edge(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)
    parent = await graph.new()
    child = await graph.new(parent=parent.id)
    parent_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    await graph.grant_capability(parent.id, parent_cap)

    delegated = await graph.delegate(
        parent.id,
        child.id,
        DelegationRequest(kind=CapabilityKind.READ_FS),
        depth_limit=3,
    )

    assert isinstance(delegated, Capability)
    events = await writer.read_all()
    edges = [e.payload for e in events if e.event_type is EventType.PROVENANCE_EDGE]
    assert {
        "from_node_id": f"capability:{parent_cap.audit_id}",
        "to_node_id": f"capability:{delegated.audit_id}",
        "kind": "delegated",
    }.items() <= edges[-1].items()


async def test_approval_records_request_and_decision_edge(tmp_path: Path) -> None:
    writer = AuditWriter(tmp_path / "audit.jsonl")
    queue = ApprovalQueue(audit=writer)
    request = await queue.submit(
        from_session=None,
        action=ApprovalAction.SEND_EMAIL,
        payload="send",
        target="person@example.com",
        justification="operator review",
    )

    await queue.approve(request.id, decided_by="alice")

    events = await writer.read_all()
    nodes = [e.payload for e in events if e.event_type is EventType.PROVENANCE_NODE]
    edges = [e.payload for e in events if e.event_type is EventType.PROVENANCE_EDGE]
    assert any(p["kind"] == "approval_request" for p in nodes)
    assert any(p["kind"] == "approval_decision" for p in nodes)
    assert any(
        p["from_node_id"] == f"approval_request:{request.id}" and p["kind"] == "decided"
        for p in edges
    )
