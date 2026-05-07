"""Execution mode selection (DESIGN.md §5.4).

Per-turn decision: which of the three execution modes (turn-level
inheritance, dual-LLM, programmatic) should this turn run in? The
dispatcher is a pure function over (label_set, available_tools);
its decision is logged as a `mode.selected` audit event so the
choice is replayable and inspectable in the trace.

In v0.1 we have turn-level and dual-LLM. Programmatic mode is Phase 7.
The dispatcher escalates to dual-LLM when a session carries any
confidential.* labels AND the registry has at least one tool that
declares it can extract via a quarantined LLM (anything in the
`quarantined.*` namespace).

When dual-LLM mode is active, build_tool_descriptions in agent/loop
filters the LLM-visible tool set to hide direct labeled-data readers
(memory.read, fs.read, web.fetch) and surface only the extraction
tools. This forces the planner to go through the schema-validated
gate rather than reading raw labeled bytes.
"""

from __future__ import annotations

from enum import StrEnum

from capabledeputy.policy.labels import Label
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
) -> tuple[ExecutionMode, str]:
    """Pick the execution mode for an upcoming turn.

    Returns (mode, reason) so the audit record explains the choice.
    """
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
