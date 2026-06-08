"""Tests for FR-027/039 per-arg payload labels.

A tool declares `arg_inherent_tags: dict[arg_name, LabelState]`;
each declared arg's tags fire ONLY when the value at that arg is
non-empty in the call. Lets tool authors say "the body field carries
confidential.personal" without painting every email-send call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


def _make_tool_with_arg_labels(arg_labels):
    """Build a tool with per-arg label declarations."""

    async def _handler(args, _ctx: ToolContext) -> ToolResult:
        return ToolResult(output={"called": True})

    return ToolDefinition(
        name="test.with_arg_labels",
        description="exercise per-arg payload labels",
        capability_kind=CapabilityKind.READ_FS,
        handler=_handler,
        parameters_schema={
            "type": "object",
            "properties": {
                "body": {"type": "string"},
                "attachments": {"type": "array"},
                "to": {"type": "string"},
            },
        },
        target_arg="to",
        arg_inherent_tags=arg_labels,
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
    )


# ---------- extract_arg_inherent_tags (unit) ----------


def test_no_arg_labels_yields_empty() -> None:
    tool = _make_tool_with_arg_labels({})
    result = tool.extract_arg_inherent_tags({"body": "hi"})
    assert len(result.a) == 0 and len(result.b) == 0


def test_populated_arg_triggers_labels() -> None:
    personal_tags = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    tool = _make_tool_with_arg_labels(
        {"body": personal_tags},
    )
    result = tool.extract_arg_inherent_tags({"body": "personal info"})
    assert any(c.category == "personal" for c in result.a)


def test_empty_string_does_not_trigger() -> None:
    """An empty body should NOT fire the label."""
    personal_tags = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    tool = _make_tool_with_arg_labels(
        {"body": personal_tags},
    )
    result = tool.extract_arg_inherent_tags({"body": ""})
    assert not any(c.category == "personal" for c in result.a)


def test_missing_arg_does_not_trigger() -> None:
    untrusted_tags = LabelState(b=frozenset())  # empty for now
    tool = _make_tool_with_arg_labels(
        {"attachments": untrusted_tags},
    )
    result = tool.extract_arg_inherent_tags({"to": "x@example.com"})
    assert len(result.b) == 0


def test_empty_list_does_not_trigger() -> None:
    untrusted_tags = LabelState(b=frozenset())  # empty for now
    tool = _make_tool_with_arg_labels(
        {"attachments": untrusted_tags},
    )
    result = tool.extract_arg_inherent_tags({"attachments": []})
    assert len(result.b) == 0


def test_multiple_arg_labels_compose() -> None:
    personal_tags = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    untrusted_tags = LabelState(b=frozenset())  # empty for now
    tool = _make_tool_with_arg_labels(
        {
            "body": personal_tags,
            "attachments": untrusted_tags,
        },
    )
    result = tool.extract_arg_inherent_tags(
        {"body": "info", "attachments": ["a.pdf"]},
    )
    assert any(c.category == "personal" for c in result.a)


def test_only_populated_args_trigger() -> None:
    """If body is populated but attachments isn't, only body's labels fire."""
    personal_tags = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    untrusted_tags = LabelState(b=frozenset())  # empty for now
    tool = _make_tool_with_arg_labels(
        {
            "body": personal_tags,
            "attachments": untrusted_tags,
        },
    )
    result = tool.extract_arg_inherent_tags(
        {"body": "info", "attachments": []},
    )
    assert any(c.category == "personal" for c in result.a)
    assert len(result.b) == 0


# ---------- chokepoint integration ----------


@pytest.mark.asyncio
async def test_per_arg_labels_propagate_to_session(tmp_path: Path) -> None:
    """When a tool with per-arg labels is called and the arg is populated,
    the session's tags grow with those labels."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    personal_tags = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    tool = _make_tool_with_arg_labels(
        {"body": personal_tags},
    )
    registry = ToolRegistry()
    registry.register(tool)
    client = LabeledToolClient(registry, graph, writer)

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )

    outcome = await client.call_tool(
        s.id,
        "test.with_arg_labels",
        {"to": "x@example.com", "body": "personal info"},
    )
    assert outcome.decision == Decision.ALLOW
    # The personal tag was added per-arg
    assert any(c.category == "personal" for c in outcome.tags_added.a)

    # Session tags now includes it
    s_after = graph.get(s.id)
    assert any(c.category == "personal" for c in s_after.label_state.a)


@pytest.mark.asyncio
async def test_empty_arg_does_not_taint_session(tmp_path: Path) -> None:
    """Tool with declared per-arg labels but the arg is unpopulated:
    those labels do NOT fire."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    personal_tags = LabelState(
        a=frozenset(
            {CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared")}
        )
    )
    tool = _make_tool_with_arg_labels(
        {"body": personal_tags},
    )
    registry = ToolRegistry()
    registry.register(tool)
    client = LabeledToolClient(registry, graph, writer)

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )

    # Call with empty body
    outcome = await client.call_tool(
        s.id,
        "test.with_arg_labels",
        {"to": "x@example.com", "body": ""},
    )
    assert outcome.decision == Decision.ALLOW
    # No per-arg label fired
    assert len(outcome.tags_added.a) == 0
