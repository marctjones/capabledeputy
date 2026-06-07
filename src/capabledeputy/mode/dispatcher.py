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

from capabledeputy.policy.labels import LabelState
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import Session
from capabledeputy.tools.registry import ToolDefinition, ToolRegistry


class ExecutionMode(StrEnum):
    TURN_LEVEL = "turn_level"
    DUAL_LLM = "dual_llm"
    PROGRAMMATIC = "programmatic"
    # 003 US5 T105 — Pattern ③ Reference Handle mode. The planner sees
    # only handle ids; the dispatcher binds real values post-decide().
    REFERENCE = "reference"
    # 003 US5 T105 — Pattern ⑤ Sealed-effect mode. Effects run inside
    # a disposable isolation region (containment lifts effective
    # reversibility to reversible/system). Requires a SandboxActuator
    # port to be wired; if not, spawn refuses (FR-047/FR-042).
    SEALED = "sealed"


class ModeSelectionError(RuntimeError):
    """Mode selection failed-closed (FR-047). Raised when a session
    requires `restricted`-tier handling but neither Pattern ③
    (accepts_handles=True tools) nor Pattern ⑤ (SandboxActuator port)
    is available."""


_CONFIDENTIAL_CATEGORIES: frozenset[str] = frozenset(
    {
        "health",
        "financial",
        "personal",
    },
)

_RAW_LABELED_DATA_TOOLS: frozenset[str] = frozenset(
    {"memory.read", "fs.read", "web.fetch"},
)


def select_mode(
    label_state: LabelState,
    registry: ToolRegistry,
    *,
    prefer_programmatic: bool = False,
    force_mode: ExecutionMode | None = None,
    has_sandbox_actuator: bool = False,
) -> tuple[ExecutionMode, str]:
    """Pick the execution mode for an upcoming turn.

    Layers of override, in order of decreasing strength:

      1. `force_mode` — caller (CLI `--mode`) explicitly demands a mode
         for this turn only.
      2. `prefer_programmatic` — session-level flag set at session.new
         time; takes precedence over the auto-escalation heuristic so
         users can opt into programmatic for a whole session.
      3. Restricted-tier floor (FR-047, Issue #52) — if ANY Axis-A tag is
         at `restricted` tier, the turn MUST run under Pattern ③
         (REFERENCE, when a handle-aware tool is present) or Pattern ⑤
         (SEALED, when a SandboxActuator is wired), never ①/② which would
         land raw restricted data in the planner. Neither available ⇒
         `ModeSelectionError` (fail-closed). This mirrors the spawn-time
         gate but now also *drives the per-turn mode*, which it did not
         before (#52): previously a restricted session silently ran under
         Pattern ②/①.
      4. Auto-heuristic — confidential categories present + a quarantined
         extractor registered → DUAL_LLM. Otherwise TURN_LEVEL.

    Returns (mode, reason) so the audit record explains the choice.
    Raises ModeSelectionError only for the restricted-tier fail-closed.
    """
    if force_mode is not None:
        return force_mode, f"forced by caller: {force_mode.value}"

    if prefer_programmatic:
        return (
            ExecutionMode.PROGRAMMATIC,
            "session prefers programmatic mode; planner will emit a program",
        )

    # Restricted-tier floor (FR-047 / #52) — evaluated before the
    # confidential-category heuristic so a restricted tag can never
    # de-escalate to DUAL_LLM/TURN_LEVEL.
    if any(tag.tier == Tier.RESTRICTED for tag in label_state.a):
        return select_mode_for_restricted(
            has_accepts_handles_tool=tool_surface_offers_handles(registry.list()),
            has_sandbox_actuator=has_sandbox_actuator,
        )

    # Check if any category in label_state.a matches confidential categories
    has_confidential = any(tag.category in _CONFIDENTIAL_CATEGORIES for tag in label_state.a)

    if not has_confidential:
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


# --- T105 — restricted-tier mode floor (FR-047) ---------------------


def select_mode_for_restricted(
    *,
    has_accepts_handles_tool: bool,
    has_sandbox_actuator: bool,
) -> tuple[ExecutionMode, str]:
    """Pick the execution mode for a session whose effective tier is
    `restricted`. The contract (FR-047):

      - Prefer REFERENCE (Pattern ③) if any tool in the session's
        surface declares `accepts_handles=True` — the planner stays
        data-blind.
      - Otherwise fall back to SEALED (Pattern ⑤) if a SandboxActuator
        port is wired — the work runs in a disposable region.
      - Neither available ⇒ raise ModeSelectionError. The spawn must
        be refused before any capability is granted (fail-closed).

    Returns (mode, reason) like select_mode does.
    """
    if has_accepts_handles_tool:
        return (
            ExecutionMode.REFERENCE,
            "restricted-tier session: Pattern (3) handles available",
        )
    if has_sandbox_actuator:
        return (
            ExecutionMode.SEALED,
            "restricted-tier session: Pattern (5) sandbox actuator available",
        )
    raise ModeSelectionError(
        "restricted-tier session requires Pattern (3) accepts_handles or "
        "Pattern (5) SandboxActuator — neither available; spawn refused (FR-047)",
    )


def tool_surface_offers_handles(tools: list[ToolDefinition]) -> bool:
    """True iff any tool in `tools` declares `accepts_handles=True`.
    Lets the spawn-time check (T099) consult the tool surface without
    importing ToolDefinition internals at the call site.
    """
    return any(getattr(t, "accepts_handles", False) for t in tools)


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
