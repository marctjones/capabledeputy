"""T099 — Restricted-tier session requires Pattern ③ or ⑤ (FR-047).

A session whose effective tier is `restricted` MUST have at least
one of:
  - Pattern ③ Reference Handle (any tool in the surface declares
    `accepts_handles=True`), OR
  - Pattern ⑤ Sealed-effect (a SandboxActuator port is wired).

If neither is available, the spawn is refused with
ModeSelectionError BEFORE any capability is granted (fail-closed).
"""

from __future__ import annotations

from typing import Any

import pytest

from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    ModeSelectionError,
    select_mode_for_restricted,
    tool_surface_offers_handles,
)
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


async def _noop_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _legacy_tool() -> ToolDefinition:
    """A tool with no Pattern ③ opt-in."""
    return ToolDefinition(
        name="legacy.read",
        description="t",
        capability_kind=CapabilityKind.READ_FS,
        handler=_noop_handler,
    )


def _handle_aware_tool() -> ToolDefinition:
    """A tool that declares accepts_handles=True — eligible for
    Pattern ③ routing."""
    return ToolDefinition(
        name="api.post_handle",
        description="t",
        capability_kind=CapabilityKind.WEB_FETCH,
        handler=_noop_handler,
        accepts_handles=True,
        handle_arg_names=("body",),
    )


def test_tool_surface_offers_handles_detects_pattern3() -> None:
    assert tool_surface_offers_handles([_handle_aware_tool()])
    assert not tool_surface_offers_handles([_legacy_tool()])


def test_mixed_surface_offers_handles_if_any_tool_does() -> None:
    """Even a single accepts_handles=True tool is enough — the
    planner can route restricted data through that tool."""
    assert tool_surface_offers_handles([_legacy_tool(), _handle_aware_tool()])


def test_restricted_with_pattern_3_picks_reference_mode() -> None:
    mode, reason = select_mode_for_restricted(
        has_accepts_handles_tool=True,
        has_sandbox_actuator=False,
    )
    assert mode == ExecutionMode.REFERENCE
    assert "Pattern (3)" in reason


def test_restricted_with_only_pattern_5_picks_sealed_mode() -> None:
    mode, reason = select_mode_for_restricted(
        has_accepts_handles_tool=False,
        has_sandbox_actuator=True,
    )
    assert mode == ExecutionMode.SEALED
    assert "Pattern (5)" in reason


def test_restricted_prefers_pattern_3_when_both_available() -> None:
    """Pattern ③ is preferred — data-blind planning > containment.
    Containment is fallback because it admits a wider blast radius
    if it fails."""
    mode, _ = select_mode_for_restricted(
        has_accepts_handles_tool=True,
        has_sandbox_actuator=True,
    )
    assert mode == ExecutionMode.REFERENCE


def test_restricted_without_either_refused_at_spawn() -> None:
    """FR-047 — no Pattern ③ and no Pattern ⑤ ⇒ spawn refused.
    ModeSelectionError carries the rationale for the audit."""
    with pytest.raises(ModeSelectionError) as exc:
        select_mode_for_restricted(
            has_accepts_handles_tool=False,
            has_sandbox_actuator=False,
        )
    assert "Pattern (3)" in str(exc.value) or "FR-047" in str(exc.value)
