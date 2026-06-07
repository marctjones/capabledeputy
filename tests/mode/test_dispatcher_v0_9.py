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


@pytest.mark.xfail(
    reason=(
        "Issue #52 — KNOWN GAP: the per-turn select_mode() does NOT consult "
        "tier, so a restricted-tier session silently runs under Pattern (2)/(1) "
        "instead of auto-selecting Pattern (3) REFERENCE. select_mode_for_restricted "
        "is currently only a spawn-time refusal gate; its chosen mode is discarded. "
        "This xfail flips to xpass once select_mode() integrates the restricted floor."
    ),
    strict=True,
)
def test_select_mode_auto_selects_reference_for_restricted() -> None:
    """End-to-end intent (FR-047): a restricted-tier label_state whose tool
    surface offers handles must drive the *turn* mode to REFERENCE — not
    just be permitted at spawn. Anchors the gap until the wiring lands."""
    from capabledeputy.mode.dispatcher import select_mode
    from capabledeputy.policy.labels import CategoryTag, LabelState
    from capabledeputy.policy.tiers import Tier
    from capabledeputy.tools.registry import ToolDefinition, ToolRegistry

    async def _noop(_args: dict) -> dict:
        return {}

    reg = ToolRegistry()
    # Bypass register()'s full schema validation — select_mode only reads
    # accepts_handles off registry.list(); this keeps the anchor minimal.
    reg._tools["sealed.run"] = ToolDefinition(
        name="sealed.run",
        description="handle-aware tool",
        capability_kind="EXECUTE",
        handler=_noop,
        accepts_handles=True,
        handle_arg_names=("ref",),
    )
    restricted = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))

    mode, _reason = select_mode(restricted, reg)
    assert mode == ExecutionMode.REFERENCE
