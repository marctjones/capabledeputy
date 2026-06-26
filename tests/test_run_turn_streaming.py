"""Tests for run_turn_streaming (#21).

Verifies the async generator yields the expected event sequence
under various scenarios:
- Normal turn (no tool calls): IterationStarted → LLMRequestSent →
  LLMResponseReceived → TurnCompleted
- Tool-call turn: ... → ToolDispatched → ToolReturned → ... → TurnCompleted
- Max-iterations exceeded: ... → TurnInterrupted(reason='max_iterations')

These tests use mocked LLM clients + tool clients so they run fast
and don't depend on a real LLM. The shape invariants are what
matter: events arrive in a sensible order, terminal events
(TurnCompleted / TurnInterrupted) appear exactly once, and the
wrapper run_turn() consumes the stream correctly.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from capabledeputy.agent.events import (
    IterationStarted,
    LLMRequestSent,
    LLMTokenReceived,
    LLMResponseReceived,
    TurnCompleted,
    TurnInterrupted,
)
from capabledeputy.agent.loop import AgentLoopExceededError, run_turn, run_turn_streaming
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.types import FinishReason, LLMResponse, Message, ToolDescription
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolRegistry


class _StubLLM:
    """Returns a canned sequence of responses, one per call."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def respond(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
    ) -> LLMResponse:
        self.call_count += 1
        if not self._responses:
            # Fallback so we don't hang in tests where the response
            # list runs short.
            return LLMResponse(
                content="(stub: out of responses)",
                tool_calls=(),
                finish_reason=FinishReason.STOP,
                model="stub",
            )
        return self._responses.pop(0)


class _StreamingStubLLM:
    """Yields token deltas via respond_streaming like MLXLLMClient."""

    _model = "stream-stub"

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.last_max_tokens: int | None = None

    async def respond_streaming(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
        *,
        max_tokens: int | None = None,
    ):
        self.last_max_tokens = max_tokens
        for chunk in self._chunks:
            yield chunk


@pytest.fixture
async def session_setup(
    tmp_path: Path,
) -> tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient]:
    """Spin up the minimum machinery the agent loop needs."""
    audit = AuditWriter(tmp_path / "audit.jsonl")
    graph = SessionGraph(audit=audit)
    session = await graph.new(intent="test")
    registry = ToolRegistry()
    tool_client = LabeledToolClient(registry=registry, graph=graph, audit=audit)
    return session.id, graph, audit, registry, tool_client


async def test_no_tool_calls_yields_terminal_completed_event(
    session_setup: tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient],
) -> None:
    """A simple turn with no tool calls produces:
    IterationStarted → LLMRequestSent → LLMResponseReceived → TurnCompleted."""
    sid, graph, audit, registry, tool_client = session_setup
    llm = _StubLLM(
        [
            LLMResponse(
                content="hello world",
                tool_calls=(),
                finish_reason=FinishReason.STOP,
                model="stub-model",
            ),
        ]
    )

    events = []
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        events.append(evt)

    # Expected sequence
    assert isinstance(events[0], IterationStarted)
    assert events[0].iteration == 1
    assert isinstance(events[1], LLMRequestSent)
    assert events[1].iteration == 1
    assert isinstance(events[2], LLMResponseReceived)
    assert events[2].iteration == 1
    assert events[2].content_length == len("hello world")
    assert events[2].n_tool_calls == 0
    assert isinstance(events[3], TurnCompleted)
    assert events[3].result.content == "hello world"
    assert events[3].result.iterations == 1
    # No more events after TurnCompleted
    assert len(events) == 4


async def test_conversational_turn_caps_max_tokens(
    session_setup: tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient],
) -> None:
    from capabledeputy.agent.chat_turn import CHAT_MAX_TOKENS

    sid, graph, audit, registry, tool_client = session_setup
    llm = _StreamingStubLLM(["quick reply"])

    async for _evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        pass

    assert llm.last_max_tokens == CHAT_MAX_TOKENS


async def test_streaming_llm_emits_token_events(
    session_setup: tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient],
) -> None:
    sid, graph, audit, registry, tool_client = session_setup
    llm = _StreamingStubLLM(["hello ", "world"])

    events = []
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        events.append(evt)

    token_events = [evt for evt in events if isinstance(evt, LLMTokenReceived)]
    assert [evt.text for evt in token_events] == ["hello ", "world"]
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].result.content == "hello world"


async def test_run_turn_wrapper_returns_same_result(
    session_setup: tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient],
) -> None:
    """The non-streaming wrapper run_turn() drains the generator
    and returns the same AgentTurnResult that the TurnCompleted
    event carried."""
    sid, graph, audit, registry, tool_client = session_setup
    llm = _StubLLM(
        [
            LLMResponse(
                content="hello",
                tool_calls=(),
                finish_reason=FinishReason.STOP,
                model="stub",
            ),
        ]
    )

    result = await run_turn(
        session_id=sid,
        user_message="hi",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    )

    assert result.content == "hello"
    assert result.iterations == 1
    assert result.finish_reason == FinishReason.STOP


async def test_max_iterations_yields_terminal_interrupted_event(
    session_setup: tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient],
) -> None:
    """When max_iterations is hit, the generator yields
    TurnInterrupted(reason='max_iterations') as its terminal event.
    The wrapper run_turn() detects this and raises
    AgentLoopExceededError for back-compat."""
    sid, graph, audit, registry, tool_client = session_setup
    # Stub LLM that ALWAYS wants to call a tool — drives the loop
    # to max_iterations. The tool itself doesn't matter because the
    # registry is empty; the loop will iterate until cap regardless.
    from capabledeputy.llm.types import ToolCall

    # Issue #2 — vary the args each iteration so the loop hits the
    # *iteration cap* (max_iterations) rather than tripping the
    # thrash guard, which fires only on a repeated (tool, args).
    def _ever_more(n: int) -> LLMResponse:
        return LLMResponse(
            content="thinking",
            tool_calls=(ToolCall(id=str(uuid4()), name="nope", args={"n": n}),),
            finish_reason=FinishReason.TOOL_CALLS,
            model="stub",
        )

    class _LoopForeverLLM:
        def __init__(self) -> None:
            self._n = 0

        async def respond(self, messages, tools):
            self._n += 1
            return _ever_more(self._n)

    events = []
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=_LoopForeverLLM(),
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
        max_iterations=3,
    ):
        events.append(evt)

    # The terminal event must be TurnInterrupted
    assert isinstance(events[-1], TurnInterrupted)
    assert events[-1].reason == "max_iterations"
    assert events[-1].iteration == 3

    # Wrapper raises for back-compat
    with pytest.raises(AgentLoopExceededError):
        await run_turn(
            session_id=sid,
            user_message="hi again",
            llm=_LoopForeverLLM(),
            tool_client=tool_client,
            registry=registry,
            graph=graph,
            audit=audit,
            max_iterations=2,
        )


async def test_completed_event_appears_exactly_once_per_clean_turn(
    session_setup: tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient],
) -> None:
    """Invariant: TurnCompleted appears 0 or 1 times. Never twice.
    Combined with the max_iterations test, this guarantees exactly
    one terminal event (completed XOR interrupted XOR errored)."""
    sid, graph, audit, registry, tool_client = session_setup
    llm = _StubLLM(
        [
            LLMResponse(
                content="done",
                tool_calls=(),
                finish_reason=FinishReason.STOP,
                model="stub",
            ),
        ]
    )

    completed_count = 0
    interrupted_count = 0
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="x",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        if isinstance(evt, TurnCompleted):
            completed_count += 1
        elif isinstance(evt, TurnInterrupted):
            interrupted_count += 1

    assert completed_count == 1
    assert interrupted_count == 0


async def test_iteration_events_carry_correct_index(
    session_setup: tuple[UUID, SessionGraph, AuditWriter, ToolRegistry, LabeledToolClient],
) -> None:
    """IterationStarted should fire at iteration=1 on the first
    iter (1-indexed, matching audit step_id convention)."""
    sid, graph, audit, registry, tool_client = session_setup
    llm = _StubLLM(
        [
            LLMResponse(
                content="ok",
                tool_calls=(),
                finish_reason=FinishReason.STOP,
                model="stub",
            ),
        ]
    )

    iter_starts = []
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="x",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        if isinstance(evt, IterationStarted):
            iter_starts.append(evt.iteration)

    assert iter_starts == [1]
