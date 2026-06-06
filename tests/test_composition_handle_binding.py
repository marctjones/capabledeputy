"""Composition Sub-phase A+B — handle binding wire-in (Demo #5).

End-to-end test that the dispatcher substitutes ReferenceHandle ids
for real values AFTER decide() approves, emits pattern3.handle_bind,
and never lets the planner-visible args contain the raw value.

Also pins the back-compat surface: a LabeledToolClient built without
a PolicyContext behaves identically to v0.7 (the foundation work
must not regress any existing caller).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.reference_handle import (
    ReferenceHandleStore,
    ResolvedLabels,
)
from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityKind,
    CapabilityOrigin,
)
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


@pytest.fixture
def writer(tmp_path: Any) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


async def _handle_consumer(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """A tool that consumes a handle-bound argument and returns it
    in `output.received_body`. The test asserts the handler sees the
    REAL value, not the UUID."""
    return ToolResult(output={"received_body": args.get("body", "<missing>")})


async def _legacy_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(output=dict(args))


def _handle_aware_tool() -> ToolDefinition:
    return ToolDefinition(
        name="api.post",
        description="post body to an external service",
        capability_kind=CapabilityKind.WEB_FETCH,
        handler=_handle_consumer,
        target_arg="url",
        accepts_handles=True,
        handle_arg_names=("body",),
        effect_class="data.api_post",
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
        parameters_schema={
            "type": "object",
            "properties": {"url": {"type": "string"}, "body": {"type": "string"}},
        },
    )


def _legacy_tool() -> ToolDefinition:
    return ToolDefinition(
        name="legacy.echo",
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
        description="echo args as-is",
        capability_kind=CapabilityKind.READ_FS,
        handler=_legacy_handler,
    )


async def _make_session_with_caps(graph: SessionGraph, caps: set[Capability]) -> Any:
    s = await graph.new()
    s = s.__class__(
        id=s.id,
        parent=s.parent,
        status=s.status,
        label_set=s.label_set,
        capability_set=frozenset(caps),
        history=s.history,
        declassification_log=s.declassification_log,
        created_at=s.created_at,
        updated_at=s.updated_at,
        owner=s.owner,
        intent=s.intent,
    )
    graph._sessions[s.id] = s
    return s


# --- back-compat ----------------------------------------------------


async def test_legacy_client_without_policy_context_works_unchanged(
    writer: AuditWriter,
) -> None:
    """A LabeledToolClient built without a PolicyContext must call
    decide() the v0.7 way and not attempt any handle binding."""
    registry = ToolRegistry()
    registry.register(_legacy_tool())
    graph = SessionGraph()
    s = await _make_session_with_caps(
        graph,
        {
            Capability(
                kind=CapabilityKind.READ_FS,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        },
    )
    client = LabeledToolClient(registry, graph, writer)  # no policy_context
    outcome = await client.call_tool(s.id, "legacy.echo", {"x": "y"})
    assert outcome.decision == Decision.ALLOW
    assert outcome.output == {"x": "y"}


# --- handle binding ------------------------------------------------


async def test_handle_substituted_for_real_value_after_decide(
    writer: AuditWriter,
) -> None:
    """The handler receives the BOUND value, not the UUID. This is the
    demo-#5 invariant: the planner held a token; the substrate
    substituted the real value at the boundary."""
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    s = await _make_session_with_caps(
        graph,
        {
            Capability(
                kind=CapabilityKind.WEB_FETCH,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        },
    )
    store = ReferenceHandleStore()
    handle = store.issue(
        s.id,
        "the-real-secret-body",
        ResolvedLabels(axis_a=("personal",), axis_b=("principal-direct",)),
    )
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(handle_store=store),
    )
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://api.example.com/post", "body": str(handle.id)},
    )
    assert outcome.decision == Decision.ALLOW
    assert outcome.output == {"received_body": "the-real-secret-body"}


async def test_handle_bind_audit_event_emitted(writer: AuditWriter) -> None:
    """T104 audit invariant: every successful bind emits a
    pattern3.handle_bind event with the destination canonical id."""
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    s = await _make_session_with_caps(
        graph,
        {
            Capability(
                kind=CapabilityKind.WEB_FETCH,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        },
    )
    store = ReferenceHandleStore()
    handle = store.issue(
        s.id,
        "secret",
        ResolvedLabels(axis_a=("personal",), axis_b=("principal-direct",)),
    )
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(handle_store=store),
    )
    await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://api.example.com/post", "body": str(handle.id)},
    )
    events = await writer.read_all()
    types = [e.event_type for e in events]
    assert EventType.PATTERN3_HANDLE_BIND in types
    bind_event = next(e for e in events if e.event_type == EventType.PATTERN3_HANDLE_BIND)
    assert bind_event.payload["tool"] == "api.post"
    assert bind_event.payload["arg_name"] == "body"
    assert bind_event.payload["handle_id"] == str(handle.id)


async def test_non_handle_arg_passed_through_unchanged(writer: AuditWriter) -> None:
    """Args not in handle_arg_names are never touched."""
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    s = await _make_session_with_caps(
        graph,
        {
            Capability(
                kind=CapabilityKind.WEB_FETCH,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        },
    )
    store = ReferenceHandleStore()
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(handle_store=store),
    )
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {
            "url": "https://api.example.com/post",
            "body": "not-a-uuid-just-a-literal-string",
        },
    )
    # The body is not UUID-shaped, so it's passed through.
    assert outcome.output == {"received_body": "not-a-uuid-just-a-literal-string"}


async def test_forged_handle_id_does_not_substitute_and_does_not_emit(
    writer: AuditWriter,
) -> None:
    """A UUID-shaped string that's not in the store ⇒ bind refuses
    (caught silently — substitution skipped), handler sees raw, NO
    pattern3.handle_bind event emitted."""
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    s = await _make_session_with_caps(
        graph,
        {
            Capability(
                kind=CapabilityKind.WEB_FETCH,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        },
    )
    store = ReferenceHandleStore()
    forged = str(uuid4())
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(handle_store=store),
    )
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://api.example.com/post", "body": forged},
    )
    # Handler sees the raw forged token (substitution refused).
    assert outcome.output == {"received_body": forged}
    events = await writer.read_all()
    types = [e.event_type for e in events]
    assert EventType.PATTERN3_HANDLE_BIND not in types


async def test_handle_store_absent_does_not_substitute(writer: AuditWriter) -> None:
    """If PolicyContext is provided but handle_store is None, the
    dispatcher does not attempt binding — the v2 path activates but
    handle substitution stays off."""
    registry = ToolRegistry()
    registry.register(_handle_aware_tool())
    graph = SessionGraph()
    s = await _make_session_with_caps(
        graph,
        {
            Capability(
                kind=CapabilityKind.WEB_FETCH,
                pattern="*",
                origin=CapabilityOrigin.USER_APPROVED,
            ),
        },
    )
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=PolicyContext(handle_store=None),
    )
    a_uuid = str(uuid4())
    outcome = await client.call_tool(
        s.id,
        "api.post",
        {"url": "https://api.example.com/post", "body": a_uuid},
    )
    # No store ⇒ no substitution; handler sees the raw token.
    assert outcome.output == {"received_body": a_uuid}
