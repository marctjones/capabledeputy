"""Executable probes for security-model / flow-pattern alignment.

These tests intentionally mix passing invariants with xfailed probes for
current design gaps. They are audit evidence: passing tests show where the
implementation matches the intended model, while strict xfails capture places
where the implementation is not yet aligned.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from capabledeputy.app import App
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse
from capabledeputy.mode.dispatcher import ExecutionMode, visible_tools
from capabledeputy.patterns.reference_handle import ReferenceHandleStore, ResolvedLabels
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.context import PolicyContext
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.substrate.sandbox_actuator import SandboxResult
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult

_HEALTH_LABELS = LabelState(
    a=frozenset(
        {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")},
    ),
)


async def _grant(app: App, session_id, *kinds: CapabilityKind) -> None:
    for kind in kinds:
        await app.graph.grant_capability(session_id, Capability(kind=kind, pattern="*"))


@pytest.mark.asyncio
async def test_turn_level_memory_read_taints_session_and_blocks_egress(tmp_path: Path) -> None:
    """Pattern 1 works through the real native tools.

    Reading labeled memory adds the source label to the session. A later email
    send is denied by the health/egress invariant, even though the session
    holds both READ_FS and SEND_EMAIL capabilities.
    """
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    app.memory.write("rx", "Patient medication: lisinopril 10mg daily.", _HEALTH_LABELS)

    session = await app.graph.new()
    await _grant(app, session.id, CapabilityKind.READ_FS, CapabilityKind.SEND_EMAIL)

    read = await app.tool_client.call_tool(session.id, "memory.read", {"key": "rx"})
    stored_rx = app.memory.read("rx")
    assert stored_rx is not None
    assert read.decision == Decision.ALLOW
    assert read.output == {"found": True, "value": stored_rx.value}

    after_read = app.graph.get(session.id)
    assert any(tag.category == "health" for tag in after_read.label_state.a)

    send = await app.tool_client.call_tool(
        session.id,
        "email.send",
        {"to": "caregiver@example.com", "subject": "rx", "body": "lisinopril 10mg"},
    )
    assert send.decision == Decision.DENY
    assert send.rule == "health-meets-egress"
    assert app.email_outbox.all() == []


@pytest.mark.asyncio
async def test_quarantined_extract_error_does_not_expose_rejected_output(
    tmp_path: Path,
) -> None:
    """Pattern 2 should not leak raw or echoed confidential data on failure."""
    quarantined = FakeLLMClient(
        [
            LLMResponse(
                content="not JSON: patient BP=120/80, medication=lisinopril",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=quarantined,
    )
    await app.startup()
    app.memory.write("rx", "Patient BP=120/80. Medication: lisinopril.", _HEALTH_LABELS)

    session = await app.graph.new()
    await _grant(app, session.id, CapabilityKind.READ_FS)

    outcome = await app.tool_client.call_tool(
        session.id,
        "quarantined.extract",
        {"key": "rx", "schema": "DoseSummary"},
    )

    assert outcome.decision == Decision.ALLOW
    serialized = json.dumps(outcome.output, sort_keys=True)
    assert "BP=120/80" not in serialized
    assert "lisinopril" not in serialized


@pytest.mark.asyncio
async def test_quarantined_extract_restricted_source_requires_reference_or_sealed_mode(
    tmp_path: Path,
) -> None:
    """Restricted sources should not be declassified through Pattern 2 alone."""
    quarantined = FakeLLMClient(
        [
            LLMResponse(
                content=json.dumps(
                    {"medication_name": "lisinopril", "dosage_mg": 10, "frequency": "daily"},
                ),
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        quarantined_llm=quarantined,
    )
    await app.startup()
    restricted = LabelState(
        a=frozenset(
            {CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared")},
        ),
    )
    app.memory.write("rx", "Patient prescription: lisinopril 10mg daily.", restricted)

    session = await app.graph.new()
    await _grant(app, session.id, CapabilityKind.READ_FS)

    outcome = await app.tool_client.call_tool(
        session.id,
        "quarantined.extract",
        {"key": "rx", "schema": "DoseSummary"},
    )

    assert outcome.decision != Decision.ALLOW
    assert outcome.output is None or "data" not in outcome.output


def test_visible_tools_aligns_with_dispatch_capability_matching() -> None:
    """The tool surface should match the policy engine's capability semantics."""

    async def _handler(_args: dict, _context: ToolContext) -> ToolResult:
        return ToolResult(output={})

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="drive.read",
            description="read drive",
            capability_kind=CapabilityKind.DRIVE_READ,
            handler=_handler,
            target_arg="path",
            operations=(Operation(EffectClass.FETCH, subtype="drive.read"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
        ),
    )

    from capabledeputy.session.model import Session

    session = Session.new(
        capability_set=frozenset({Capability(kind=CapabilityKind.READ_FS, pattern="*")}),
    )
    assert Capability(kind=CapabilityKind.READ_FS, pattern="*").matches(
        CapabilityKind.DRIVE_READ,
        "doc",
    )
    visible_names = [t.name for t in visible_tools(registry, session, ExecutionMode.TURN_LEVEL)]
    assert "drive.read" in visible_names


@pytest.mark.asyncio
async def test_sandbox_bound_handle_labels_propagate_to_session(tmp_path: Path) -> None:
    """Pattern 5 is containment, not declassification.

    A planner-visible handle can be bound into sandbox stdin, but the handle's
    source labels must taint the session when outputs cross the boundary.
    """
    actuator = MagicMock()
    actuator.create_region.return_value = "region-1"
    actuator.execute.return_value = SandboxResult(
        region_id="region-1",
        exit_code=0,
        output_digest="digest",
    )
    actuator.discard_region.return_value = None
    store = ReferenceHandleStore()
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        policy_context=PolicyContext(sandbox_actuator=actuator, handle_store=store),
    )
    await app.startup()
    session = await app.graph.new()
    await _grant(app, session.id, CapabilityKind.EXECUTE_SANDBOX)
    handle = store.issue(
        session.id,
        "Patient medication: lisinopril 10mg daily.",
        ResolvedLabels(axis_a=("health:restricted",)),
    )

    outcome = await app.tool_client.call_tool(
        session.id,
        "sandbox.run",
        {"spec_id": "scratch", "argv": ["cat"], "stdin": str(handle.id)},
    )

    assert outcome.decision == Decision.ALLOW
    assert actuator.execute.call_args.kwargs["stdin_bytes"] == (
        b"Patient medication: lisinopril 10mg daily."
    )
    after = app.graph.get(session.id)
    assert CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared") in (
        after.label_state.a
    )
