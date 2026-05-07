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
from capabledeputy.tools.client import LabeledToolClient, ToolCallOutcome
from capabledeputy.tools.registry import ToolNotFoundError, ToolRegistry

DEFAULT_SYSTEM_PROMPT = (
    "You are CapableDeputy, a structurally secure personal AI assistant.\n"
    "You operate inside a runtime that gates every tool call by capability "
    "and information-flow policy. If a tool call is denied, you will see "
    "an error explaining why; do not retry the same denied call.\n"
    "When you have completed the user's task, respond with a final answer "
    "and no tool calls."
)


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
    if session is not None:
        tools = visible_tools(registry, session, mode)
    else:
        tools = filter_tools_for_mode(registry.list(), mode)
    return [
        ToolDescription(
            name=t.name,
            description=t.description,
            parameters_schema=t.parameters_schema,
        )
        for t in tools
    ]


def _format_outcome(outcome: ToolCallOutcome) -> str:
    if outcome.error is not None:
        return f"tool error: {outcome.error}"
    if outcome.decision == Decision.DENY:
        return f"policy denied: {outcome.rule or 'no_rule'}: {outcome.reason or ''}"
    if outcome.decision == Decision.REQUIRE_APPROVAL:
        return f"approval required by rule {outcome.rule}: {outcome.reason or 'queued'}"
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
    max_iterations: int = 10,
) -> AgentTurnResult:
    session = graph.get(session_id)
    if session.is_terminal:
        raise SessionStateError(
            f"cannot send to terminal session {session_id} (status={session.status})",
        )

    user_turn = Turn(
        turn_id=len(session.history),
        role="user",
        content=user_message,
    )
    session = await graph.add_turn(session_id, user_turn)

    mode, mode_reason = select_mode(session.label_set, registry)
    await audit.write(
        Event(
            event_type=EventType.MODE_SELECTED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={"mode": mode.value, "reason": mode_reason},
        ),
    )

    messages: list[Message] = [Message(role=Role.SYSTEM, content=system_prompt)]
    for turn in session.history:
        message = _turn_to_message(turn)
        if message is not None:
            messages.append(message)

    tool_descriptions = build_tool_descriptions(registry, mode, session)
    tool_outcomes: list[ToolCallOutcome] = []

    iteration = 0
    last_response: LLMResponse | None = None
    while iteration < max_iterations:
        iteration += 1
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

        if not response.tool_calls:
            agent_turn = Turn(
                turn_id=len(session.history),
                role="agent",
                content=response.content,
            )
            await graph.add_turn(session_id, agent_turn)
            return AgentTurnResult(
                content=response.content,
                iterations=iteration,
                finish_reason=response.finish_reason,
                tool_outcomes=tuple(tool_outcomes),
            )

        messages.append(
            Message(
                role=Role.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            ),
        )

        for tool_call in response.tool_calls:
            try:
                outcome = await tool_client.call_tool(
                    session_id,
                    tool_call.name,
                    tool_call.args,
                )
            except ToolNotFoundError:
                outcome = ToolCallOutcome(
                    decision=Decision.DENY,
                    reason=f"tool not found: {tool_call.name}",
                )

            tool_outcomes.append(outcome)
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=_format_outcome(outcome),
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                ),
            )

    raise AgentLoopExceededError(
        f"agent loop exceeded {max_iterations} iterations "
        f"(last finish_reason: {last_response.finish_reason if last_response else 'none'})",
    )
