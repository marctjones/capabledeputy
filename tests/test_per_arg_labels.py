"""Tests for FR-027/039 per-arg payload labels.

A tool declares `arg_inherent_labels: dict[arg_name, frozenset[Label]]`;
each declared arg's labels fire ONLY when the value at that arg is
non-empty in the call. Lets tool authors say "the body field carries
confidential.personal" without painting every email-send call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
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
        arg_inherent_labels=arg_labels,
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
    )


# ---------- extract_arg_inherent_labels (unit) ----------


def test_no_arg_labels_yields_empty() -> None:
    tool = _make_tool_with_arg_labels({})
    assert tool.extract_arg_inherent_labels({"body": "hi"}) == frozenset()


def test_populated_arg_triggers_labels() -> None:
    tool = _make_tool_with_arg_labels(
        {"body": frozenset({Label.CONFIDENTIAL_PERSONAL})},
    )
    result = tool.extract_arg_inherent_labels({"body": "personal info"})
    assert Label.CONFIDENTIAL_PERSONAL in result


def test_empty_string_does_not_trigger() -> None:
    """An empty body should NOT fire the label."""
    tool = _make_tool_with_arg_labels(
        {"body": frozenset({Label.CONFIDENTIAL_PERSONAL})},
    )
    result = tool.extract_arg_inherent_labels({"body": ""})
    assert Label.CONFIDENTIAL_PERSONAL not in result


def test_missing_arg_does_not_trigger() -> None:
    tool = _make_tool_with_arg_labels(
        {"attachments": frozenset({Label.UNTRUSTED_USER_INPUT})},
    )
    result = tool.extract_arg_inherent_labels({"to": "x@example.com"})
    assert Label.UNTRUSTED_USER_INPUT not in result


def test_empty_list_does_not_trigger() -> None:
    tool = _make_tool_with_arg_labels(
        {"attachments": frozenset({Label.UNTRUSTED_USER_INPUT})},
    )
    result = tool.extract_arg_inherent_labels({"attachments": []})
    assert Label.UNTRUSTED_USER_INPUT not in result


def test_multiple_arg_labels_compose() -> None:
    tool = _make_tool_with_arg_labels(
        {
            "body": frozenset({Label.CONFIDENTIAL_PERSONAL}),
            "attachments": frozenset({Label.UNTRUSTED_USER_INPUT}),
        },
    )
    result = tool.extract_arg_inherent_labels(
        {"body": "info", "attachments": ["a.pdf"]},
    )
    assert Label.CONFIDENTIAL_PERSONAL in result
    assert Label.UNTRUSTED_USER_INPUT in result


def test_only_populated_args_trigger() -> None:
    """If body is populated but attachments isn't, only body's labels fire."""
    tool = _make_tool_with_arg_labels(
        {
            "body": frozenset({Label.CONFIDENTIAL_PERSONAL}),
            "attachments": frozenset({Label.UNTRUSTED_USER_INPUT}),
        },
    )
    result = tool.extract_arg_inherent_labels(
        {"body": "info", "attachments": []},
    )
    assert Label.CONFIDENTIAL_PERSONAL in result
    assert Label.UNTRUSTED_USER_INPUT not in result


# ---------- chokepoint integration ----------


@pytest.mark.asyncio
async def test_per_arg_labels_propagate_to_session(tmp_path: Path) -> None:
    """When a tool with per-arg labels is called and the arg is populated,
    the session's label_set grows with those labels."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    tool = _make_tool_with_arg_labels(
        {"body": frozenset({Label.CONFIDENTIAL_PERSONAL})},
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
    # The CONFIDENTIAL_PERSONAL label was added per-arg
    assert Label.CONFIDENTIAL_PERSONAL in outcome.labels_added

    # Session label_set now includes it
    s_after = graph.get(s.id)
    assert Label.CONFIDENTIAL_PERSONAL in s_after.label_set


@pytest.mark.asyncio
async def test_empty_arg_does_not_taint_session(tmp_path: Path) -> None:
    """Tool with declared per-arg labels but the arg is unpopulated:
    those labels do NOT fire."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    tool = _make_tool_with_arg_labels(
        {"body": frozenset({Label.CONFIDENTIAL_PERSONAL})},
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
    assert Label.CONFIDENTIAL_PERSONAL not in outcome.labels_added
