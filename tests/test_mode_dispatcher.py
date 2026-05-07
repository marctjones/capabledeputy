from typing import Any

from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    filter_tools_for_mode,
    select_mode,
)
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult


async def _noop(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _make_registry(*tool_names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in tool_names:
        registry.register(
            ToolDefinition(
                name=name,
                description="t",
                capability_kind=CapabilityKind.READ_FS,
                handler=_noop,
            ),
        )
    return registry


def test_no_confidential_labels_picks_turn_level() -> None:
    registry = _make_registry("memory.read", "quarantined.extract")
    mode, reason = select_mode(frozenset(), registry)
    assert mode == ExecutionMode.TURN_LEVEL
    assert "no confidential" in reason


def test_confidential_with_quarantined_picks_dual_llm() -> None:
    registry = _make_registry("memory.read", "quarantined.extract")
    mode, _ = select_mode(
        frozenset({Label.CONFIDENTIAL_HEALTH}),
        registry,
    )
    assert mode == ExecutionMode.DUAL_LLM


def test_confidential_without_quarantined_falls_back() -> None:
    registry = _make_registry("memory.read")
    mode, reason = select_mode(
        frozenset({Label.CONFIDENTIAL_HEALTH}),
        registry,
    )
    assert mode == ExecutionMode.TURN_LEVEL
    assert "no quarantined" in reason


def test_filter_keeps_all_tools_in_turn_level() -> None:
    registry = _make_registry("memory.read", "quarantined.extract", "email.send")
    filtered = filter_tools_for_mode(registry.list(), ExecutionMode.TURN_LEVEL)
    assert {t.name for t in filtered} == {
        "memory.read",
        "quarantined.extract",
        "email.send",
    }


def test_filter_hides_raw_readers_in_dual_llm() -> None:
    registry = _make_registry(
        "memory.read",
        "fs.read",
        "web.fetch",
        "quarantined.extract",
        "email.send",
    )
    filtered = filter_tools_for_mode(registry.list(), ExecutionMode.DUAL_LLM)
    names = {t.name for t in filtered}
    assert "memory.read" not in names
    assert "fs.read" not in names
    assert "web.fetch" not in names
    assert "quarantined.extract" in names
    assert "email.send" in names


def test_each_confidential_label_triggers_dual_llm() -> None:
    registry = _make_registry("quarantined.extract")
    for label in (
        Label.CONFIDENTIAL_HEALTH,
        Label.CONFIDENTIAL_FINANCIAL,
        Label.CONFIDENTIAL_PERSONAL,
    ):
        mode, _ = select_mode(frozenset({label}), registry)
        assert mode == ExecutionMode.DUAL_LLM
