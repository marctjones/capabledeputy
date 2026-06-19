from __future__ import annotations

from typing import Any
from uuid import uuid4

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.patterns.reference_handle import ReferenceHandleStore, ResolvedLabels
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult
from capabledeputy.tools.source_flow import (
    RESTRICTED_SOURCE_FLOW_RULE,
    ToolSourceFlow,
)


async def _unused_handler(_args: dict[str, Any], _context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _tool(**kwargs: Any) -> ToolDefinition:
    return ToolDefinition(
        name="demo.tool",
        description="demo",
        capability_kind=CapabilityKind.READ_FS,
        handler=_unused_handler,
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
        **kwargs,
    )


def test_extract_source_tags_combines_tool_lookup_and_reference_handles(
    tmp_path,
) -> None:
    session_id = uuid4()
    store = ReferenceHandleStore()
    handle = store.issue(
        session_id,
        "hidden",
        ResolvedLabels(axis_a=("health:restricted",)),
    )
    tool_source = LabelState(
        a=frozenset(
            {CategoryTag("financial", Tier.REGULATED, assignment_provenance="source-declared")},
        ),
    )
    flow = ToolSourceFlow(
        policy_context=PolicyContext(handle_store=store),
        audit=AuditWriter(tmp_path / "audit.jsonl"),
    )
    tags = flow.extract_source_tags(
        session_id=session_id,
        tool=_tool(
            accepts_handles=True,
            handle_arg_names=("body",),
            source_label_lookup=lambda _args: tool_source,
        ),
        args={"body": str(handle.id)},
    )

    assert {tag.category for tag in tags.a} == {"financial", "health"}
    assert any(tag.category == "health" and tag.tier is Tier.RESTRICTED for tag in tags.a)


def test_restricted_source_floor_blocks_pattern2_declassification(tmp_path) -> None:
    flow = ToolSourceFlow(
        policy_context=PolicyContext(),
        audit=AuditWriter(tmp_path / "audit.jsonl"),
    )
    decision = flow.restricted_source_floor_decision(
        tool=_tool(forbid_restricted_source=True, effect_class="data.extract"),
        source_tags=LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared")},
            ),
        ),
        base_decision=PolicyDecision(decision=Decision.ALLOW, rule="base"),
        labels_snapshot=LabelState(),
        axis_d_snapshot=None,
    )

    assert decision is not None
    assert decision.decision == Decision.DENY
    assert decision.rule == RESTRICTED_SOURCE_FLOW_RULE


async def test_bind_reference_handles_recurses_and_audits_nested_paths(tmp_path) -> None:
    session_id = uuid4()
    store = ReferenceHandleStore()
    handle = store.issue(
        session_id,
        "nested-secret",
        ResolvedLabels(axis_a=("health:restricted",)),
    )
    writer = AuditWriter(tmp_path / "audit.jsonl")
    flow = ToolSourceFlow(
        policy_context=PolicyContext(handle_store=store),
        audit=writer,
    )

    bound_args, bound_tags, bound_nodes = await flow.bind_reference_handles(
        session_id=session_id,
        tool=_tool(
            accepts_handles=True,
            handle_arg_names=("inputs",),
        ),
        tool_name="demo.tool",
        args={"inputs": {"stdin": [str(handle.id)]}},
    )

    assert bound_args == {"inputs": {"stdin": ["nested-secret"]}}
    assert CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared") in (
        bound_tags.a
    )
    assert bound_nodes == (f"reference_handle:{handle.id}",)
    events = await writer.read_all()
    bind = next(event for event in events if event.event_type == EventType.PATTERN3_HANDLE_BIND)
    assert bind.payload["arg_name"] == "inputs.stdin[0]"
