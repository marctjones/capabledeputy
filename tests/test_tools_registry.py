from typing import Any
from uuid import uuid4

import pytest

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.tools.registry import (
    DuplicateToolError,
    ToolContext,
    ToolDefinition,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
)


async def _noop_handler(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _make_tool(name: str = "fs.read", **kwargs: Any) -> ToolDefinition:
    defaults: dict[str, Any] = {
        "name": name,
        "description": "test tool",
        "capability_kind": CapabilityKind.READ_FS,
        "handler": _noop_handler,
        "target_arg": "path",
        "operations": (Operation(EffectClass.FETCH),),
        "risk_ids": ("RISK-INDIRECT-INJECTION",),
    }
    defaults.update(kwargs)
    return ToolDefinition(**defaults)


def test_register_and_get_round_trip() -> None:
    registry = ToolRegistry()
    tool = _make_tool()
    registry.register(tool)
    assert registry.get("fs.read") is tool


def test_duplicate_register_raises() -> None:
    registry = ToolRegistry()
    registry.register(_make_tool())
    with pytest.raises(DuplicateToolError):
        registry.register(_make_tool())


def test_get_unknown_raises() -> None:
    registry = ToolRegistry()
    with pytest.raises(ToolNotFoundError):
        registry.get("nope")


def test_list_returns_all_registered() -> None:
    registry = ToolRegistry()
    a = _make_tool("a")
    b = _make_tool("b")
    registry.register(a)
    registry.register(b)
    listed = registry.list()
    assert {t.name for t in listed} == {"a", "b"}


def test_descriptors_return_split_tool_contract() -> None:
    registry = ToolRegistry()
    registry.register(
        _make_tool(
            "macos.open_app",
            target_template="macos://app/{bundle_id}",
            accepts_handles=True,
            handle_arg_names=("bundle_id",),
            parameters_schema={
                "type": "object",
                "properties": {"bundle_id": {"type": "string"}},
                "required": ["bundle_id"],
            },
        ),
    )
    [descriptor] = registry.descriptors()
    assert descriptor.runtime.name == "macos.open_app"
    assert descriptor.policy.capability_kind == CapabilityKind.READ_FS.value
    assert descriptor.policy.target_template == "macos://app/{bundle_id}"
    assert descriptor.policy.risk_ids == ("RISK-INDIRECT-INJECTION",)
    assert descriptor.flow.accepts_handles is True
    assert descriptor.flow.handle_arg_names == ("bundle_id",)


def test_contains_and_len() -> None:
    registry = ToolRegistry()
    assert len(registry) == 0
    assert "fs.read" not in registry
    registry.register(_make_tool())
    assert "fs.read" in registry
    assert len(registry) == 1


def test_extract_target_pulls_from_named_arg() -> None:
    tool = _make_tool(target_arg="path")
    assert tool.extract_target({"path": "/home/marc/notes.md"}) == "/home/marc/notes.md"


def test_extract_target_renders_template() -> None:
    tool = _make_tool(target_template="macos://app/{bundle_id}")
    assert (
        tool.extract_target({"bundle_id": "com.apple.iWork.Pages"})
        == "macos://app/com.apple.iWork.Pages"
    )


def test_extract_target_template_missing_arg_uses_empty_string() -> None:
    tool = _make_tool(target_template="gcal://calendar/{calendar_id}/events")
    assert tool.extract_target({}) == "gcal://calendar//events"


def test_extract_target_returns_empty_string_when_missing() -> None:
    tool = _make_tool(target_arg="path")
    assert tool.extract_target({}) == ""


def test_extract_amount_returns_none_when_arg_unset() -> None:
    tool = _make_tool(amount_arg=None)
    assert tool.extract_amount({"amount": 50}) is None


def test_extract_amount_returns_int_when_present() -> None:
    tool = _make_tool(amount_arg="amount")
    assert tool.extract_amount({"amount": 50}) == 50


def test_extract_amount_returns_none_when_missing() -> None:
    tool = _make_tool(amount_arg="amount")
    assert tool.extract_amount({}) is None


def test_inherent_labels_default_empty() -> None:
    tool = _make_tool()
    assert tool.inherent_tags == LabelState()


def test_inherent_labels_can_be_supplied() -> None:
    tool = _make_tool(
        inherent_tags=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    )
    assert ProvenanceLevel.EXTERNAL_UNTRUSTED in {t.level for t in tool.inherent_tags.b}


def test_tool_context_carries_session_state() -> None:
    sid = uuid4()
    ctx = ToolContext(session_id=sid, label_state=LabelState())
    assert ctx.session_id == sid
    assert ctx.label_state == LabelState()


def test_tool_result_default_no_additional_labels() -> None:
    result = ToolResult(output={"ok": True})
    assert result.additional_tags == LabelState()


def test_tool_result_with_additional_labels() -> None:
    result = ToolResult(
        output={"value": "x"},
        additional_tags=LabelState(
            b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})
        ),
    )
    assert ProvenanceLevel.EXTERNAL_UNTRUSTED in {t.level for t in result.additional_tags.b}
