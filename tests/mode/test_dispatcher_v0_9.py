"""T100 — Mode dispatcher returns REFERENCE/SEALED for restricted (US5).

The v0.9 extension to select_mode: for restricted-tier sessions,
the mode is REFERENCE (Pattern ③) when handle-aware tools exist,
SEALED (Pattern ⑤) when only a sandbox actuator is wired. The
dispatcher MUST NOT auto-de-escalate to DUAL_LLM or TURN_LEVEL —
those modes do not protect a restricted tier's raw data from
landing in the planner's context.

The legacy select_mode (used for non-restricted sessions) is
unchanged.
"""

from __future__ import annotations

import pytest

from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    ModeSelectionError,
    select_mode_for_restricted,
)


def test_dispatcher_returns_reference_for_handle_aware_restricted() -> None:
    mode, reason = select_mode_for_restricted(
        has_accepts_handles_tool=True,
        has_sandbox_actuator=False,
    )
    assert mode == ExecutionMode.REFERENCE
    assert "Pattern (3)" in reason


def test_dispatcher_returns_sealed_for_sandbox_only_restricted() -> None:
    mode, _ = select_mode_for_restricted(
        has_accepts_handles_tool=False,
        has_sandbox_actuator=True,
    )
    assert mode == ExecutionMode.SEALED


def test_dispatcher_never_falls_back_to_dual_llm_for_restricted() -> None:
    """No combination of restricted-tier inputs may yield DUAL_LLM,
    TURN_LEVEL, or PROGRAMMATIC. The dispatcher either picks a
    Pattern ③/⑤ mode or refuses entirely."""
    # With both patterns available ⇒ REFERENCE.
    mode_both, _ = select_mode_for_restricted(
        has_accepts_handles_tool=True,
        has_sandbox_actuator=True,
    )
    assert mode_both in (ExecutionMode.REFERENCE, ExecutionMode.SEALED)
    assert mode_both not in (
        ExecutionMode.DUAL_LLM,
        ExecutionMode.TURN_LEVEL,
        ExecutionMode.PROGRAMMATIC,
    )


def test_dispatcher_refuses_when_neither_pattern_available() -> None:
    """The dispatcher refuses to auto-de-escalate. Audit caller sees
    a ModeSelectionError they can convert into a refused spawn."""
    with pytest.raises(ModeSelectionError):
        select_mode_for_restricted(
            has_accepts_handles_tool=False,
            has_sandbox_actuator=False,
        )


def _registry_with(*, handles: bool):
    """Minimal registry whose tool may declare accepts_handles. Bypasses
    register()'s full schema validation — select_mode only reads
    accepts_handles off registry.list()."""
    from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolResult

    async def _noop(_args: dict, _context: ToolContext) -> ToolResult:
        return ToolResult(output={})

    reg = ToolRegistry()
    reg._tools["t"] = ToolDefinition(
        name="t",
        description="x",
        capability_kind="EXECUTE",
        handler=_noop,
        accepts_handles=handles,
        handle_arg_names=("ref",) if handles else (),
    )
    return reg


def _restricted_state():
    from capabledeputy.policy.labels import CategoryTag, LabelState
    from capabledeputy.policy.tiers import Tier

    return LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))


def test_select_mode_auto_selects_reference_for_restricted() -> None:
    """Issue #52 — a restricted-tier label_state whose tool surface offers
    handles drives the *turn* mode to REFERENCE (Pattern ③), not just
    permits it at spawn."""
    from capabledeputy.mode.dispatcher import select_mode

    mode, reason = select_mode(_restricted_state(), _registry_with(handles=True))
    assert mode == ExecutionMode.REFERENCE
    assert "Pattern (3)" in reason


def test_select_mode_falls_to_sealed_for_restricted_with_sandbox_only() -> None:
    """Issue #52 — no handle-aware tool but a SandboxActuator is wired ⇒
    SEALED (Pattern ⑤)."""
    from capabledeputy.mode.dispatcher import select_mode

    mode, _reason = select_mode(
        _restricted_state(),
        _registry_with(handles=False),
        has_sandbox_actuator=True,
    )
    assert mode == ExecutionMode.SEALED


def test_select_mode_refuses_restricted_when_no_protective_mode() -> None:
    """Issue #52 — restricted tier with neither Pattern ③ nor ⑤ available
    fails closed (FR-047), never silently de-escalating to ②/①."""
    from capabledeputy.mode.dispatcher import select_mode

    with pytest.raises(ModeSelectionError):
        select_mode(_restricted_state(), _registry_with(handles=False))


def test_restricted_floor_beats_prefer_programmatic() -> None:
    """Pattern ④ cannot downgrade restricted data out of Pattern ③/⑤."""
    from capabledeputy.mode.dispatcher import select_mode

    mode, reason = select_mode(
        _restricted_state(),
        _registry_with(handles=True),
        prefer_programmatic=True,
    )

    assert mode == ExecutionMode.REFERENCE
    assert "Pattern (3)" in reason


def test_restricted_floor_refuses_unsafe_forced_mode() -> None:
    from capabledeputy.mode.dispatcher import select_mode

    with pytest.raises(ModeSelectionError, match="cannot run forced mode"):
        select_mode(
            _restricted_state(),
            _registry_with(handles=True),
            force_mode=ExecutionMode.TURN_LEVEL,
        )
