"""Deterministic LLM context builder.

Given a session + tool registry + recent audit events, produces a
system prompt string that gives the LLM rich awareness of:
  - Current session state (labels, profile, dial, capabilities)
  - Tool policy hints (likely outcome per tool given current state)
  - Recent decisions (last N events for in-context learning)
  - Recovery hints (what to do on deny)

Pure function. Same inputs → same output (modulo timestamps which
are formatted as ISO 8601 strings). This makes audit replay
meaningful.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from capabledeputy.audit.events import Event, EventType
from capabledeputy.llm.types import ToolDescription
from capabledeputy.policy.labels import Label
from capabledeputy.session.model import Session
from capabledeputy.tools.registry import ToolDefinition


@dataclass(frozen=True)
class LLMContext:
    """Structured context for the LLM.

    Attributes:
        system_prompt: The full system prompt string to send to the LLM.
        context_hash: SHA-256 of system_prompt for audit replay.
        n_tools: Number of tools visible in this context.
        n_recent_decisions: Number of recent policy decisions included.
    """

    system_prompt: str
    context_hash: str
    n_tools: int
    n_recent_decisions: int


def _format_iso8601(dt: datetime) -> str:
    """Format datetime as ISO 8601 string, rounded to seconds."""
    return dt.isoformat(timespec="seconds")


def _compute_context_hash(system_prompt: str) -> str:
    """Compute SHA-256 of system_prompt for audit."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def _session_labels_str(session: Session) -> str:
    """Format session's label set as human-readable string."""
    if not session.label_set:
        return "none"
    # Sort labels for determinism
    return ", ".join(sorted(label.value for label in session.label_set))


def _session_profile_str(session: Session) -> str:
    """Format session's clearance profile."""
    if session.clearance_profile_id:
        return session.clearance_profile_id
    return "default"


def _session_dial_str(session: Session) -> str:
    """Format session's risk preference dial."""
    return session.risk_preference_at_spawn


def _likely_outcome_for_tool(
    tool: ToolDefinition,
    label_set: frozenset[Label],
) -> str:
    """Heuristic: estimate likely policy outcome for this tool given
    current session labels.

    Returns one of: "likely AUTO", "likely DENY", "likely REQUIRE_APPROVAL",
    "ALLOW likely", or "???".
    """
    # Egress tools with untrusted content -> DENY
    if (
        (Label.UNTRUSTED_EXTERNAL in label_set or Label.UNTRUSTED_USER_INPUT in label_set)
        and (Label.EGRESS_EMAIL in label_set or Label.EGRESS_PURCHASE in label_set)
    ):
        return "likely DENY (untrusted-meets-egress)"

    # Health label with egress -> DENY
    if (
        Label.CONFIDENTIAL_HEALTH in label_set
        and (Label.EGRESS_EMAIL in label_set or Label.EGRESS_PURCHASE in label_set)
    ):
        return "likely DENY (health-meets-egress)"

    # Financial + email -> DENY
    if (
        Label.CONFIDENTIAL_FINANCIAL in label_set
        and Label.EGRESS_EMAIL in label_set
        and tool.capability_kind.value == "SEND_EMAIL"
    ):
        return "likely DENY (financial-meets-email)"

    # Financial + purchase -> REQUIRE_APPROVAL
    if (
        Label.CONFIDENTIAL_FINANCIAL in label_set
        and Label.EGRESS_PURCHASE in label_set
        and tool.capability_kind.value == "QUEUE_PURCHASE"
    ):
        return "likely REQUIRE_APPROVAL (financial-meets-purchase)"

    # Reversible system tools with no egress -> likely AUTO
    if tool.default_reversibility is not None:
        degree = tool.default_reversibility.get("degree", "")
        agent = tool.default_reversibility.get("agent", "")
        if degree == "reversible" and agent == "system":
            # Check effect class doesn't egress
            if tool.effect_class and not tool.effect_class.startswith("egress"):
                return "likely AUTO"

    # Default fallback
    return "ALLOW likely"


def _format_tool_line(
    tool_desc: ToolDescription,
    tool_def: ToolDefinition | None,
    label_set: frozenset[Label],
) -> str:
    """Format a single tool line for the context, including outcome hint."""
    hint = "???"
    if tool_def is not None:
        hint = _likely_outcome_for_tool(tool_def, label_set)

    # Tool line format: name [CAPABILITY_KIND] hint
    kind = tool_def.capability_kind.value if tool_def else "UNKNOWN"
    return f"- {tool_desc.name:<25} [{kind:<20}] {hint}"


def _format_recent_decisions(events: list[Event], max_count: int = 10) -> tuple[str, int]:
    """Format recent policy decisions from audit events.

    Returns (formatted_string, count_included).
    """
    # Filter to POLICY_DECIDED and TOOL_RETURNED events
    policy_events = [
        e
        for e in events
        if e.event_type == EventType.POLICY_DECIDED
    ]

    # Take last N
    recent = policy_events[-max_count:] if policy_events else []

    if not recent:
        return "No recent decisions.", 0

    lines = []
    for event in recent:
        payload = event.payload or {}
        ts = _format_iso8601(event.timestamp)
        decision = payload.get("decision", "?")
        tool = payload.get("tool", "?")
        reason = payload.get("reason", "")

        # Format: timestamp DECISION tool (reason)
        reason_str = f"({reason})" if reason else ""
        lines.append(f"{ts} {decision.upper():<16} {tool:<20} {reason_str}")

    return "\n".join(lines), len(recent)


def build_llm_context(
    session: Session,
    available_tools: list[ToolDescription],
    tool_registry: dict[str, ToolDefinition],
    recent_events: list[Event],
    *,
    max_recent_decisions: int = 10,
) -> LLMContext:
    """Build deterministic LLM context given session + tools + audit events.

    Args:
        session: Current session state.
        available_tools: Tools visible to the LLM on this turn.
        tool_registry: Mapping of tool name -> ToolDefinition for hints.
        recent_events: Recent audit events (typically from tail()).
        max_recent_decisions: Max recent policy decisions to include.

    Returns:
        LLMContext with system_prompt, context_hash, n_tools, n_recent_decisions.
    """
    # --- Session state section ---
    session_id_short = str(session.id)[:8]
    labels_str = _session_labels_str(session)
    profile_str = _session_profile_str(session)
    dial_str = _session_dial_str(session)

    # --- Tools section ---
    tool_lines = []
    for tool_desc in available_tools:
        tool_def = tool_registry.get(tool_desc.name)
        line = _format_tool_line(tool_desc, tool_def, session.label_set)
        tool_lines.append(line)

    tools_section = "\n".join(tool_lines) if tool_lines else "No tools available."

    # --- Recent decisions section ---
    decisions_section, n_decisions = _format_recent_decisions(
        recent_events,
        max_recent_decisions,
    )

    # --- Assemble full system prompt ---
    system_prompt = f"""You are an AI assistant operating inside CapableDeputy.

You operate inside a runtime that gates every tool call by capability and
information-flow policy. The runtime enforces these rules — you cannot
bypass them, but you should understand them so you can give the user
useful, accurate answers.

CRITICAL — how you call tools:

- The ONLY way to invoke a tool is via the API's `tool_use` mechanism.
  Tools available to you on this turn appear in the system-provided
  tool list. If a tool is not in that list, it does not exist for you,
  full stop.
- NEVER write a tool call as text or code (e.g. backticked ```inbox.search(...)```).
  That is not an invocation — it is fabrication. The runtime cannot see
  it, no policy is evaluated, no real action happens.
- NEVER invent tool names. If the user asks for "forward email" and your
  tool list has only `email.send`, `inbox.read`, `inbox.list`, say so —
  do not invent `email.forward`.
- If your tool list is EMPTY for this turn, you have no tools at all.
  Tell the user explicitly: "I have no capabilities granted in this
  session. Run `/grant <KIND> <pattern>` to give me one (for example,
  `/grant SEND_EMAIL recipient@example.com --one-shot`)." Do not pretend.
- After calling a tool, the runtime returns a real result. Report that
  result accurately. Do not fabricate decisions or outputs.

How the policy works (high-level):

- Every tool you call reads or writes labelled data. Reading an inbox
  message adds `untrusted.external` to the session. Reading the calendar
  adds `confidential.personal`. Financial notes carry `confidential.financial`.
- Outbound channels (email send, purchase) carry an egress label.
- Conflict rules block flows: e.g. `untrusted.external` + `egress.email` -> DENY,
  `confidential.financial` + `egress.purchase` -> REQUIRE_APPROVAL.
- Labels are sticky: once the session has read untrusted content, every
  later outbound attempt — including ones you compose "from scratch" —
  is still tagged with the prior untrusted read and is blocked.

Before any outbound or destructive tool call — `email.send`,
`purchase.queue`, `memory.update`, `memory.delete`,
`calendar.update_event`, `calendar.delete_event` — and especially when
the session has accumulated multiple labels, **first call `policy.preview`
with the same kind and target you intend to use**. If preview returns
`decision="deny"`, do not attempt the real call; tell the user what
would have been blocked and why, and suggest the appropriate escape
hatch below.

When a tool call comes back DENY:

- This is a hard block, not "ask again nicely." The same call from the
  same session will fail the same way; do not retry.
- If the user genuinely wants the action, tell them about the escape
  hatches the *human* (not you) controls:
    * `/spawn <intent>` — they can create a fresh clean session that
      doesn't carry this session's labels, then grant a one-shot
      capability for the specific action. This is the right path when
      the current session is "tainted" by an untrusted read.
    * `/extract <key> from <message-id>` (when available) — runs the
      quarantined extractor against a single untrusted message and
      produces a labelled-clean fact that a clean session can use.
- Match the recovery to the rule in the denial:
    * `capability-expired` — the capability's deadline passed. The
      user can `/grant` a fresh capability, optionally with a longer
      `--ttl SECONDS`.
    * `rate-limit-exceeded` — too many uses in the window. The user
      can wait for the window to slide, or `/grant` a capability with
      a higher `--rate MAX/WINDOW`.
    * `capability-revoked-by-prior-use` — a prior tool use revoked it;
      a fresh `/spawn`-ed session has not used the revoking tool.
- Never claim "no approval mechanism exists." There IS a human-in-the-loop
  path; you just can't invoke it yourself. Tell the user how *they* can.

When a tool call comes back REQUIRE_APPROVAL:

- The action is held pending. The user reviews the verbatim payload and
  decides. Tell the user clearly what you want to do and why, so the
  approval prompt is meaningful.

# Session State

- Session id: {session_id_short}
- Purpose: {session.intent or "general"}
- Profile: {profile_str}
- Risk dial: {dial_str}
- Current labels: {labels_str}

# Available Tools

{tools_section}

# Recent Decisions (last {n_decisions})

{decisions_section}

# Recovery Hints

When a tool is denied:
- Don't retry; the deny is structural.
- Either ask the operator to override (`capdep override request`) or work around it.
- If a session is tainted by an earlier read, a fresh session may be cleaner.

When you have completed the task, respond with a final answer and no
tool calls. Be concise and honest about what you did and didn't do.
"""

    # Compute deterministic hash
    context_hash = _compute_context_hash(system_prompt)

    return LLMContext(
        system_prompt=system_prompt,
        context_hash=context_hash,
        n_tools=len(available_tools),
        n_recent_decisions=n_decisions,
    )
