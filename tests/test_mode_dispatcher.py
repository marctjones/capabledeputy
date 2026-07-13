from typing import Any

from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    filter_tools_for_mode,
    select_mode,
)
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session
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
                operations=(Operation(EffectClass.FETCH),),
                risk_ids=("RISK-INDIRECT-INJECTION",),
            ),
        )
    return registry


def _restricted_state() -> LabelState:
    return LabelState(
        a=frozenset(
            {CategoryTag("health", Tier.RESTRICTED, assignment_provenance="source-declared")}
        ),
    )


def _handle_aware_tool(name: str, kind: CapabilityKind) -> ToolDefinition:
    from capabledeputy.tools.registry import _KIND_TO_EFFECT

    effect = _KIND_TO_EFFECT.get(str(kind), EffectClass.FETCH)
    return ToolDefinition(
        name=name,
        description="handle consumer",
        capability_kind=kind,
        handler=_noop,
        target_arg="target",
        operations=(Operation(effect),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
        surfaces_destination_id=True,
        accepts_handles=True,
        handle_arg_names=("payload",),
        parameters_schema={
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "payload": {"type": "string"},
            },
        },
    )


def _sandbox_tool() -> ToolDefinition:
    return ToolDefinition(
        name="sandbox.run",
        description="sandbox",
        capability_kind=CapabilityKind.EXECUTE_SANDBOX,
        handler=_noop,
        target_arg="spec_id",
        effect_class="EXECUTE.sandbox",
        operations=(Operation(EffectClass.EXECUTE_SANDBOX),),
        risk_ids=("RISK-UNSAFE-CODE-EXEC",),
        surfaces_destination_id=True,
        parameters_schema={
            "type": "object",
            "properties": {"spec_id": {"type": "string"}},
        },
    )


def test_no_confidential_labels_picks_turn_level() -> None:
    registry = _make_registry("memory.read", "quarantined.extract")
    mode, reason = select_mode(LabelState(), registry)
    assert mode == ExecutionMode.TURN_LEVEL
    assert "no confidential" in reason


def test_confidential_with_quarantined_picks_dual_llm() -> None:
    registry = _make_registry("memory.read", "quarantined.extract")
    mode, _ = select_mode(
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
        registry,
    )
    assert mode == ExecutionMode.DUAL_LLM


def test_confidential_without_quarantined_falls_back() -> None:
    registry = _make_registry("memory.read")
    mode, reason = select_mode(
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
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


def test_filter_hides_raw_readers_in_reference_and_sealed() -> None:
    registry = _make_registry(
        "memory.read",
        "fs.read",
        "web.fetch",
        "memory.handle",
        "sandbox.run",
    )

    for mode in (ExecutionMode.REFERENCE, ExecutionMode.SEALED):
        filtered = filter_tools_for_mode(registry.list(), mode)
        names = {t.name for t in filtered}
        assert "memory.read" not in names
        assert "fs.read" not in names
        assert "web.fetch" not in names
        assert "memory.handle" in names
        assert "sandbox.run" in names


def test_each_confidential_label_triggers_dual_llm() -> None:
    registry = _make_registry("quarantined.extract")
    for category in ("health", "financial", "personal"):
        mode, _ = select_mode(
            LabelState(
                a=frozenset(
                    {CategoryTag(category, Tier.REGULATED, assignment_provenance="source-declared")}
                )
            ),
            registry,
        )
        assert mode == ExecutionMode.DUAL_LLM


def test_prefer_programmatic_overrides_default_heuristic() -> None:
    registry = _make_registry("memory.read")
    mode, reason = select_mode(
        LabelState(),
        registry,
        prefer_programmatic=True,
    )
    assert mode == ExecutionMode.PROGRAMMATIC
    assert "prefers programmatic" in reason


def test_force_mode_overrides_prefer_and_heuristic() -> None:
    registry = _make_registry("quarantined.extract")
    # Confidential label + quarantined extractor would normally pick
    # dual_llm; prefer_programmatic would pick programmatic; force
    # beats both.
    mode, reason = select_mode(
        LabelState(
            a=frozenset(
                {CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared")}
            )
        ),
        registry,
        prefer_programmatic=True,
        force_mode=ExecutionMode.TURN_LEVEL,
    )
    assert mode == ExecutionMode.TURN_LEVEL
    assert "forced" in reason


def test_restricted_reference_selection_requires_visible_handle_consumer() -> None:
    registry = ToolRegistry()
    registry.register(_handle_aware_tool("email.send", CapabilityKind.SEND_EMAIL))
    registry.register(_sandbox_tool())
    session = Session.new(
        capability_set=frozenset({Capability(kind=CapabilityKind.EXECUTE_SANDBOX, pattern="*")}),
    )

    mode, reason = select_mode(
        _restricted_state(),
        registry,
        has_sandbox_actuator=True,
        session=session,
    )

    assert mode == ExecutionMode.SEALED
    assert "Pattern (5)" in reason


def test_restricted_reference_selected_when_handle_consumer_visible() -> None:
    registry = ToolRegistry()
    registry.register(_handle_aware_tool("sandbox.run", CapabilityKind.EXECUTE_SANDBOX))
    session = Session.new(
        capability_set=frozenset({Capability(kind=CapabilityKind.EXECUTE_SANDBOX, pattern="*")}),
    )

    mode, reason = select_mode(
        _restricted_state(),
        registry,
        has_sandbox_actuator=True,
        session=session,
    )

    assert mode == ExecutionMode.REFERENCE
    assert "Pattern (3)" in reason
