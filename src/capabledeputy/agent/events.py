"""Turn-event types for the async-generator agent loop (Issue #21).

`run_turn_streaming` yields `TurnEvent`s at every meaningful step of
an agent turn. Consumers (the chat REPL, the rich Textual surface,
audit/tracing tools) can `for await` over the events for streaming
UX (Issue #22), apply backpressure naturally, or cancel mid-turn
via the surrounding anyio task group (Issues #23, #31).

Design notes
------------

- Events are immutable dataclasses tagged with a string `kind`.
  String tags (vs. enums) keep wire serialization trivial.
- Every event carries the `iteration` it belongs to (0-indexed).
  Pre-loop events use iteration=0.
- `completed` is always the final event for a turn that finishes
  cleanly. It carries the full `AgentTurnResult` so callers that
  don't care about streaming can wrap and consume.
- `interrupted` and `error` are terminal events for non-clean exits.
- `policy_decision` carries the chokepoint decision for each tool
  call — useful for the rich surface's compartment-state indicator.

Compatibility
-------------

`run_turn` (the existing public API) is rewritten as a thin wrapper
that consumes the generator and synthesizes the final result. All
existing callers (daemon RPC, tests) continue to work unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from capabledeputy.tools.client import ToolCallOutcome


@dataclass(frozen=True)
class TurnEventBase:
    """Base for all turn events. Always carries the iteration index."""

    iteration: int


@dataclass(frozen=True)
class IterationStarted(TurnEventBase):
    """Fires at the start of each iteration of the agent loop. Useful
    for progress UIs to show "iter 2/50" and similar."""

    kind: str = "iteration_started"


@dataclass(frozen=True)
class LLMRequestSent(TurnEventBase):
    """LLM call is about to be made. Carries shape stats so the REPL
    can render 'Thinking...' style indicators."""

    n_messages: int
    n_tools: int
    kind: str = "llm_request_sent"


@dataclass(frozen=True)
class LLMTokenReceived(TurnEventBase):
    """Partial LLM token / content chunk. Only emitted when the
    underlying LLM client supports streaming. Consumers concatenate
    these for live token-by-token rendering (Issue #22)."""

    text: str
    kind: str = "llm_token"


@dataclass(frozen=True)
class LLMResponseReceived(TurnEventBase):
    """Complete LLM response arrived (after streaming if applicable).
    Carries the finalized content + tool-call metadata."""

    content_length: int
    n_tool_calls: int
    finish_reason: str
    model: str
    kind: str = "llm_response_received"


@dataclass(frozen=True)
class ToolDispatched(TurnEventBase):
    """A tool call is about to be dispatched through the chokepoint."""

    tool_name: str
    tool_args: dict[str, Any] = field(default_factory=dict)
    kind: str = "tool_dispatched"


@dataclass(frozen=True)
class ToolReturned(TurnEventBase):
    """A tool call has returned. The outcome carries the policy
    decision + result + any synthesized recovery_steps."""

    outcome: ToolCallOutcome
    kind: str = "tool_returned"


@dataclass(frozen=True)
class TurnCompleted(TurnEventBase):
    """Terminal event for a clean turn. The result is the same
    `AgentTurnResult` that `run_turn` (the non-streaming wrapper)
    returns. Callers that don't want streaming consume only this
    event."""

    # The result type is `AgentTurnResult` but importing it here
    # would create a circular: events.py is imported by loop.py.
    # Annotated as Any so callers cast.
    result: Any  # AgentTurnResult
    kind: str = "completed"


@dataclass(frozen=True)
class TurnInterrupted(TurnEventBase):
    """Turn was cancelled mid-flight. Reasons include 'ctrl-c'
    (#23), 'surface-disconnect' (#31), 'heartbeat-timeout' (#32).
    Partial state (tool_outcomes, content_so_far) is preserved."""

    reason: str
    partial_content: str = ""
    partial_outcomes: tuple[ToolCallOutcome, ...] = field(default_factory=tuple)
    kind: str = "interrupted"


@dataclass(frozen=True)
class TurnErrored(TurnEventBase):
    """Uncaught exception during the turn. Distinguishes from
    `interrupted` (which is a deliberate cancel). The message and
    type are surfaced; full traceback stays in the audit log."""

    error_type: str
    message: str
    kind: str = "error"


# Tagged union for type-safe consumers
TurnEvent = (
    IterationStarted
    | LLMRequestSent
    | LLMTokenReceived
    | LLMResponseReceived
    | ToolDispatched
    | ToolReturned
    | TurnCompleted
    | TurnInterrupted
    | TurnErrored
)


def event_to_dict(evt: TurnEvent) -> dict[str, Any]:
    """Serialize an event to a JSON-friendly dict for the wire.
    Used by the daemon's streaming RPC to ship events to remote
    clients (chat REPL, rich surface)."""
    base: dict[str, Any] = {"kind": evt.kind, "iteration": evt.iteration}
    if isinstance(evt, LLMRequestSent):
        base["n_messages"] = evt.n_messages
        base["n_tools"] = evt.n_tools
    elif isinstance(evt, LLMTokenReceived):
        base["text"] = evt.text
    elif isinstance(evt, LLMResponseReceived):
        base["content_length"] = evt.content_length
        base["n_tool_calls"] = evt.n_tool_calls
        base["finish_reason"] = evt.finish_reason
        base["model"] = evt.model
    elif isinstance(evt, ToolDispatched):
        base["tool_name"] = evt.tool_name
        base["tool_args"] = evt.tool_args
    elif isinstance(evt, ToolReturned):
        # ToolCallOutcome is dict-serialized by the daemon's
        # _outcome_to_dict; here we keep it as-is. The RPC layer
        # converts via _outcome_to_dict before shipping.
        base["outcome"] = evt.outcome
    elif isinstance(evt, TurnCompleted):
        base["result"] = evt.result
    elif isinstance(evt, TurnInterrupted):
        base["reason"] = evt.reason
        base["partial_content"] = evt.partial_content
        base["partial_outcomes"] = list(evt.partial_outcomes)
    elif isinstance(evt, TurnErrored):
        base["error_type"] = evt.error_type
        base["message"] = evt.message
    return base
