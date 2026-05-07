"""Execution mode selection and capability-aware tool visibility
(DESIGN.md §5.4 + Phase 7b).

Two related filtering decisions per turn:

1. select_mode picks turn-level or dual-LLM based on whether the
   session carries any confidential.* labels and whether a quarantined
   extractor exists. Logged as mode.selected.

2. visible_tools filters by the session's capability set: a tool is
   visible to the LLM only when the session holds at least one
   capability whose kind matches the tool's capability_kind. This
   means the LLM cannot even propose calling a tool the session has
   no business invoking — the call would be denied at dispatch
   anyway, but visibility filtering both saves a wasted turn and
   stops leaking 'tools that exist elsewhere' to this LLM.
"""

from __future__ import annotations

from enum import StrEnum

from capabledeputy.policy.labels import Label
from capabledeputy.session.model import Session
from capabledeputy.tools.registry import ToolDefinition, ToolRegistry


class ExecutionMode(StrEnum):
    TURN_LEVEL = "turn_level"
    DUAL_LLM = "dual_llm"
    PROGRAMMATIC = "programmatic"


_CONFIDENTIAL_LABELS: frozenset[Label] = frozenset(
    {
        Label.CONFIDENTIAL_HEALTH,
        Label.CONFIDENTIAL_FINANCIAL,
        Label.CONFIDENTIAL_PERSONAL,
    },
)

_RAW_LABELED_DATA_TOOLS: frozenset[str] = frozenset(
    {"memory.read", "fs.read", "web.fetch"},
)


def select_mode(
    label_set: frozenset[Label],
    registry: ToolRegistry,
    *,
    prefer_programmatic: bool = False,
    force_mode: ExecutionMode | None = None,
) -> tuple[ExecutionMode, str]:
    """Pick the execution mode for an upcoming turn.

    Three layers of override, in order of decreasing strength:

      1. `force_mode` — caller (CLI `--mode`) explicitly demands a mode
         for this turn only.
      2. `prefer_programmatic` — session-level flag set at session.new
         time; takes precedence over the auto-escalation heuristic so
         users can opt into programmatic for a whole session.
      3. Auto-heuristic — confidential labels present + a quarantined
         extractor registered → DUAL_LLM. Otherwise TURN_LEVEL.

    Returns (mode, reason) so the audit record explains the choice.
    """
    if force_mode is not None:
        return force_mode, f"forced by caller: {force_mode.value}"

    if prefer_programmatic:
        return (
            ExecutionMode.PROGRAMMATIC,
            "session prefers programmatic mode; planner will emit a program",
        )

    if not label_set & _CONFIDENTIAL_LABELS:
        return ExecutionMode.TURN_LEVEL, "no confidential labels in session"

    has_quarantined = any(tool.name.startswith("quarantined.") for tool in registry.list())
    if has_quarantined:
        return (
            ExecutionMode.DUAL_LLM,
            "session carries confidential labels; routing reads through quarantined extraction",
        )

    return (
        ExecutionMode.TURN_LEVEL,
        "session has confidential labels but no quarantined extractor "
        "is registered; falling back to turn-level with policy gating",
    )


def filter_tools_for_mode(
    tools: list[ToolDefinition],
    mode: ExecutionMode,
) -> list[ToolDefinition]:
    """Hide raw labeled-data readers when running dual-LLM mode."""
    if mode != ExecutionMode.DUAL_LLM:
        return tools
    return [t for t in tools if t.name not in _RAW_LABELED_DATA_TOOLS]


def visible_tools(
    registry: ToolRegistry,
    session: Session,
    mode: ExecutionMode,
) -> list[ToolDefinition]:
    """Tools the LLM should see for this turn.

    A tool is visible iff:
      1. It survives the mode filter (raw readers hidden in dual-LLM).
      2. The session holds at least one capability whose kind matches
         the tool's capability_kind.
    """
    mode_filtered = filter_tools_for_mode(registry.list(), mode)
    held_kinds = {cap.kind for cap in session.capability_set}
    return [t for t in mode_filtered if t.capability_kind in held_kinds]
