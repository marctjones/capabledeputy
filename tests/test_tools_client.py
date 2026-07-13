from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.actions import Action
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
    Tier,
)
from capabledeputy.policy.pipeline import DecisionFrame, DecisionRequest
from capabledeputy.policy.relationships import RelationshipGroup, RelationshipGroups
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.substrate.decision_inspector_port import DecisionTighten
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.native.memory import LabeledMemoryStore, make_memory_tools
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


@pytest.fixture
def writer(tmp_path: Path) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


async def _ok_handler(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={"received": args})


async def _labeling_handler(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(
        output={"value": args.get("payload", "x")},
        additional_tags=LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )


async def _raising_handler(args: dict[str, Any], context: ToolContext) -> ToolResult:
    raise RuntimeError("boom")


async def _capture_context_handler(
    args: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    return ToolResult(
        output={
            "session_id": str(context.session_id),
            "label_count": len(context.label_state.a) + len(context.label_state.b),
        },
    )


async def _make_setup(
    writer: AuditWriter,
) -> tuple[ToolRegistry, SessionGraph, LabeledToolClient]:
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(registry, graph, writer)
    return registry, graph, client


class _AllowingPipeline:
    def __init__(self) -> None:
        self.requests: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> DecisionFrame:
        from capabledeputy.policy.engine import PolicyDecision

        self.requests.append(request)
        return DecisionFrame(
            request=request,
            decision=PolicyDecision(decision=Decision.ALLOW, rule="test-pipeline"),
            stages=("test",),
        )


class _WarnInspector:
    name = "warner"

    def inspect(self, *, action, session, proposed_outcome):
        return DecisionTighten(
            to=Decision.WARN,
            rule="heads-up",
            rationale="non-blocking warning",
        )


async def test_allow_dispatches_and_returns_output(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="fs.read",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="path",
        ),
    )

    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    s = await graph.new()
    await graph._save(s)
    s = await graph.add_tags(s.id, LabelState())
    graph._sessions[s.id] = s.__class__(  # type: ignore[misc]
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )

    outcome = await client.call_tool(s.id, "fs.read", {"path": "/home/marc/n.md"})
    assert outcome.decision == Decision.ALLOW
    assert outcome.output == {"received": {"path": "/home/marc/n.md"}}


async def test_warn_dispatches_and_emits_advisory_audit(writer: AuditWriter) -> None:
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(decision_inspectors=(_WarnInspector(),)),
    )
    registry.register(
        ToolDefinition(
            name="fs.read",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="path",
        ),
    )
    s = await graph.new()
    await graph.grant_capability(s.id, Capability(kind=CapabilityKind.READ_FS, pattern="*"))

    outcome = await client.call_tool(s.id, "fs.read", {"path": "/home/marc/n.md"})

    assert outcome.decision == Decision.WARN
    assert outcome.output == {"received": {"path": "/home/marc/n.md"}}
    events = await writer.read_all()
    event_types = [event.event_type for event in events]
    assert EventType.POLICY_WARNED in event_types
    assert event_types.index(EventType.POLICY_WARNED) < event_types.index(
        EventType.TOOL_DISPATCHED,
    )


async def test_dispatch_uses_injected_policy_pipeline(writer: AuditWriter) -> None:
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    pipeline = _AllowingPipeline()
    client = LabeledToolClient(registry, graph, writer, policy_pipeline=pipeline)
    registry.register(
        ToolDefinition(
            name="fs.read",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="path",
        ),
    )

    s = await graph.new()
    outcome = await client.call_tool(s.id, "fs.read", {"path": "/ungranted"})
    assert outcome.decision == Decision.ALLOW
    assert outcome.rule == "test-pipeline"
    assert outcome.output == {"received": {"path": "/ungranted"}}
    assert len(pipeline.requests) == 1
    assert pipeline.requests[0].action.target == "/ungranted"


async def test_deny_when_no_matching_capability(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="email.send",
            description="t",
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=_ok_handler,
            operations=(Operation(EffectClass.COMMUNICATE),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            surfaces_destination_id=True,
            target_arg="to",
        ),
    )
    s = await graph.new()

    outcome = await client.call_tool(s.id, "email.send", {"to": "a@b.com"})
    assert outcome.decision == Decision.DENY
    assert "no matching capability" in (outcome.reason or "")


async def test_deny_on_brewer_nash_conflict(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="email.send",
            description="t",
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=_ok_handler,
            operations=(Operation(EffectClass.COMMUNICATE),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            surfaces_destination_id=True,
            target_arg="to",
        ),
    )
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    s = await graph.new()
    s = await graph.add_tags(
        s.id,
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )

    outcome = await client.call_tool(s.id, "email.send", {"to": "a@b.com"})
    assert outcome.decision == Decision.DENY
    assert outcome.rule == "health-meets-egress"


async def test_inherent_labels_propagate_to_session(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="web.fetch",
            description="t",
            capability_kind=CapabilityKind.WEB_FETCH,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="url",
            inherent_tags=LabelState(
                b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})
            ),
        ),
    )
    cap = Capability(kind=CapabilityKind.WEB_FETCH, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )

    outcome = await client.call_tool(s.id, "web.fetch", {"url": "https://x.com"})
    assert outcome.decision == Decision.ALLOW
    assert ProvenanceLevel.EXTERNAL_UNTRUSTED in {t.level for t in outcome.tags_added.b}
    after = graph.get(s.id)
    assert ProvenanceLevel.EXTERNAL_UNTRUSTED in {t.level for t in after.label_state.b}


async def test_operation_required_floor_participates_in_decision(writer: AuditWriter) -> None:
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(),
    )
    registry.register(
        ToolDefinition(
            name="trusted.summarize",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_ok_handler,
            operations=(
                Operation(
                    EffectClass.FETCH,
                    required_floor=ProvenanceLevel.PRINCIPAL_DIRECT,
                ),
            ),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="path",
        ),
    )
    s = await graph.new()
    await graph.grant_capability(s.id, Capability(kind=CapabilityKind.READ_FS, pattern="*"))
    await graph.add_tags(
        s.id,
        LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
    )

    outcome = await client.call_tool(s.id, "trusted.summarize", {"path": "/tmp/x"})

    assert outcome.decision == Decision.DENY
    assert outcome.rule == "integrity-floor-refused"


async def test_action_axis_d_resolves_embedded_target_email(writer: AuditWriter) -> None:
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(
            relationship_groups=RelationshipGroups(
                groups={
                    "self": RelationshipGroup(
                        group_id="self",
                        member_principal_ids=frozenset({"me@example.com"}),
                    ),
                },
            ),
        ),
    )
    session = await graph.new()

    axis_d = client._resolve_action_axis_d(
        session.axis_d,
        action=Action(
            kind=CapabilityKind.CREATE_CAL,
            target="gcal://calendar/primary/events/attendees/me@example.com",
        ),
    )

    assert axis_d.relationship_group_ids == frozenset({"self"})


async def test_handler_additional_labels_propagate(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="memory.read",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_labeling_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="key",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )

    outcome = await client.call_tool(s.id, "memory.read", {"key": "x"})
    assert outcome.decision == Decision.ALLOW
    assert (
        CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")
        in outcome.tags_added.a
    )


async def test_raw_restricted_memory_read_denied_before_dispatch(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    memory = LabeledMemoryStore()
    for tool in make_memory_tools(memory):
        if tool.name == "memory.read":
            registry.register(tool)

    s = await graph.new()
    graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({Capability(kind=CapabilityKind.READ_FS, pattern="*")}),
    )
    memory.write(
        "secret",
        "raw restricted value",
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared")}
            ),
        ),
    )

    outcome = await client.call_tool(s.id, "memory.read", {"key": "secret"})

    assert outcome.decision == Decision.DENY
    assert outcome.output is None
    assert outcome.rule == "restricted-source-requires-reference-or-sealed"
    events = await writer.read_all()
    assert not [e for e in events if e.event_type == EventType.TOOL_RETURNED]


async def test_handler_exception_returns_error(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="boom",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_raising_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="x",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )

    outcome = await client.call_tool(s.id, "boom", {"x": "y"})
    assert outcome.decision == Decision.ALLOW
    assert outcome.error is not None
    assert "RuntimeError" in outcome.error


async def test_audit_events_are_emitted(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="fs.read",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="path",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )

    await client.call_tool(s.id, "fs.read", {"path": "/x"})
    events = await writer.read_all()
    types = [e.event_type for e in events]
    assert EventType.POLICY_DECIDED in types
    assert EventType.CAPABILITY_CHECKED in types
    assert EventType.TOOL_DISPATCHED in types
    assert EventType.TOOL_RETURNED in types


async def test_revoked_by_prior_use_blocks_second_dispatch(
    writer: AuditWriter,
) -> None:
    """End-to-end runtime check: dispatching web.fetch records the kind
    into the session's used_kinds, after which a notes.write whose
    capability declares revoked_by={WEB_FETCH} is denied with the
    capability-revoked-by-prior-use rule."""
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="web.fetch",
            description="t",
            capability_kind=CapabilityKind.WEB_FETCH,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="url",
        ),
    )
    registry.register(
        ToolDefinition(
            name="notes.write",
            description="t",
            capability_kind=CapabilityKind.WRITE_FS,
            handler=_ok_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="path",
        ),
    )

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.WEB_FETCH, pattern="*"),
    )
    await graph.grant_capability(
        s.id,
        Capability(
            kind=CapabilityKind.WRITE_FS,
            pattern="*",
            allows_destructive=True,
            revoked_by=frozenset({CapabilityKind.WEB_FETCH}),
        ),
    )

    first = await client.call_tool(
        s.id,
        "web.fetch",
        {"url": "https://example.com"},
    )
    assert first.decision == Decision.ALLOW

    assert CapabilityKind.WEB_FETCH in graph.get(s.id).used_kinds

    second = await client.call_tool(
        s.id,
        "notes.write",
        {"path": "/notes/x.md"},
    )
    assert second.decision == Decision.DENY
    assert second.rule == "capability-revoked-by-prior-use"
    assert "WEB_FETCH" in (second.reason or "")


async def test_context_carries_session_state(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="introspect",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_capture_context_handler,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            target_arg="x",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    s = await graph.add_tags(
        s.id,
        LabelState(
            a=frozenset(
                {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
    )
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_state=s.label_state,
        axis_d=s.axis_d,
        capability_set=frozenset({cap}),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )

    outcome = await client.call_tool(s.id, "introspect", {"x": "y"})
    assert outcome.output is not None
    assert outcome.output["session_id"] == str(s.id)
    assert outcome.output["label_count"] == 1
