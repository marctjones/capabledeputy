"""Execution mode selection and capability-aware tool visibility
(DESIGN.md §5.4 + Phase 7b).

Two related filtering decisions per turn:

1. select_mode picks the protective flow pattern: turn-level,
   dual-LLM quarantine, programmatic, reference handles, or sealed
   isolation. Restricted-tier data is floored to reference/sealed
   handling and logged as mode.selected.

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
from typing import TYPE_CHECKING

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.labels import LabelState
from capabledeputy.policy.tiers import Tier, is_above
from capabledeputy.session.model import Session
from capabledeputy.tools.registry import ToolDefinition, ToolRegistry

if TYPE_CHECKING:
    from capabledeputy.policy.posture import Posture


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

# Raw labeled-data readers hidden in planner-exposure-limited modes (DUAL_LLM /
# REFERENCE / SEALED): once the session is confidential, these raw reads would
# land labeled data straight in the planner's context.
_RAW_LABELED_DATA_TOOLS: frozenset[str] = frozenset(
    {"memory.read", "fs.read", "web.fetch"},
)

# #359 — untrusted-SOURCE raw readers, hidden from the planner in EVERY mode
# under the projection-only posture (the default). Unlike _RAW_LABELED_DATA_TOOLS
# (which fire only in exposure-limited modes, i.e. only once a confidential
# Axis-A category is present), these must be hidden even in a fresh TURN_LEVEL
# session with no labels yet — because the CaMeL threat is *steering*, and
# steering happens on the FIRST read: an injected email body reaching the
# planner raw on turn 1 has already delivered its instructions before any
# provenance label exists to react to. So the raw reader is never shown; the
# planner reaches inbox content ONLY via `quarantined.extract_inbox` (a
# schema-validated projection: EmailTriageItem / EmailForwardable / …).
# `inbox.list` metadata (id/sender/subject) stays visible for message selection.
# NB: projection-only is the default; a selected posture (#305) can set
# `projection_only: false` (raw-allowed-with-taint) — but that knob only
# governs the raw-exposure modes (TURN_LEVEL/PROGRAMMATIC). In the
# exposure-limited modes these readers are hidden unconditionally (see
# `filter_tools_for_mode` layer 2 — the #302 floor). No shipped preset sets
# the knob false. Scope: inbox.read today; web.fetch is a follow-up (it needs
# a fetch-and-project tool, which does not exist yet).
UNTRUSTED_SOURCE_RAW_READERS: frozenset[str] = frozenset({"inbox.read"})


def planner_projection_only(posture: Posture | None = None) -> bool:
    """Whether untrusted-source raw readers are hidden from the planner
    (projection-only posture). Single choke point, now posture-driven (#305):
    the secure default (True) holds unless an explicitly selected posture sets
    `projection_only: false` (raw-allowed-with-taint — an operator override; no
    shipped preset does this). NB: this knob governs TURN_LEVEL/PROGRAMMATIC
    only — in the exposure-limited modes `filter_tools_for_mode` hides the
    untrusted-source raw readers regardless (the #302 floor)."""
    return True if posture is None else posture.projection_only


def select_mode(
    label_state: LabelState,
    registry: ToolRegistry,
    *,
    prefer_programmatic: bool = False,
    force_mode: ExecutionMode | None = None,
    has_sandbox_actuator: bool = False,
    session: Session | None = None,
    posture: Posture | None = None,
) -> tuple[ExecutionMode, str]:
    """Pick the execution mode for an upcoming turn.

    Layers of override, in order of decreasing strength:

      1. Restricted-tier floor (FR-047, Issue #52) — if ANY Axis-A tag is
         at `restricted` tier, the turn MUST run under Pattern ③
         (REFERENCE, when a handle-aware tool is usable) or Pattern ⑤
         (SEALED, when a sandbox tool is usable), never ①/② which would
         land raw restricted data in the planner. Neither available ⇒
         `ModeSelectionError` (fail-closed). Unsafe forced modes and
         prefer_programmatic cannot downgrade this floor.
      2. `force_mode` — caller (CLI `--mode`) explicitly demands a mode
         for this turn only, when it does not violate the restricted floor.
      3. `prefer_programmatic` — session-level flag set at session.new
         time; takes precedence over the auto-escalation heuristic so
         users can opt into programmatic for a whole session.
      4. `posture` (#304) — a configured security posture supplies the
         default flow pattern per tier, replacing the auto-heuristic for
         NONE/SENSITIVE/REGULATED sessions (restricted was already floored
         above). Downgraded to the strongest achievable pattern, never
         below the tier floor. Absent (posture is None) ⇒ legacy heuristic.
      5. Auto-heuristic — confidential categories present + a quarantined
         extractor registered → DUAL_LLM. Otherwise TURN_LEVEL.

    Returns (mode, reason) so the audit record explains the choice.
    When `session` is provided, restricted-tier selection considers only
    tools visible to that session's current capabilities. Without it, callers
    get the legacy registry-wide behavior used by spawn-time checks/tests.
    Raises ModeSelectionError only for the restricted-tier fail-closed.
    """
    # Restricted-tier floor (FR-047 / #52) — evaluated before the
    # forced/preferred mode and confidential-category heuristic so a
    # restricted tag can never de-escalate to planner-exposing modes.
    if any(tag.tier == Tier.RESTRICTED for tag in label_state.a):
        handles_available = (
            visible_tool_surface_offers_handles(registry, session)
            if session is not None
            else tool_surface_offers_handles(registry.list())
        )
        sealed_available = has_sandbox_actuator and (
            visible_tool_surface_offers_sandbox(registry, session) if session is not None else True
        )
        if force_mode is not None:
            if force_mode == ExecutionMode.REFERENCE:
                if not handles_available:
                    raise ModeSelectionError(
                        "restricted-tier session cannot force reference mode: "
                        "no usable accepts_handles tool is visible",
                    )
                return force_mode, f"forced by caller: {force_mode.value}"
            if force_mode == ExecutionMode.SEALED:
                if not sealed_available:
                    raise ModeSelectionError(
                        "restricted-tier session cannot force sealed mode: "
                        "no usable SandboxActuator-backed tool is visible",
                    )
                return force_mode, f"forced by caller: {force_mode.value}"
            raise ModeSelectionError(
                "restricted-tier session cannot run forced mode "
                f"{force_mode.value}; requires Pattern (3) reference handles "
                "or Pattern (5) sealed isolation",
            )
        return select_mode_for_restricted(
            has_accepts_handles_tool=handles_available,
            has_sandbox_actuator=sealed_available,
        )

    if force_mode is not None:
        return force_mode, f"forced by caller: {force_mode.value}"

    if prefer_programmatic:
        return (
            ExecutionMode.PROGRAMMATIC,
            "session prefers programmatic mode; planner will emit a program",
        )

    # #304 — a configured posture supplies the default flow pattern per tier,
    # REPLACING the hardcoded confidential→DUAL_LLM heuristic below. Restricted+
    # was already handled by the fail-closed floor above, so this governs only
    # NONE/SENSITIVE/REGULATED sessions. The posture default is downgraded to the
    # strongest pattern actually achievable with the session's tool surface, but
    # never below the tier floor (which for these tiers is TURN_LEVEL). When no
    # posture is configured (posture is None) the legacy heuristic below runs
    # unchanged.
    if posture is not None:
        highest = _highest_tier(label_state)
        desired = posture.flow_pattern_for(highest)
        mode = _achievable_mode(
            desired,
            registry,
            has_sandbox_actuator=has_sandbox_actuator,
            session=session,
        )
        return (
            mode,
            f"posture {posture.id!r}: tier {highest.value} default {desired.value} → {mode.value}",
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
    posture: Posture | None = None,
) -> list[ToolDefinition]:
    """Hide raw readers from the planner.

    Two layers:
      1. #359 — untrusted-SOURCE raw readers (`inbox.read`) are hidden in EVERY
         mode under the projection-only posture (the default), so an injected
         email body can never reach the planner raw — not even on the first read
         of a fresh TURN_LEVEL session. The planner uses the quarantined
         projection instead. #305: a selected posture with
         `projection_only: false` (raw-allowed-with-taint) disables THIS layer
         only — the operator accepts turn-1 steering risk, relying on the
         taint→egress floor.
      2. #302 — raw labeled-data readers (`memory.read`/`fs.read`/`web.fetch`)
         AND the untrusted-source raw readers are hidden in the exposure-limited
         modes (DUAL_LLM / REFERENCE / SEALED), i.e. once the session carries a
         confidential Axis-A category. This layer is knob-independent: no
         posture can re-expose a raw reader in an exposure-limited mode (the
         #302 CaMeL invariant is a floor).
    """
    if planner_projection_only(posture):
        tools = [t for t in tools if t.name not in UNTRUSTED_SOURCE_RAW_READERS]
    if mode not in {
        ExecutionMode.DUAL_LLM,
        ExecutionMode.REFERENCE,
        ExecutionMode.SEALED,
    }:
        return tools
    hidden = _RAW_LABELED_DATA_TOOLS | UNTRUSTED_SOURCE_RAW_READERS
    return [t for t in tools if t.name not in hidden]


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


def _highest_tier(label_state: LabelState) -> Tier:
    """The session's most-restrictive Axis-A tier (NONE if unlabeled)."""
    highest = Tier.NONE
    for tag in label_state.a:
        if is_above(tag.tier, highest):
            highest = tag.tier
    return highest


def _achievable_mode(
    desired: ExecutionMode,
    registry: ToolRegistry,
    *,
    has_sandbox_actuator: bool,
    session: Session | None,
) -> ExecutionMode:
    """Downgrade a posture's desired flow pattern to the strongest pattern the
    session's tool surface can actually support, never below TURN_LEVEL.

    Used only for NONE/SENSITIVE/REGULATED (the restricted floor is fail-closed
    and handled separately), so a fall-through to TURN_LEVEL is always ≥ the tier
    floor. This mirrors the availability checks the restricted floor uses, so a
    posture asking for a pattern the surface can't provide degrades gracefully
    rather than erroring.
    """
    if desired == ExecutionMode.PROGRAMMATIC:
        return ExecutionMode.PROGRAMMATIC

    def _handles() -> bool:
        return (
            visible_tool_surface_offers_handles(registry, session)
            if session is not None
            else tool_surface_offers_handles(registry.list())
        )

    def _sandbox() -> bool:
        return has_sandbox_actuator and (
            visible_tool_surface_offers_sandbox(registry, session) if session is not None else True
        )

    def _extractor() -> bool:
        return any(t.name.startswith("quarantined.") for t in registry.list())

    if desired == ExecutionMode.SEALED and _sandbox():
        return ExecutionMode.SEALED
    if desired in (ExecutionMode.SEALED, ExecutionMode.REFERENCE) and _handles():
        return ExecutionMode.REFERENCE
    if (
        desired in (ExecutionMode.SEALED, ExecutionMode.REFERENCE, ExecutionMode.DUAL_LLM)
        and _extractor()
    ):
        return ExecutionMode.DUAL_LLM
    return ExecutionMode.TURN_LEVEL


def tool_surface_offers_handles(tools: list[ToolDefinition]) -> bool:
    """True iff any tool in `tools` declares `accepts_handles=True`.
    Lets the spawn-time check (T099) consult the tool surface without
    importing ToolDefinition internals at the call site.
    """
    return any(getattr(t, "accepts_handles", False) for t in tools)


def tool_surface_offers_sandbox(tools: list[ToolDefinition]) -> bool:
    """True iff `tools` exposes a Pattern ⑤ sandbox execution sink."""
    return any(
        t.capability_kind == CapabilityKind.EXECUTE_SANDBOX
        or str(t.effect_class or "").lower().startswith("execute.sandbox")
        for t in tools
    )


def visible_tool_surface_offers_handles(registry: ToolRegistry, session: Session) -> bool:
    """True iff this session can actually see a handle-consuming tool."""
    return tool_surface_offers_handles(visible_tools(registry, session, ExecutionMode.REFERENCE))


def visible_tool_surface_offers_sandbox(registry: ToolRegistry, session: Session) -> bool:
    """True iff this session can actually see a sealed sandbox tool."""
    return tool_surface_offers_sandbox(visible_tools(registry, session, ExecutionMode.SEALED))


def visible_tools(
    registry: ToolRegistry,
    session: Session,
    mode: ExecutionMode,
    posture: Posture | None = None,
) -> list[ToolDefinition]:
    """Tools the LLM should see for this turn.

    A tool is visible iff:
      1. It survives the mode filter (raw readers hidden in dual-LLM).
      2. The session holds at least one capability whose kind matches
         the tool's capability_kind.

    `posture` threads the #305 projection-only knob into the mode filter.
    Callers that omit it get the secure default (projection-only) — a missing
    thread fails closed, never open.
    """
    mode_filtered = filter_tools_for_mode(registry.list(), mode, posture)
    return [
        t
        for t in mode_filtered
        if any(cap.covers_kind(t.capability_kind) for cap in session.capability_set)
    ]
