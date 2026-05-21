"""Integration tests for declassifier chokepoint wire-in.

Verifies that operator-registered DeclassifyingTransformers actually
fire when a tool returns, transform the value the agent sees, and
reduce per-result label propagation — without lowering session
label_set monotonicity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.substrate.declassifiers_builtin import (
    RegexRedactor,
    SchemaProjector,
)
from capabledeputy.tools.client import LabeledToolClient, PolicyContext
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


def _make_tool_returning(value, inherent_labels=frozenset()):
    """Build a synthetic tool that returns a fixed value with given inherent labels."""

    async def _handler(args, _ctx: ToolContext) -> ToolResult:
        return ToolResult(output=value, additional_labels=inherent_labels)

    return ToolDefinition(
        name="test.return",
        description="return a fixed value",
        capability_kind=CapabilityKind.READ_FS,
        handler=_handler,
        parameters_schema={"type": "object", "properties": {}},
        target_arg=None,
        inherent_labels=inherent_labels,
    )


@pytest.mark.asyncio
async def test_declassifier_runs_and_transforms_value(tmp_path: Path) -> None:
    """A registered RegexRedactor redacts PII in the tool output."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    tool = _make_tool_returning("Customer SSN: 123-45-6789")
    registry = ToolRegistry()
    registry.register(tool)

    policy_ctx = PolicyContext(
        declassifiers=(RegexRedactor(),),
    )
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=policy_ctx,
    )

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )

    outcome = await client.call_tool(s.id, "test.return", {})
    assert outcome.decision == Decision.ALLOW
    # The agent sees the redacted output, not the original
    assert outcome.output is not None
    assert "123-45-6789" not in str(outcome.output)
    assert "[REDACTED]-SSN" in str(outcome.output)

    # An audit event was emitted with the structural proof
    events = await writer.read_all()
    declassifier_events = [e for e in events if e.event_type == EventType.DECLASSIFIER_APPLIED]
    assert len(declassifier_events) == 1
    assert declassifier_events[0].payload.get("structural_proof_kind") == "regex-redacted"


@pytest.mark.asyncio
async def test_declassifier_reduces_label_propagation(tmp_path: Path) -> None:
    """When a declassifier lowers axis_b to 'trusted', untrusted labels
    drop from the per-result propagation."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    # Tool returns a dict; SchemaProjector keeps only allowed keys and
    # lowers axis_b to 'trusted', signalling that we should drop the
    # untrusted.external label from propagation.
    tool = _make_tool_returning(
        {"summary": "fact", "noise": "drop me"},
        inherent_labels=frozenset({Label.UNTRUSTED_EXTERNAL}),
    )
    registry = ToolRegistry()
    registry.register(tool)

    policy_ctx = PolicyContext(
        declassifiers=(SchemaProjector(allowed_keys=("summary",)),),
    )
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=policy_ctx,
    )

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )

    outcome = await client.call_tool(s.id, "test.return", {})
    assert outcome.decision == Decision.ALLOW
    # The untrusted.external label was dropped from this result's propagation
    assert Label.UNTRUSTED_EXTERNAL not in outcome.labels_added


@pytest.mark.asyncio
async def test_declassifier_chain_runs_in_order(tmp_path: Path) -> None:
    """Two declassifiers in order: each emits its own audit event."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    # Input is a string with PII; first redactor handles it.
    # SchemaProjector skips strings, so only the redactor's event fires.
    tool = _make_tool_returning("SSN 123-45-6789 here")
    registry = ToolRegistry()
    registry.register(tool)

    policy_ctx = PolicyContext(
        declassifiers=(RegexRedactor(), SchemaProjector(allowed_keys=("a",))),
    )
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=policy_ctx,
    )

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )

    outcome = await client.call_tool(s.id, "test.return", {})
    assert outcome.decision == Decision.ALLOW

    events = await writer.read_all()
    declassifier_events = [e for e in events if e.event_type == EventType.DECLASSIFIER_APPLIED]
    # Only the redactor fired (projector skips strings)
    assert len(declassifier_events) == 1
    assert declassifier_events[0].payload.get("structural_proof_kind") == "regex-redacted"


@pytest.mark.asyncio
async def test_no_declassifiers_passes_value_unchanged(tmp_path: Path) -> None:
    """With no declassifiers registered, the value flows through unchanged."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    tool = _make_tool_returning("PII: 123-45-6789 unchanged")
    registry = ToolRegistry()
    registry.register(tool)

    policy_ctx = PolicyContext()  # no declassifiers
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=policy_ctx,
    )

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )

    outcome = await client.call_tool(s.id, "test.return", {})
    assert outcome.decision == Decision.ALLOW
    # PII is in the output unchanged
    assert "123-45-6789" in str(outcome.output)
    # No DECLASSIFIER_APPLIED events
    events = await writer.read_all()
    declassifier_events = [e for e in events if e.event_type == EventType.DECLASSIFIER_APPLIED]
    assert len(declassifier_events) == 0


@pytest.mark.asyncio
async def test_buggy_declassifier_does_not_crash_chokepoint(tmp_path: Path) -> None:
    """A declassifier that throws is captured + audited; the chokepoint
    continues with the original value."""
    writer = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=writer)

    class _BrokenDeclassifier:
        name = "BrokenDeclassifier"

        def declassify(self, **kwargs):
            raise RuntimeError("intentional bug")

    tool = _make_tool_returning("untouched")
    registry = ToolRegistry()
    registry.register(tool)

    policy_ctx = PolicyContext(declassifiers=(_BrokenDeclassifier(),))
    client = LabeledToolClient(
        registry,
        graph,
        writer,
        policy_context=policy_ctx,
    )

    s = await graph.new()
    await graph.grant_capability(
        s.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )

    outcome = await client.call_tool(s.id, "test.return", {})
    # Tool dispatch succeeded; value passed through unchanged
    assert outcome.decision == Decision.ALLOW
    assert outcome.output == "untouched"

    # Audit captured the error
    events = await writer.read_all()
    declassifier_events = [e for e in events if e.event_type == EventType.DECLASSIFIER_APPLIED]
    assert len(declassifier_events) == 1
    assert "intentional bug" in declassifier_events[0].payload.get("error", "")
