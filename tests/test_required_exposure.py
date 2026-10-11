import pytest

from capabledeputy.mode.dispatcher import ExecutionMode, ModeSelectionError, select_mode
from capabledeputy.policy.labels import LabelState
from capabledeputy.tools.registry import ToolRegistry


def test_no_silent_raw_fallback():
    with pytest.raises(ModeSelectionError, match="no widening"):
        select_mode(LabelState(), ToolRegistry(), required_modes=frozenset({ExecutionMode.SEALED}))


def test_required_mode_does_not_grant_or_choose_mode():
    with pytest.raises(ModeSelectionError):
        select_mode(LabelState(), ToolRegistry(), required_modes=frozenset())
    assert select_mode(LabelState(), ToolRegistry())[0] == ExecutionMode.TURN_LEVEL
    assert (
        select_mode(
            LabelState(), ToolRegistry(), required_modes=frozenset({ExecutionMode.TURN_LEVEL})
        )[0]
        == ExecutionMode.TURN_LEVEL
    )


def test_invalid_requirement_fails_closed():
    with pytest.raises(ModeSelectionError):
        select_mode(
            LabelState(),
            ToolRegistry(),
            required_modes=frozenset({"turn_level"}),  # type: ignore[arg-type]
        )
