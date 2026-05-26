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
from capabledeputy.policy.capabilities import kind_name
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


def _session_caps_str(session: Session) -> str:
    """Format the session's currently-granted capabilities. Empty set
    means the agent has zero permissions — the LLM context tells it to
    surface that to the user rather than fabricate."""
    if not session.capability_set:
        return "(none — agent has zero capabilities; tell user to /grant)"
    parts = []
    for cap in sorted(session.capability_set, key=lambda c: (kind_name(c.kind), c.pattern)):
        parts.append(f"{kind_name(cap.kind)}({cap.pattern})")
    return ", ".join(parts)


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
    if (Label.UNTRUSTED_EXTERNAL in label_set or Label.UNTRUSTED_USER_INPUT in label_set) and (
        Label.EGRESS_EMAIL in label_set or Label.EGRESS_PURCHASE in label_set
    ):
        return "likely DENY (untrusted-meets-egress)"

    # Health label with egress -> DENY
    if Label.CONFIDENTIAL_HEALTH in label_set and (
        Label.EGRESS_EMAIL in label_set or Label.EGRESS_PURCHASE in label_set
    ):
        return "likely DENY (health-meets-egress)"

    # Financial + email -> DENY
    if (
        Label.CONFIDENTIAL_FINANCIAL in label_set
        and Label.EGRESS_EMAIL in label_set
        and kind_name(tool.capability_kind) == "SEND_EMAIL"
    ):
        return "likely DENY (financial-meets-email)"

    # Financial + purchase -> REQUIRE_APPROVAL
    if (
        Label.CONFIDENTIAL_FINANCIAL in label_set
        and Label.EGRESS_PURCHASE in label_set
        and kind_name(tool.capability_kind) == "QUEUE_PURCHASE"
    ):
        return "likely REQUIRE_APPROVAL (financial-meets-purchase)"

    # Reversible system tools with no egress -> likely AUTO
    if tool.default_reversibility is not None:
        degree = tool.default_reversibility.get("degree", "")
        agent = tool.default_reversibility.get("agent", "")
        if (
            degree == "reversible"
            and agent == "system"
            and tool.effect_class
            and not tool.effect_class.startswith("egress")
        ):
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
    kind = kind_name(tool_def.capability_kind) if tool_def else "UNKNOWN"
    return f"- {tool_desc.name:<25} [{kind:<20}] {hint}"


def _format_recent_decisions(events: list[Event], max_count: int = 10) -> tuple[str, int]:
    """Format recent policy decisions from audit events.

    Returns (formatted_string, count_included).
    """
    # Filter to POLICY_DECIDED and TOOL_RETURNED events
    policy_events = [e for e in events if e.event_type == EventType.POLICY_DECIDED]

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


def _format_custom_kinds_section() -> str:
    """Issue #35 — enumerate custom kinds the global registry knows
    about, so the LLM can suggest valid `/grant <plugin:kind>`
    commands. Empty when no plugins have contributed.

    Format:
        Plugin kinds (from servers.d/*.yaml):
          slack:dm.send (destructive — sends a Slack DM)
          slack:read    (read-only — list/search Slack messages)
          notion:read   (read-only — read Notion pages)
    """
    # Import the registry accessor lazily to avoid circular: capabilities
    # imports nothing from agent, but agent imports both.
    from capabledeputy.policy.capabilities import _CUSTOM_KIND_REGISTRY

    if _CUSTOM_KIND_REGISTRY is None:
        return ""
    kinds = _CUSTOM_KIND_REGISTRY.all()
    if not kinds:
        return ""

    lines = ["", "Plugin kinds (from servers.d/*.yaml):"]
    for kind in kinds:
        flag = "destructive" if kind.destructive else "read-only"
        desc = kind.description or ""
        if desc:
            lines.append(f"  {kind.name:<28} ({flag} — {desc})")
        else:
            lines.append(f"  {kind.name:<28} ({flag})")
    return "\n".join(lines)


def build_llm_context(
    session: Session,
    available_tools: list[ToolDescription],
    tool_registry: dict[str, ToolDefinition],
    recent_events: list[Event],
    *,
    max_recent_decisions: int = 10,
    sandbox_summary: str | None = None,
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

    # --- Sandbox section ---
    # Only present when an actuator is wired. Tells the agent the
    # operational rule: containment lifts reversibility, but does NOT
    # declassify outputs that leave the region (FR-041).
    if sandbox_summary:
        # NB: This is a regular string with one explicit `.format(...)`
        # call for `sandbox_summary`. NOT an f-string: the body
        # contains literal `{filename}` / `{base64}` placeholders that
        # describe the `sandbox.run` arg shape — if this were an
        # f-string, Python would try to resolve those names at
        # evaluation time and raise NameError.
        sandbox_section = (
            "# Sandbox (disposable isolation regions)\n\n"
            + sandbox_summary
            + """

What the sandbox does for you:
- Lifts reversibility to `reversible/system` while a run executes
  inside a region — the region's discard undoes every side effect
  inside it, by construction.
- Does NOT declassify outputs. Data that leaves the region keeps
  its source-category labels. Containment kills the side effect,
  not the label.

When to ask the user for sandboxed execution:
- The action is risky/irreversible at the host level, but trivially
  undoable inside a container (a build that writes files, a script
  that mutates a checked-out tree, an experimental command run).
- The user wants to try an untrusted blob (a downloaded script, a
  generated patch) without risk to their host filesystem.

How to use it: call the `sandbox.run` tool (when granted
`EXECUTE_SANDBOX <spec_id>` capability). Pass:
  - `spec_id`: one of the region ids above
  - `argv`: command + args, e.g. ["python3", "/in/script.py"]
  - `inputs`: optional {filename: "text"} or {filename: {base64: "..."}}
              — each lands at /in/<filename> inside the container
  - `timeout_seconds`: optional, default 30, max 600
The container runs, files it writes under /out come back in the
result. The region is discarded after the run — containment is
the guarantee.

"""
        )
    else:
        sandbox_section = ""

    # --- Custom kinds section (Issue #35) ---
    # Enumerate any custom kinds registered from servers.d/*.yaml so
    # the LLM knows what /grant <namespaced:kind> commands the
    # operator might run. Empty section when no plugins have
    # contributed custom kinds.
    custom_kinds_section = _format_custom_kinds_section()

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

- This is a hard block, not "ask again nicely." The same call from
  the same session will fail the same way; do not retry.
- The runtime ALWAYS computes `recovery_steps` for non-ALLOW
  outcomes — those steps are the literal slash commands the user
  needs to run. See the Recovery Hints section below for how to
  surface them. Don't invent your own recovery prose.
- Never claim "no approval mechanism exists." There IS a
  human-in-the-loop path; you just can't invoke it yourself. The
  `recovery_steps` field tells the user exactly what to do.

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
- Capabilities held by this session: {_session_caps_str(session)}

# Available Tools

{tools_section}

# Recent Decisions (last {n_decisions})

{decisions_section}

# Recovery Hints

When a tool is denied, the runtime computes the exact slash commands
that would unblock the action. They arrive on each tool outcome's
`recovery_steps` field (and on `policy.preview`'s output dict when
you preview before calling).

**IMPORTANT — quote-only rule:** when telling the user how to
recover, you MUST quote those commands verbatim. Never invent or
paraphrase. The user has F1 / F2 / F3 keypresses that execute
the first three steps directly, so quoting accurately matters.

If `recovery_steps` is empty, say so explicitly: "The runtime
suggests no slash-command recovery for this denial — the operator
will need to look at the daemon config." Don't fabricate.

**Surfacing format:** when recovery_steps are present, say:

> The runtime suggests:
> 1. `<command from steps[0]>` — <rationale from steps[0]>
> 2. `<command from steps[1]>` — <rationale from steps[1]>
> 3. `<command from steps[2]>` — <rationale from steps[2]>
>
> Press F1 / F2 / F3 to run them.

Recovery commands always come from this fixed runtime vocabulary:
`/grant`, `/spawn`, `/override`, `/extract`. If you find yourself
typing a command name that isn't in `recovery_steps`, stop — that
command doesn't exist or isn't available right now.

Other rules:
- Don't retry a denied call; the deny is structural.
- A `recovery_steps` list with multiple entries means there are
  alternative paths. The first is the primary (simplest). Mention
  alternatives if the user pushes back on the first.
- Never reference `capdep override request` (not a real command),
  `/override grant` (not a real command), or other commands you
  don't see in `recovery_steps`. Real commands only.

When you have completed the task, respond with a final answer and no
tool calls. Be concise and honest about what you did and didn't do.

# Context budget — DON'T overfetch (Issue #36)

The LLM context window is finite. If you stuff too much data into it
across iterations, the next call will fail with a context-overflow
error and the user gets nothing.

Operational guidance for batch reads:

- **Prefer LIST/SEARCH tools that return metadata + snippets.** Most
  upstream servers expose `*_list`, `*_search`, `*_summarize`
  variants alongside `*_get`. The list tools return enough to triage;
  full bodies are rarely needed for every item.
- **For email summarization specifically**: `gmail_messages_list`
  returns subjects + snippets. Use those to compose the summary.
  Only fetch full bodies (`gmail_messages_get`) for the 2-3
  messages you genuinely need to quote or analyze.
- **Batch in small groups.** If you must process N items, fetch 5
  at a time and summarize each batch into a brief before fetching
  the next batch. Don't fetch 20 in parallel and try to summarize
  all at once.
- **If you receive a "context approaching limit" system notice**:
  STOP making new tool calls. Respond with a summary of what you
  have. Suggest the user `/spawn` a fresh session if they need
  more detail on something specific.

This isn't about being lazy — it's about staying within the
session's budget so you can actually finish.

{sandbox_section}# Capability Kinds (VALID values for `/grant <KIND>`)

The ONLY valid CapabilityKind values are listed below. NEVER suggest
or invent any other kind — `/grant INBOX_READ`, `/grant ANY_*` do
not exist and will be refused. When telling the user to /grant
something, use one of these exact strings:

  Filesystem:  READ_FS, WRITE_FS, CREATE_FS, MODIFY_FS, DELETE_FS
  Email:       GMAIL_READ, IMAP_READ, SEND_EMAIL
  Drive:       DRIVE_READ
  Calendar:    CALENDAR_READ, CALENDAR_WRITE, CREATE_CAL, MODIFY_CAL, DELETE_CAL
  Web:         WEB_FETCH
  Purchase:    QUEUE_PURCHASE
  Sandbox:     EXECUTE_SANDBOX
{custom_kinds_section}

Examples:
- Read Gmail messages: `/grant GMAIL_READ *` (or `/grant GMAIL_READ from:boss@*`)
- Read Google Drive: `/grant DRIVE_READ *`
- Read IMAP inbox: `/grant IMAP_READ *`
- Send email: `/grant SEND_EMAIL recipient@example.com --one-shot`

Note: a legacy `/grant READ_FS *` capability ALSO satisfies
GMAIL_READ / IMAP_READ / DRIVE_READ (backward-compat union) — but
new sessions should use the granular kinds so the operator can
distinguish "read email" from "read local filesystem."
"""

    # Compute deterministic hash
    context_hash = _compute_context_hash(system_prompt)

    return LLMContext(
        system_prompt=system_prompt,
        context_hash=context_hash,
        n_tools=len(available_tools),
        n_recent_decisions=n_decisions,
    )
