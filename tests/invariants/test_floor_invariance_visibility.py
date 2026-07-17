"""#306 surface C — `filter_tools_for_mode()` raw-reader hiding is knob-independent.

The #302 CaMeL floor (dispatcher `filter_tools_for_mode` layer 2): in the
exposure-limited modes (DUAL_LLM / REFERENCE / SEALED) the raw labeled-data
readers AND the untrusted-source raw readers are hidden from the planner
UNCONDITIONALLY — no posture, not even one with `projection_only: false`, can
re-expose them. Surface C locks that:

  C1  For ANY posture (`projection_only` True or False) and ANY exposure-limited
      mode, every raw reader is hidden.
  C2  Non-vacuity witness: in a NON-exposure mode (TURN_LEVEL / PROGRAMMATIC) the
      `projection_only` knob genuinely works — True hides `inbox.read`, False
      exposes it — so C1 is a real floor above a working knob, not a filter that
      always strips everything.

Beyond the TLA model (which has no tool-visibility concept).
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from capabledeputy.mode.dispatcher import (
    _RAW_LABELED_DATA_TOOLS,
    UNTRUSTED_SOURCE_RAW_READERS,
    ExecutionMode,
    filter_tools_for_mode,
)
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.posture import Posture
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult
from tests.invariants.floor_strategies import valid_postures

_SETTINGS = settings(max_examples=200, deadline=None)

_RAW_READERS = tuple(sorted(_RAW_LABELED_DATA_TOOLS | UNTRUSTED_SOURCE_RAW_READERS))
_EXPOSURE_LIMITED = (
    ExecutionMode.DUAL_LLM,
    ExecutionMode.REFERENCE,
    ExecutionMode.SEALED,
)
_NON_EXPOSURE = (ExecutionMode.TURN_LEVEL, ExecutionMode.PROGRAMMATIC)


async def _noop(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _tool(name: str) -> ToolDefinition:
    # Bare ToolDefinition (no registry validation needed — filter_tools_for_mode
    # keys only on `.name`).
    return ToolDefinition(
        name=name,
        description="t",
        capability_kind=CapabilityKind.READ_FS,
        handler=_noop,
    )


_ALL_TOOLS = [_tool(name) for name in (*_RAW_READERS, "inbox.list", "calendar.read")]


@given(
    posture=valid_postures(),
    mode=st.sampled_from(_EXPOSURE_LIMITED),
)
@_SETTINGS
def test_raw_readers_hidden_in_exposure_limited_modes(
    posture: Posture,
    mode: ExecutionMode,
) -> None:
    """C1 — every raw reader is hidden in DUAL_LLM/REFERENCE/SEALED regardless
    of the posture's projection_only knob (the #302 floor)."""
    visible = {t.name for t in filter_tools_for_mode(_ALL_TOOLS, mode, posture)}
    for reader in _RAW_READERS:
        assert reader not in visible, (
            f"raw reader {reader!r} visible in {mode} under posture "
            f"projection_only={posture.projection_only} — #302 floor breached"
        )


@given(mode=st.sampled_from(_EXPOSURE_LIMITED))
@_SETTINGS
def test_raw_readers_hidden_even_with_projection_only_false(mode: ExecutionMode) -> None:
    """C1 (sharpened) — an explicit `projection_only=False` posture, the
    operator override that re-exposes raw readers in TURN_LEVEL, STILL cannot
    re-expose them in an exposure-limited mode."""
    posture = Posture(id="raw-allowed", projection_only=False).validate()
    visible = {t.name for t in filter_tools_for_mode(_ALL_TOOLS, mode, posture)}
    assert not (set(_RAW_READERS) & visible)


@given(mode=st.sampled_from(_NON_EXPOSURE))
@_SETTINGS
def test_projection_only_knob_works_outside_exposure_limited_modes(
    mode: ExecutionMode,
) -> None:
    """C2 — non-vacuity witness: outside the exposure-limited modes the knob
    genuinely governs `inbox.read` (True hides, False exposes). Proves C1 is a
    floor above a working knob, not a filter that always strips everything."""
    hide = Posture(id="proj-on", projection_only=True).validate()
    show = Posture(id="proj-off", projection_only=False).validate()
    hidden = {t.name for t in filter_tools_for_mode(_ALL_TOOLS, mode, hide)}
    shown = {t.name for t in filter_tools_for_mode(_ALL_TOOLS, mode, show)}
    assert "inbox.read" not in hidden
    assert "inbox.read" in shown
    # The non-untrusted raw readers are only hidden by mode (layer 2), so in a
    # non-exposure mode they stay visible under both knob settings.
    assert "fs.read" in hidden and "fs.read" in shown
