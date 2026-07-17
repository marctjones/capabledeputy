"""#306 surface B — `mode.dispatcher.select_mode()` posture floor-invariance.

A posture's `flow_pattern_defaults` choose the execution mode per tier. The
structural floor is `_FLOOR_PATTERN` (posture.py): RESTRICTED / PROHIBITED data
must run under a planner-blind pattern (REFERENCE / SEALED). Surface B asserts:

  B1  For ANY valid posture (adversarially generated, `projection_only` free)
      and ANY session carrying a RESTRICTED+ Axis-A tag, `select_mode` returns
      REFERENCE or SEALED, or fails closed (`ModeSelectionError`) — it NEVER
      returns a planner-exposing mode (TURN_LEVEL / DUAL_LLM / PROGRAMMATIC).
      Swept across degenerate tool surfaces that saturate `_achievable_mode`'s
      downgrade path — the likeliest place a floor could slip.
  B2  For ANY valid posture and ANY label state, the selected mode's planner-
      exposure strength is >= the floor for the session's highest tier. Asserted
      against `_FLOOR_PATTERN` (not a hard-coded TURN_LEVEL) so it stays a real
      lock if a tier floor is ever raised.
  B3  `Posture.validate()` REJECTS every sub-floor posture — the generation-side
      guarantee that "any posture that validates respects the floor," which is
      what makes B1/B2's "valid posture" quantifier safe.

Beyond the TLA model (which has no execution-mode / posture concept).
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    ModeSelectionError,
    select_mode,
)
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import CategoryTag, LabelState
from capabledeputy.policy.posture import (
    _FLOOR_PATTERN,
    _PATTERN_STRENGTH,
    Posture,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from tests.invariants.floor_strategies import (
    adversarial_postures,
    label_states,
    valid_postures,
)

_SETTINGS = settings(max_examples=300, deadline=None)

# Planner-EXPOSING modes: the ones the restricted floor must never allow.
_EXPOSING_MODES = frozenset(
    {ExecutionMode.TURN_LEVEL, ExecutionMode.DUAL_LLM, ExecutionMode.PROGRAMMATIC},
)


async def _noop(args: dict[str, Any], context: ToolContext) -> ToolResult:
    return ToolResult(output={})


def _read_tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="read",
        capability_kind=CapabilityKind.READ_FS,
        handler=_noop,
        operations=(Operation(EffectClass.FETCH),),
        risk_ids=("RISK-INDIRECT-INJECTION",),
    )


def _handle_tool() -> ToolDefinition:
    return ToolDefinition(
        name="ref.consume",
        description="handle consumer",
        capability_kind=CapabilityKind.SEND_EMAIL,
        handler=_noop,
        operations=(Operation(EffectClass.COMMUNICATE),),
        risk_ids=("RISK-DATA-EXFIL-AGENT-TOOLS",),
        surfaces_destination_id=True,
        accepts_handles=True,
        handle_arg_names=("payload",),
        parameters_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}, "payload": {"type": "string"}},
        },
    )


def _sandbox_tool() -> ToolDefinition:
    return ToolDefinition(
        name="sandbox.run",
        description="sandbox",
        capability_kind=CapabilityKind.EXECUTE_SANDBOX,
        handler=_noop,
        effect_class="EXECUTE.sandbox",
        operations=(Operation(EffectClass.EXECUTE_SANDBOX),),
        risk_ids=("RISK-UNSAFE-CODE-EXEC",),
        surfaces_destination_id=True,
    )


def _extractor_tool() -> ToolDefinition:
    return _registered(
        ToolDefinition(
            name="quarantined.extract_inbox",
            description="quarantined projection",
            capability_kind=CapabilityKind.READ_FS,
            handler=_noop,
            operations=(Operation(EffectClass.FETCH),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
        ),
    )


def _registered(tool: ToolDefinition) -> ToolDefinition:
    return tool


# Registry-surface variants that exercise every branch of `_achievable_mode`'s
# downgrade ladder: nothing usable, only reads, a handle sink, a sandbox sink,
# and a quarantined extractor.
def _registry(*tools: ToolDefinition) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


_SURFACES = st.sampled_from(
    [
        (),  # empty — degenerate, forces restricted fail-closed
        (_read_tool("fs.read"),),  # reads only — no handle/sandbox/extractor
        (_handle_tool(),),  # Pattern 3 available
        (_sandbox_tool(),),  # Pattern 5 sink present
        (_read_tool("fs.read"), _extractor_tool()),  # DUAL_LLM extractor present
    ],
)


def _tier_rank(t: Tier) -> int:
    from capabledeputy.policy.tiers import _RANK

    return _RANK[t]


@given(
    posture=valid_postures(),
    labels=label_states(),
    surface=_SURFACES,
    has_sandbox=st.booleans(),
    restricted_extra=st.booleans(),
)
@_SETTINGS
def test_restricted_never_selects_exposing_mode(
    posture: Posture,
    labels: LabelState,
    surface: tuple[ToolDefinition, ...],
    has_sandbox: bool,
    restricted_extra: bool,
) -> None:
    """B1 — a session carrying restricted-tier data selects REFERENCE/SEALED or
    fails closed; never a planner-exposing mode, for any valid posture and any
    (possibly degenerate) tool surface."""
    a = set(labels.a)
    if restricted_extra:
        a.add(CategoryTag("health", Tier.RESTRICTED))
    label_state = LabelState(a=frozenset(a), b=labels.b)
    # Only meaningful when a restricted+ tag is actually present.
    if not any(_tier_rank(t.tier) >= _tier_rank(Tier.RESTRICTED) for t in label_state.a):
        return
    reg = _registry(*surface)
    try:
        mode, _reason = select_mode(
            label_state,
            reg,
            has_sandbox_actuator=has_sandbox,
            posture=posture,
        )
    except ModeSelectionError:
        return  # fail-closed is the floor being honored
    assert mode not in _EXPOSING_MODES, (
        f"restricted data selected planner-exposing mode {mode} "
        f"(posture={posture.id}, surface={[t.name for t in surface]})"
    )


@given(
    posture=valid_postures(),
    labels=label_states(),
    surface=_SURFACES,
    has_sandbox=st.booleans(),
)
@_SETTINGS
def test_selected_mode_never_below_tier_floor(
    posture: Posture,
    labels: LabelState,
    surface: tuple[ToolDefinition, ...],
    has_sandbox: bool,
) -> None:
    """B2 — the selected mode's planner-exposure strength is never below the
    floor for the session's highest tier, for any valid posture. Guards the
    `_achievable_mode` downgrade path against slipping below the floor."""
    reg = _registry(*surface)
    try:
        mode, _reason = select_mode(
            labels,
            reg,
            has_sandbox_actuator=has_sandbox,
            posture=posture,
        )
    except ModeSelectionError:
        return  # fail-closed >= floor by definition
    highest = Tier.NONE
    for tag in labels.a:
        if _tier_rank(tag.tier) > _tier_rank(highest):
            highest = tag.tier
    floor = _FLOOR_PATTERN[highest]
    assert _PATTERN_STRENGTH[mode] >= _PATTERN_STRENGTH[floor], (
        f"selected {mode} (strength {_PATTERN_STRENGTH[mode]}) below floor {floor} "
        f"(strength {_PATTERN_STRENGTH[floor]}) for tier {highest}"
    )


@given(posture=adversarial_postures())
@_SETTINGS
def test_validate_rejects_every_sub_floor_posture(posture: Posture) -> None:
    """B3 — generation-side lock: a posture is accepted by `validate()` iff
    every per-tier default meets the floor. So the "valid posture" quantifier in
    B1/B2 is exactly the set that respects the floor."""
    below_floor = any(
        _PATTERN_STRENGTH[mode] < _PATTERN_STRENGTH[_FLOOR_PATTERN[tier]]
        for tier, mode in posture.flow_pattern_defaults.items()
    )
    from capabledeputy.policy.posture import PostureError

    if below_floor:
        try:
            posture.validate()
        except PostureError:
            return
        raise AssertionError(f"sub-floor posture {posture.flow_pattern_defaults} was accepted")
    # Not below floor ⇒ must validate cleanly.
    posture.validate()


# --- Non-vacuity witnesses -------------------------------------------------


def test_witness_restricted_routes_to_reference_or_fails_closed() -> None:
    """A restricted-tier session with a handle tool routes to REFERENCE; with
    no protective sink it fails closed — pinning that B1 exercises a live
    restricted floor rather than passing on empty label sets."""
    from capabledeputy.policy.posture import BUILTIN_POSTURES

    restricted = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
    posture = BUILTIN_POSTURES["low-friction-practical"]

    mode, _ = select_mode(restricted, _registry(_handle_tool()), posture=posture)
    assert mode == ExecutionMode.REFERENCE

    import pytest

    with pytest.raises(ModeSelectionError):
        select_mode(restricted, _registry(_read_tool("fs.read")), posture=posture)
