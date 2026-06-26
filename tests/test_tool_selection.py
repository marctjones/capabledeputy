"""Tests for deterministic tool-surface curation."""

from __future__ import annotations

from typing import Any

from capabledeputy.agent.tool_families import ToolFamiliesConfig, ToolFamily, load_tool_families
from capabledeputy.agent.tool_selection import select_tools_for_turn, widen_tool_surface
from capabledeputy.llm.models_config import ToolSelectionConfig
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.session.model import Session
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult


async def _noop(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _tool(name: str, kind: CapabilityKind) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"tool {name}",
        capability_kind=kind,
        handler=_noop,
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
        parameters_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )


def _session_with_caps(*kinds: CapabilityKind) -> Session:
    caps = frozenset(Capability(kind=kind, pattern="*") for kind in kinds)
    return Session.new(capability_set=caps, purpose_handle="inbox")


def test_full_surface_when_small_catalog() -> None:
    registry = ToolRegistry()
    tools = [
        _tool("inbox.list", CapabilityKind.GMAIL_READ),
        _tool("email.send", CapabilityKind.SEND_EMAIL),
        _tool("policy.preview", CapabilityKind.READ_FS),
    ]
    for tool in tools:
        registry.register(tool)
    session = _session_with_caps(CapabilityKind.GMAIL_READ, CapabilityKind.SEND_EMAIL)
    visible = tools
    result = select_tools_for_turn(
        registry,
        session,
        ExecutionMode.TURN_LEVEL,
        visible,
        user_message="triage my inbox",
        selection_config=ToolSelectionConfig(max_selected=15),
    )
    assert result.method == "full_surface"
    assert len(result.selected) == 3


def test_family_narrows_inbox_tools() -> None:
    registry = ToolRegistry()
    tools = [
        _tool(f"inbox.tool{i}", CapabilityKind.GMAIL_READ) for i in range(12)
    ] + [
        _tool(f"calendar.tool{i}", CapabilityKind.CALENDAR_READ) for i in range(12)
    ] + [_tool("policy.preview", CapabilityKind.READ_FS)]
    for tool in tools:
        registry.register(tool)
    session = _session_with_caps(CapabilityKind.GMAIL_READ, CapabilityKind.READ_FS)
    families = ToolFamiliesConfig(
        mandatory_always=("policy.preview",),
        families={
            "inbox": ToolFamily(
                purpose_handles=frozenset({"inbox"}),
                include_prefixes=("inbox.",),
            ),
        },
    )
    result = select_tools_for_turn(
        registry,
        session,
        ExecutionMode.TURN_LEVEL,
        tools,
        user_message="search urgent inbox mail",
        families=families,
        selection_config=ToolSelectionConfig(mode="retrieve", retrieval_top_k=8, max_selected=8),
    )
    names = {t.name for t in result.selected}
    assert "policy.preview" in names
    assert all(n.startswith("inbox.") or n == "policy.preview" for n in names)


def test_widen_adds_missing_tool() -> None:
    registry = ToolRegistry()
    tools = [_tool("inbox.list", CapabilityKind.GMAIL_READ)]
    session = _session_with_caps(CapabilityKind.GMAIL_READ)
    base = select_tools_for_turn(
        registry,
        session,
        ExecutionMode.TURN_LEVEL,
        tools,
        user_message="hi",
        selection_config=ToolSelectionConfig(mode="retrieve", max_selected=1),
    )
    widened = widen_tool_surface(base, tools, missing_tool_name="inbox.list")
    assert len(widened.selected) >= len(base.selected)


def test_load_tool_families_from_repo() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cfg = load_tool_families(root / "configs" / "tool_families.yaml")
    assert "policy.preview" in cfg.mandatory_always
    assert "inbox" in cfg.families