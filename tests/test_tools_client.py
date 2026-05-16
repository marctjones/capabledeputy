from pathlib import Path
from typing import Any

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
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
        additional_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
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
            "label_count": len(context.label_set),
        },
    )


async def _make_setup(
    writer: AuditWriter,
) -> tuple[ToolRegistry, SessionGraph, LabeledToolClient]:
    registry = ToolRegistry()
    graph = SessionGraph(audit=writer)
    client = LabeledToolClient(registry, graph, writer)
    return registry, graph, client


async def test_allow_dispatches_and_returns_output(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="fs.read",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_ok_handler,
            target_arg="path",
        ),
    )

    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    s = await graph.new()
    await graph._save(s)
    s = await graph.add_labels(s.id, frozenset())
    graph._sessions[s.id] = s.__class__(  # type: ignore[misc]
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
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


async def test_deny_when_no_matching_capability(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="email.send",
            description="t",
            capability_kind=CapabilityKind.SEND_EMAIL,
            handler=_ok_handler,
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
            target_arg="to",
        ),
    )
    cap = Capability(kind=CapabilityKind.SEND_EMAIL, pattern="*")
    s = await graph.new()
    s = await graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_HEALTH}))
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
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
            target_arg="url",
            inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
        ),
    )
    cap = Capability(kind=CapabilityKind.WEB_FETCH, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
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
    assert Label.UNTRUSTED_EXTERNAL in outcome.labels_added
    after = graph.get(s.id)
    assert Label.UNTRUSTED_EXTERNAL in after.label_set


async def test_handler_additional_labels_propagate(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="memory.read",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_labeling_handler,
            target_arg="key",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
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
    assert Label.CONFIDENTIAL_HEALTH in outcome.labels_added


async def test_handler_exception_returns_error(writer: AuditWriter) -> None:
    registry, graph, client = await _make_setup(writer)
    registry.register(
        ToolDefinition(
            name="boom",
            description="t",
            capability_kind=CapabilityKind.READ_FS,
            handler=_raising_handler,
            target_arg="x",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
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
            target_arg="path",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
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
            target_arg="url",
        ),
    )
    registry.register(
        ToolDefinition(
            name="notes.write",
            description="t",
            capability_kind=CapabilityKind.WRITE_FS,
            handler=_ok_handler,
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
            target_arg="x",
        ),
    )
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = await graph.new()
    s = await graph.add_labels(s.id, frozenset({Label.CONFIDENTIAL_PERSONAL}))
    graph._sessions[s.id] = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
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
