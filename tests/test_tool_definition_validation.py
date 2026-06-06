"""R3a — invariant tests for validate_tool_definition (the registry-load
contract, contracts/tool_definition.md). Fail-closed per Constitution VI.

These exercise the validator directly (it is wired into register() in
R3b once native tools declare the new fields).
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import ProvenanceLevel
from capabledeputy.tools.registry import (
    ToolDefinition,
    ToolValidationError,
    validate_tool_definition,
)


async def _noop_handler(args, ctx):  # pragma: no cover - never called here
    raise NotImplementedError


def _tool(**overrides) -> ToolDefinition:
    """A valid baseline tool; override fields per test."""
    base: dict[str, object] = {
        "name": "t",
        "description": "d",
        "capability_kind": "data.read",
        "handler": _noop_handler,
        "operations": (Operation(EffectClass.OBSERVE),),
        "risk_ids": ("RISK-PII-DISCLOSURE",),
        "surfaces_destination_id": False,  # OBSERVE is read-only -> fine
    }
    base.update(overrides)
    return ToolDefinition(**base)  # type: ignore[arg-type]


def test_valid_tool_passes() -> None:
    validate_tool_definition(_tool())


def test_missing_operation_refused() -> None:
    with pytest.raises(ToolValidationError, match="operation"):
        validate_tool_definition(_tool(operations=()))


def test_missing_risk_ids_refused() -> None:
    with pytest.raises(ToolValidationError, match="risk_id"):
        validate_tool_definition(_tool(risk_ids=()))


def test_mechanical_execute_must_not_be_social() -> None:
    with pytest.raises(ToolValidationError, match="social_commitment"):
        validate_tool_definition(
            _tool(
                operations=(Operation(EffectClass.EXECUTE_HOST),),
                social_commitment=True,
                surfaces_destination_id=True,
            )
        )


def test_accepts_handles_requires_arg_in_schema() -> None:
    with pytest.raises(ToolValidationError, match="handle_arg_names"):
        validate_tool_definition(
            _tool(
                accepts_handles=True,
                handle_arg_names=("body",),
                parameters_schema={"type": "object", "properties": {}, "required": []},
            )
        )


def test_write_effect_requires_canonical_destination() -> None:
    # COMMUNICATE without surfaces_destination_id is a write/egress with no
    # canonical destination → refused (FR-048).
    with pytest.raises(ToolValidationError, match="surfaces_destination_id"):
        validate_tool_definition(
            _tool(
                operations=(Operation(EffectClass.COMMUNICATE),),
                surfaces_destination_id=False,
            )
        )


def test_write_effect_with_destination_passes() -> None:
    validate_tool_definition(
        _tool(
            operations=(Operation(EffectClass.COMMUNICATE),),
            surfaces_destination_id=True,
        )
    )


def test_unknown_risk_id_refused_when_register_known() -> None:
    with pytest.raises(ToolValidationError, match="unknown risk_ids"):
        validate_tool_definition(
            _tool(risk_ids=("RISK-DOES-NOT-EXIST",)),
            known_risk_ids=frozenset({"RISK-PII-DISCLOSURE"}),
        )


def test_known_risk_id_passes_register_check() -> None:
    validate_tool_definition(
        _tool(risk_ids=("RISK-PII-DISCLOSURE",)),
        known_risk_ids=frozenset({"RISK-PII-DISCLOSURE"}),
    )


def test_operation_required_floor_is_carried() -> None:
    op = Operation(EffectClass.TRANSACT, required_floor=ProvenanceLevel.SYSTEM_INTERNAL)
    tool = _tool(operations=(op,), surfaces_destination_id=True)
    validate_tool_definition(tool)
    assert tool.operations[0].required_floor is ProvenanceLevel.SYSTEM_INTERNAL
