"""Per-session tool token aliasing.

Aliases are deterministic per (session_id, tool_name) so traces replay
identically. Different sessions produce different aliases for the same
tool, so a token leaked from one session is meaningless in another.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest

from capabledeputy.agent.loop import build_tool_descriptions, run_turn
from capabledeputy.app import App
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.tools.aliasing import (
    alias_for,
    build_alias_map,
    build_reverse_map,
)
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)


def test_alias_is_deterministic_per_session() -> None:
    sid = UUID("00000000-0000-0000-0000-000000000001")
    a1 = alias_for(sid, "memory.read")
    a2 = alias_for(sid, "memory.read")
    assert a1 == a2
    assert a1.startswith("t_")


def test_alias_differs_across_sessions() -> None:
    s1 = uuid4()
    s2 = uuid4()
    assert alias_for(s1, "memory.read") != alias_for(s2, "memory.read")


def test_alias_differs_across_tools() -> None:
    sid = uuid4()
    assert alias_for(sid, "memory.read") != alias_for(sid, "memory.write")


def test_build_alias_and_reverse_round_trip() -> None:
    sid = uuid4()
    names = ["memory.read", "memory.write", "purchase.queue"]
    forward = build_alias_map(sid, names)
    reverse = build_reverse_map(sid, names)
    for name, token in forward.items():
        assert reverse[token] == name


async def _noop(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _registry_with(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for name in names:
        reg.register(
            ToolDefinition(
                name=name,
                description=f"tool {name}",
                capability_kind=CapabilityKind.READ_FS,
                handler=_noop,
                operations=(Operation(EffectClass.FETCH),),
                risk_ids=("RISK-INDIRECT-INJECTION",),
            ),
        )
    return reg


def test_build_tool_descriptions_uses_canonical_names_when_aliasing_off() -> None:
    """Without aliasing, the LLM-visible tool list uses canonical names."""
    from capabledeputy.session.model import Session

    reg = _registry_with("memory.read", "memory.write")
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = Session.new(capability_set=frozenset({cap}))
    desc = build_tool_descriptions(reg, ExecutionMode.TURN_LEVEL, s)
    names = {d.name for d in desc}
    assert "memory.read" in names


def test_build_tool_descriptions_uses_aliases_when_aliasing_on() -> None:
    from capabledeputy.session.model import Session

    reg = _registry_with("memory.read", "memory.write")
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    s = Session.new(capability_set=frozenset({cap}), tool_aliasing=True)
    desc = build_tool_descriptions(reg, ExecutionMode.TURN_LEVEL, s)
    names = {d.name for d in desc}
    # Aliasing on + capability filters keep only memory.read visible
    # (memory.write needs WRITE_FS). The visible name is the alias.
    assert all(name.startswith("t_") for name in names)
    assert alias_for(s.id, "memory.read") in names


@pytest.mark.parametrize("aliasing", [False, True])
async def test_agent_loop_dispatches_correctly_with_or_without_aliasing(
    tmp_path: str,
    aliasing: bool,
) -> None:
    """End-to-end: agent loop reverse-maps the alias before dispatch.

    The FakeLLM produces a tool_call referencing the alias when
    aliasing is on, and the canonical name when it's off. Either way
    LabeledToolClient.call_tool dispatches to the real handler.
    """
    from pathlib import Path

    app = App(
        state_db_path=Path(str(tmp_path)) / "state.db",
        audit_log_path=Path(str(tmp_path)) / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    s = await app.graph.new(intent="aliasing test", tool_aliasing=aliasing)
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    from capabledeputy.policy.labels import LabelState

    app.memory.write("k", "v", LabelState())

    tool_name = alias_for(s.id, "memory.read") if aliasing else "memory.read"

    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="reading",
                tool_calls=(ToolCall(id="r1", name=tool_name, args={"key": "k"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="done", finish_reason=FinishReason.STOP),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="read k",
        llm=app.llm_client,
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )
    assert len(result.tool_outcomes) == 1
    assert result.tool_outcomes[0].decision.value == "allow"


async def test_aliasing_blocks_unknown_token_dispatches(tmp_path: str) -> None:
    """A token not in the reverse map (e.g. an LLM hallucination)
    falls through to ToolNotFoundError-as-deny so the call cannot
    succeed against an unintended tool.
    """
    from pathlib import Path

    app = App(
        state_db_path=Path(str(tmp_path)) / "state.db",
        audit_log_path=Path(str(tmp_path)) / "audit.jsonl",
        llm_client=FakeLLMClient([]),
    )
    await app.startup()
    s = await app.graph.new(tool_aliasing=True)
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    app.llm_client = FakeLLMClient(
        [
            LLMResponse(
                content="trying",
                tool_calls=(
                    ToolCall(
                        id="r1",
                        name="t_deadbeef",
                        args={"key": "k"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="halted", finish_reason=FinishReason.STOP),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="x",
        llm=app.llm_client,
        tool_client=app.tool_client,
        registry=app.registry,
        graph=app.graph,
        audit=app.audit,
    )
    assert len(result.tool_outcomes) == 1
    assert result.tool_outcomes[0].decision.value == "deny"
    assert "t_deadbeef" in (result.tool_outcomes[0].reason or "")


async def test_session_persistence_round_trips_tool_aliasing(tmp_path: str) -> None:
    """The flag survives daemon restart (SQLite + schema v2)."""
    from pathlib import Path

    db = Path(str(tmp_path)) / "state.db"
    audit = Path(str(tmp_path)) / "audit.jsonl"
    app = App(state_db_path=db, audit_log_path=audit)
    await app.startup()
    s = await app.graph.new(tool_aliasing=True, prefer_programmatic=True)

    # Re-load
    app2 = App(state_db_path=db, audit_log_path=audit)
    await app2.startup()
    loaded = app2.graph.get(s.id)
    assert loaded.tool_aliasing is True
    assert loaded.prefer_programmatic is True
