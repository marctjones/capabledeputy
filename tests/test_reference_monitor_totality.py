from __future__ import annotations

from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.audit.events import EventType
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import LabelState
from capabledeputy.policy.rules import Decision


def test_native_tool_registry_entries_carry_policy_metadata(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )

    tools = app.registry.list()

    assert tools
    for tool in tools:
        assert tool.operations, f"{tool.name} must declare effect operations"
        assert tool.risk_ids, f"{tool.name} must cite at least one risk id"
        assert tool.capability_kind, f"{tool.name} must declare capability kind"


def test_every_outbound_native_tool_declares_egress_effect(tmp_path: Path) -> None:
    """#294 / #298 — audit: every registered outbound-capable tool must declare
    an egress-capable operation, so egress-ness is carried by the tool's own
    declaration rather than a hand-maintained kind list at the chokepoint. This
    makes the Rule-8 registration guarantee explicit at the app level and guards
    against a future tool (native, MCP-adapted, or skill) re-introducing the
    web.fetch drift (#293). App.startup() would already refuse to register a
    violator; this asserts the invariant holds for the full shipped surface."""
    from capabledeputy.policy.effect_class import effect_class_is_egress_capable
    from capabledeputy.tools.registry import _KIND_TO_EFFECT

    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    for tool in app.registry.list():
        kind_effect = _KIND_TO_EFFECT.get(str(tool.capability_kind))
        if kind_effect is None or not effect_class_is_egress_capable(kind_effect):
            continue  # not an outbound-capable kind
        declared = {op.effect_class for op in tool.operations}
        assert any(effect_class_is_egress_capable(e) for e in declared), (
            f"{tool.name}: outbound capability {tool.capability_kind} must declare "
            f"an egress-capable operation (declared: {sorted(str(e) for e in declared)})"
        )


@pytest.mark.asyncio
async def test_policy_decided_is_emitted_before_every_dispatched_tool(
    tmp_path: Path,
) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
    )
    await app.startup()
    session = await app.graph.new()
    await app.graph.grant_capability(
        session.id,
        Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    )
    app.memory.write("note", "hello", LabelState())

    outcome = await app.tool_client.call_tool(session.id, "memory.read", {"key": "note"})

    assert outcome.decision == Decision.ALLOW
    events = [
        event.event_type
        for event in await app.audit.read_all()
        if event.event_type in {EventType.POLICY_DECIDED, EventType.TOOL_DISPATCHED}
    ]
    assert events[:2] == [EventType.POLICY_DECIDED, EventType.TOOL_DISPATCHED]
