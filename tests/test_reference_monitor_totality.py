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
