"""Turn-level agent loop (DESIGN.md §5.1).

run_turn() implements the simplest of the three execution modes:
turn-level inheritance. The agent processes one user message at a
time, looping LLM calls and tool dispatches until the LLM produces
a final answer (no tool calls). Every tool result's labels accumulate
into the session via the LabeledToolClient, so subsequent policy
checks within the same turn see the up-to-date label set — that is
the inheritance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from capabledeputy.agent.context import build_llm_context
from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.types import (
    FinishReason,
    LLMResponse,
    Message,
    Role,
    ToolDescription,
)
from capabledeputy.mode.dispatcher import (
    ExecutionMode,
    filter_tools_for_mode,
    select_mode,
    visible_tools,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph, SessionStateError
from capabledeputy.session.model import Session, Turn
from capabledeputy.tools.aliasing import build_alias_map, build_reverse_map
from capabledeputy.tools.client import LabeledToolClient, ToolCallOutcome
from capabledeputy.tools.registry import ToolNotFoundError, ToolRegistry

DEFAULT_SYSTEM_PROMPT = """You are CapableDeputy, a structurally secure personal AI assistant.

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
- Conflict rules block flows: e.g. `untrusted.external` + `egress.email` → DENY,
  `confidential.financial` + `egress.purchase` → REQUIRE_APPROVAL.
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

When you have completed the task, respond with a final answer and no
tool calls. Be concise and honest about what you did and didn't do.
"""


class AgentLoopExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentTurnResult:
    content: str
    iterations: int
    finish_reason: FinishReason
    tool_outcomes: tuple[ToolCallOutcome, ...] = field(default_factory=tuple)


def _turn_to_message(turn: Turn) -> Message | None:
    if turn.role == "user":
        return Message(role=Role.USER, content=turn.content)
    if turn.role == "agent":
        return Message(role=Role.ASSISTANT, content=turn.content)
    return None


def build_tool_descriptions(
    registry: ToolRegistry,
    mode: ExecutionMode = ExecutionMode.TURN_LEVEL,
    session: Session | None = None,
) -> list[ToolDescription]:
    """Build the tool list shown to the LLM.

    If the session has `tool_aliasing` enabled, every visible tool's
    canonical name is replaced with a session-specific token. The
    reverse map is recomputed at dispatch time (also from the session id),
    so we don't have to thread state — the alias function is pure.
    """
    if session is not None:
        tools = visible_tools(registry, session, mode)
    else:
        tools = filter_tools_for_mode(registry.list(), mode)

    aliasing = session is not None and session.tool_aliasing
    alias_map: dict[str, str] = {}
    if aliasing and session is not None:
        alias_map = build_alias_map(session.id, [t.name for t in tools])

    return [
        ToolDescription(
            name=alias_map.get(t.name, t.name),
            description=t.description,
            parameters_schema=t.parameters_schema,
        )
        for t in tools
    ]


def _format_outcome(outcome: ToolCallOutcome) -> str:
    if outcome.error is not None:
        return f"tool error: {outcome.error}"
    if outcome.decision == Decision.DENY:
        rule_part = outcome.rule or "policy"
        reason_part = f": {outcome.reason}" if outcome.reason else ""
        recovery_hint = (
            "\n\nRecovery: This is a structural block, not a permission request. "
            "Consider /spawn to create a fresh session, or /extract to isolate "
            "the data separately."
        )
        return f"POLICY DENIED by rule '{rule_part}'{reason_part}{recovery_hint}"
    if outcome.decision == Decision.REQUIRE_APPROVAL:
        rule_part = outcome.rule or "policy"
        reason_part = f": {outcome.reason}" if outcome.reason else ""
        recovery_hint = (
            "\n\nApproval Status: The operation is queued for human review. "
            "The operator will approve or deny it separately."
        )
        return f"APPROVAL REQUIRED by rule '{rule_part}'{reason_part}{recovery_hint}"
    output = outcome.output if outcome.output is not None else {}
    return str(output)


async def run_turn(
    *,
    session_id: UUID,
    user_message: str,
    llm: LLMClient,
    tool_client: LabeledToolClient,
    registry: ToolRegistry,
    graph: SessionGraph,
    audit: AuditWriter,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_iterations: int = 50,
    force_mode: ExecutionMode | None = None,
) -> AgentTurnResult:
    """Issue #21 — `run_turn` is now a thin wrapper around the
    streaming generator `run_turn_streaming`. Existing callers
    (daemon RPC, tests, programmatic_loop, etc.) keep working
    unchanged: this consumes all events and returns the final
    `AgentTurnResult`. New streaming consumers (chat REPL Rich
    Live region, rich Textual surface) call `run_turn_streaming`
    directly via `for await` and observe each event."""
    from capabledeputy.agent.events import TurnCompleted, TurnInterrupted

    final_result: AgentTurnResult | None = None
    interrupt_reason: str | None = None
    async for evt in run_turn_streaming(
        session_id=session_id,
        user_message=user_message,
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
        system_prompt=system_prompt,
        max_iterations=max_iterations,
        force_mode=force_mode,
    ):
        if isinstance(evt, TurnCompleted):
            final_result = evt.result
        elif isinstance(evt, TurnInterrupted):
            interrupt_reason = evt.reason
    if interrupt_reason == "max_iterations":
        raise AgentLoopExceededError(
            f"agent loop exceeded {max_iterations} iterations",
        )
    if final_result is None:
        # Defensive: streaming generator must yield exactly one of
        # TurnCompleted or TurnInterrupted before returning.
        raise RuntimeError(
            "run_turn_streaming exited without a terminal event",
        )
    return final_result


async def run_turn_streaming(
    *,
    session_id: UUID,
    user_message: str,
    llm: LLMClient,
    tool_client: LabeledToolClient,
    registry: ToolRegistry,
    graph: SessionGraph,
    audit: AuditWriter,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_iterations: int = 50,
    force_mode: ExecutionMode | None = None,
):
    """Streaming variant of `run_turn` (Issue #21).

    Yields `TurnEvent`s as the turn progresses:
      - `IterationStarted` at the top of each loop iter
      - `LLMRequestSent` before each LLM call
      - `LLMResponseReceived` after each LLM call
      - `ToolDispatched` before each tool dispatch
      - `ToolReturned` after each tool returns
      - `TurnCompleted` (terminal) when the LLM produces a final answer
      - `TurnInterrupted` (terminal) when max_iterations exceeded
        (with reason='max_iterations'). Future #23/#31/#32 work
        will surface 'ctrl-c', 'surface-disconnect', and
        'heartbeat-timeout' as additional reasons.

    Token-level streaming (LLMTokenReceived) requires the LLM
    client to support streaming — left as a future extension once
    the underlying client gains that capability. The generator
    structure is designed to accommodate it without further
    architectural changes.
    """
    from capabledeputy.agent.events import (
        IterationStarted,
        LLMRequestSent,
        LLMResponseReceived,
        ToolDispatched,
        ToolReturned,
        TurnCompleted,
        TurnInterrupted,
    )
    session = graph.get(session_id)
    if session.is_terminal:
        raise SessionStateError(
            f"cannot send to terminal session {session_id} (status={session.status})",
        )

    # Mode selection happens BEFORE history is mutated so the
    # programmatic loop can take over cleanly when selected.
    mode, mode_reason = select_mode(
        session.label_set,
        registry,
        prefer_programmatic=session.prefer_programmatic,
        force_mode=force_mode,
    )

    if mode == ExecutionMode.PROGRAMMATIC:
        # Local import to avoid a circular: programmatic_loop imports from
        # this module for AgentTurnResult.
        from capabledeputy.agent.programmatic_loop import run_programmatic_turn

        await audit.write(
            Event(
                event_type=EventType.MODE_SELECTED,
                session_id=session_id,
                turn_id=len(session.history),
                payload={"mode": mode.value, "reason": mode_reason},
            ),
        )
        prog_result = await run_programmatic_turn(
            session_id=session_id,
            user_message=user_message,
            llm=llm,
            tool_client=tool_client,
            registry=registry,
            graph=graph,
            audit=audit,
        )
        yield TurnCompleted(iteration=0, result=prog_result)
        return

    user_turn = Turn(
        turn_id=len(session.history),
        role="user",
        content=user_message,
    )
    session = await graph.add_turn(session_id, user_turn)

    await audit.write(
        Event(
            event_type=EventType.MODE_SELECTED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={"mode": mode.value, "reason": mode_reason},
        ),
    )

    # Build deterministic LLM context with session state, tool hints, recent decisions
    tool_descriptions = build_tool_descriptions(registry, mode, session)
    recent_events = await audit.tail(limit=40)

    # Create tool registry dict for context builder
    tool_registry_dict = {tool.name: registry.get(tool.name) for tool in registry.list()}

    # Surface available sandbox regions to the agent (Phase A: agent
    # context plumbing for the Podman provider). Empty/None when no
    # actuator is wired so the system prompt skips the section.
    sandbox_summary: str | None = None
    pc = tool_client.policy_context
    if pc is not None and pc.sandbox_actuator is not None:
        actuator = pc.sandbox_actuator
        # Best-effort: actuator may not expose its specs (e.g. demo).
        specs = getattr(actuator, "_specs", None)
        if specs:
            lines = ["Available disposable regions:"]
            for spec_id, spec in specs.items():
                net = getattr(spec, "network", "?")
                img = getattr(spec, "image", "?")
                lines.append(f"  - {spec_id}: image={img}, network={net}")
            sandbox_summary = "\n".join(lines)
        else:
            sandbox_summary = "A SandboxActuator is wired (provider details unavailable)."

    llm_context = build_llm_context(
        session,
        tool_descriptions,
        tool_registry_dict,
        recent_events,
        max_recent_decisions=10,
        sandbox_summary=sandbox_summary,
    )

    # Audit the context for replay purposes
    await audit.write(
        Event(
            event_type=EventType.LLM_CONTEXT_ASSEMBLED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={
                "context_hash": llm_context.context_hash,
                "n_tools": llm_context.n_tools,
                "n_recent_decisions": llm_context.n_recent_decisions,
            },
        ),
    )

    # Use the enriched context as system prompt
    messages: list[Message] = [Message(role=Role.SYSTEM, content=llm_context.system_prompt)]
    for turn in session.history:
        message = _turn_to_message(turn)
        if message is not None:
            messages.append(message)

    # Defense-in-depth: if visible_tools is empty, the LLM has no
    # tool_use options at all. Without explicit notice, models tend
    # to "be helpful" by writing code-block tool calls as text — which
    # bypasses the runtime entirely. Tell the LLM, in plain language,
    # that the list is empty and what the user has to do to fix it.
    if not tool_descriptions:
        messages.append(
            Message(
                role=Role.SYSTEM,
                content=(
                    "NOTICE: this session has no tool capabilities. "
                    "Your tool list is empty for this turn. You CANNOT "
                    "call any tools. Do not write tool-call code blocks "
                    "as text — that does nothing. Respond to the user "
                    "by telling them: 'I have no tools available in this "
                    "session. Run `/grant <KIND> <pattern>` to grant me "
                    "one (e.g. `/grant SEND_EMAIL recipient@example.com "
                    "--one-shot`).' Then stop."
                ),
            ),
        )
    reverse_map: dict[str, str] = {}
    if session.tool_aliasing:
        visible = visible_tools(registry, session, mode)
        reverse_map = build_reverse_map(session.id, [t.name for t in visible])
    tool_outcomes: list[ToolCallOutcome] = []

    iteration = 0
    last_response: LLMResponse | None = None
    while iteration < max_iterations:
        iteration += 1
        # Issue #21 — yield IterationStarted before any work. Lets the
        # REPL render "iter N/max" indicators in real time.
        yield IterationStarted(iteration=iteration)
        await audit.write(
            Event(
                event_type=EventType.LLM_REQUEST_SENT,
                session_id=session_id,
                turn_id=len(session.history),
                step_id=iteration,
                payload={
                    "n_messages": len(messages),
                    "n_tools": len(tool_descriptions),
                },
            ),
        )
        yield LLMRequestSent(
            iteration=iteration,
            n_messages=len(messages),
            n_tools=len(tool_descriptions),
        )

        response = await llm.respond(messages, tool_descriptions)
        last_response = response

        await audit.write(
            Event(
                event_type=EventType.LLM_RESPONSE_RECEIVED,
                session_id=session_id,
                turn_id=len(session.history),
                step_id=iteration,
                payload={
                    "content_length": len(response.content),
                    "n_tool_calls": len(response.tool_calls),
                    "finish_reason": response.finish_reason.value,
                    "model": response.model,
                },
            ),
        )
        yield LLMResponseReceived(
            iteration=iteration,
            content_length=len(response.content),
            n_tool_calls=len(response.tool_calls),
            finish_reason=response.finish_reason.value,
            model=response.model,
        )

        if not response.tool_calls:
            agent_turn = Turn(
                turn_id=len(session.history),
                role="agent",
                content=response.content,
            )
            await graph.add_turn(session_id, agent_turn)
            yield TurnCompleted(
                iteration=iteration,
                result=AgentTurnResult(
                    content=response.content,
                    iterations=iteration,
                    finish_reason=response.finish_reason,
                    tool_outcomes=tuple(tool_outcomes),
                ),
            )
            return

        messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            ),
        )

        for tool_call in response.tool_calls:
            # Reverse-map alias → canonical name. If the LLM produces a
            # token that doesn't match any visible tool's alias, the
            # name passes through untouched and ToolNotFoundError fires
            # below with the unmatched string in the message.
            real_name = reverse_map.get(tool_call.name, tool_call.name)
            yield ToolDispatched(
                iteration=iteration,
                tool_name=real_name,
                tool_args=tool_call.args,
            )
            try:
                outcome = await tool_client.call_tool(
                    session_id,
                    real_name,
                    tool_call.args,
                )
            except ToolNotFoundError:
                outcome = ToolCallOutcome(
                    decision=Decision.DENY,
                    reason=f"tool not found: {tool_call.name}",
                    tool_name=tool_call.name,
                    tool_args=tool_call.args,
                )

            tool_outcomes.append(outcome)
            yield ToolReturned(iteration=iteration, outcome=outcome)
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=_format_outcome(outcome),
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                ),
            )

    # Loop exceeded max_iterations. Yield the terminal TurnInterrupted
    # so streaming consumers can render the partial state; run_turn
    # wrapper detects this and raises AgentLoopExceededError for
    # backwards compatibility.
    partial_content = last_response.content if last_response else ""
    yield TurnInterrupted(
        iteration=iteration,
        reason="max_iterations",
        partial_content=partial_content,
        partial_outcomes=tuple(tool_outcomes),
    )
