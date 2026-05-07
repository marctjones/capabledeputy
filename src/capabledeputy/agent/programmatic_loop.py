"""Programmatic agent loop (DESIGN.md §5.3).

Counterpart to `agent/loop.run_turn` for sessions running in
ExecutionMode.PROGRAMMATIC. The LLM is asked to respond with a single
Python code block describing the entire planned data flow; the harness
parses and executes it through the AST-subset interpreter, gating each
`call(...)` through `LabeledToolClient`.

The contract for the LLM:
  - One code block per turn (```python ... ```).
  - The code uses `call(tool_name, **kwargs)` for every tool dispatch.
  - The code may use a small set of safe builtins (len, str, int, ...).
  - Any forbidden construct (import, class, def, attribute access, ...)
    is rejected at parse time without invoking any tools.

Response shape: same `AgentTurnResult` as the turn-level loop, so
callers and audit tooling don't have to special-case the mode.
"""

from __future__ import annotations

import re
from uuid import UUID

from capabledeputy.agent.loop import AgentTurnResult
from capabledeputy.audit.events import Event, EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.types import FinishReason, Message, Role
from capabledeputy.policy.rules import Decision
from capabledeputy.programmatic import (
    ProgramSyntaxError,
    run_program_against_session,
)
from capabledeputy.programmatic.evaluator import ToolCallRecord
from capabledeputy.session.graph import SessionGraph, SessionStateError
from capabledeputy.session.model import Turn
from capabledeputy.tools.client import LabeledToolClient, ToolCallOutcome
from capabledeputy.tools.registry import ToolRegistry

PROGRAMMATIC_SYSTEM_PROMPT = """\
You are CapableDeputy operating in PROGRAMMATIC MODE.

Instead of calling tools one at a time, you respond with a single
Python program that describes the entire planned data flow. The
harness will parse and execute the program; every `call(...)` runs
through the same policy gate as turn-level mode, and information-flow
labels propagate through every operation in the program.

Strict rules for your program:
  - Use the `call(tool_name, **kwargs)` builtin for every tool call.
  - Only positional/keyword arguments; no **kwargs unpacking.
  - Allowed: assignment to bare names or subscripts, literals, binary
    and boolean and comparison and unary operators, `if/else`,
    `for ... in ...`, `pass`, `break`, `continue`, `return`.
  - Forbidden: import, class, def, lambda, attribute access (no `.`),
    try/except, with, while, comprehensions, generators, decorators.
  - Safe builtins available: len, str, int, float, bool, list, dict,
    tuple, set, min, max, sum, sorted, range, enumerate, zip, abs,
    round, any, all.

Respond with prose explaining what you intend, then a single fenced
code block:

```python
# your program here
```

If you cannot accomplish the task safely, respond with prose only and
no code block; the harness will treat that as a final answer.
"""


_CODE_BLOCK_RE = re.compile(
    r"```(?:python|py|starlark)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def extract_code_block(text: str) -> str | None:
    """Return the first fenced code block's body, or None if none found."""
    match = _CODE_BLOCK_RE.search(text)
    return match.group(1) if match else None


def _format_tool_descriptions_for_prompt(registry: ToolRegistry) -> str:
    lines = ["Available tools (call via `call('tool_name', kwarg=value)`):"]
    for tool in registry.list():
        lines.append(f"  - {tool.name} — {tool.description}")
    return "\n".join(lines)


def _record_to_outcome(record: ToolCallRecord) -> ToolCallOutcome:
    """Project a programmatic ToolCallRecord onto the turn-level
    ToolCallOutcome shape so callers (and the JSON RPC surface) don't
    need to branch on mode.
    """
    return ToolCallOutcome(
        decision=record.decision,
        output={"args": record.args} if record.decision == Decision.ALLOW else None,
        rule=record.rule,
        reason=record.reason,
        labels_added=record.inherent_labels,
    )


async def run_programmatic_turn(
    *,
    session_id: UUID,
    user_message: str,
    llm: LLMClient,
    tool_client: LabeledToolClient,
    registry: ToolRegistry,
    graph: SessionGraph,
    audit: AuditWriter,
    system_prompt: str = PROGRAMMATIC_SYSTEM_PROMPT,
) -> AgentTurnResult:
    """Run one turn in programmatic mode.

    The flow:
      1. Append the user message to session history.
      2. Ask the LLM (no tools provided) for a code block.
      3. If no code block, treat the response as a final answer.
      4. Otherwise parse + execute the program against the session.
      5. Append the LLM response as the agent turn and return.
    """
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

    tool_block = _format_tool_descriptions_for_prompt(registry)
    full_prompt = f"{system_prompt}\n\n{tool_block}"

    messages: list[Message] = [Message(role=Role.SYSTEM, content=full_prompt)]
    for turn in session.history:
        if turn.role == "user":
            messages.append(Message(role=Role.USER, content=turn.content))
        elif turn.role == "agent":
            messages.append(Message(role=Role.ASSISTANT, content=turn.content))

    await audit.write(
        Event(
            event_type=EventType.LLM_REQUEST_SENT,
            session_id=session_id,
            turn_id=len(session.history),
            payload={
                "n_messages": len(messages),
                "mode": "programmatic",
            },
        ),
    )

    response = await llm.respond(messages, [])

    await audit.write(
        Event(
            event_type=EventType.LLM_RESPONSE_RECEIVED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={
                "content_length": len(response.content),
                "finish_reason": response.finish_reason.value,
                "model": response.model,
                "mode": "programmatic",
            },
        ),
    )

    code = extract_code_block(response.content)

    agent_turn = Turn(
        turn_id=len(session.history),
        role="agent",
        content=response.content,
    )
    await graph.add_turn(session_id, agent_turn)

    if code is None:
        # No program → treat as a final natural-language answer.
        return AgentTurnResult(
            content=response.content,
            iterations=1,
            finish_reason=response.finish_reason,
            tool_outcomes=(),
        )

    await audit.write(
        Event(
            event_type=EventType.LLM_RESPONSE_PARSED,
            session_id=session_id,
            turn_id=len(session.history),
            payload={
                "mode": "programmatic",
                "program_length": len(code),
            },
        ),
    )

    try:
        result = await run_program_against_session(
            code,
            session_id=session_id,
            tool_client=tool_client,
            graph=graph,
            registry=registry,
            audit=audit,
        )
    except ProgramSyntaxError as e:
        return AgentTurnResult(
            content=f"{response.content}\n\n[program rejected: {e}]",
            iterations=1,
            finish_reason=FinishReason.STOP,
            tool_outcomes=(),
        )

    outcomes = tuple(_record_to_outcome(r) for r in result.tool_calls)

    final_content = response.content
    if result.error is not None:
        final_content = f"{response.content}\n\n[execution halted: {result.error}]"
    elif result.return_value is not None:
        final_content = f"{response.content}\n\n[program returned: {result.return_value.raw!r}]"

    return AgentTurnResult(
        content=final_content,
        iterations=1,
        finish_reason=FinishReason.STOP,
        tool_outcomes=outcomes,
    )
