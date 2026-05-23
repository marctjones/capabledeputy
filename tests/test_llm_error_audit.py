"""Tests for LLM error audit + context-window guardrail (Issue #36).

Verifies:
- When llm.respond() raises, an LLM_ERROR audit event is written
  before the exception propagates / TurnInterrupted yields
- When estimated context exceeds 80%, an LLM_CONTEXT_WARNING audit
  event fires AND a system notice is injected into messages
- When estimated context exceeds 90%, TurnInterrupted(reason='context_overflow')
  is yielded as the terminal event
- The estimator's chars/4 heuristic
- Backwards compat: normal turns audit cleanly with no spurious LLM_ERROR
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from capabledeputy.agent.events import (
    LLMRequestSent,
    TurnCompleted,
    TurnInterrupted,
)
from capabledeputy.agent.loop import (
    ContextOverflowError,
    _MODEL_CONTEXT_WINDOWS,
    _context_window_for,
    _estimate_message_tokens,
    run_turn_streaming,
)
from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.types import FinishReason, LLMResponse, Message, Role
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.registry import ToolRegistry


def _read_audit_events(audit_path: Path) -> list[dict]:
    events = []
    if not audit_path.exists():
        return events
    with audit_path.open() as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _events_of_type(events: list[dict], et: str) -> list[dict]:
    return [e for e in events if e.get("event_type") == et]


@pytest.fixture
async def session_setup(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditWriter(audit_path)
    graph = SessionGraph(audit=audit)
    session = await graph.new(intent="test-llm-error")
    registry = ToolRegistry()
    tool_client = LabeledToolClient(registry=registry, graph=graph, audit=audit)
    return session.id, graph, audit, audit_path, registry, tool_client


# --- context size estimator ------------------------------------------------


def test_estimate_message_tokens_heuristic() -> None:
    """chars/4 heuristic for context size."""
    msgs = [
        Message(role=Role.USER, content="hello world"),  # 11 chars → 2 tokens
        Message(role=Role.ASSISTANT, content="hi there"),  # 8 chars → 2 tokens
    ]
    est = _estimate_message_tokens(msgs)
    # 19 chars / 4 = 4 tokens
    assert est == 4


def test_estimate_message_tokens_empty() -> None:
    assert _estimate_message_tokens([]) == 0


def test_context_window_known_model() -> None:
    assert _context_window_for("claude-haiku-4-5-20251001") == 200_000
    assert _context_window_for("gpt-4") == 8192


def test_context_window_unknown_model_defaults() -> None:
    """Unknown models fall back to a conservative DEFAULT_CONTEXT_WINDOW."""
    from capabledeputy.agent.loop import DEFAULT_CONTEXT_WINDOW

    assert _context_window_for("never-heard-of-this-model") == DEFAULT_CONTEXT_WINDOW
    assert _context_window_for(None) == DEFAULT_CONTEXT_WINDOW


# --- LLM error audit ------------------------------------------------------


class _RaisingLLM:
    """LLM client that raises on respond()."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def respond(self, messages, tools) -> LLMResponse:
        raise self._exc


async def test_llm_exception_audits_llm_error_event(session_setup) -> None:
    sid, graph, audit, audit_path, registry, tool_client = session_setup
    llm = _RaisingLLM(RuntimeError("simulated provider 503"))

    events_yielded = []
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        events_yielded.append(evt)

    # The terminal event should be TurnInterrupted with the llm_error reason
    assert isinstance(events_yielded[-1], TurnInterrupted)
    assert events_yielded[-1].reason.startswith("llm_error:")
    assert "RuntimeError" in events_yielded[-1].reason

    # The audit log should have an LLM_ERROR event with the exception details
    audit_events = _read_audit_events(audit_path)
    llm_errors = _events_of_type(audit_events, EventType.LLM_ERROR.value)
    assert len(llm_errors) == 1
    payload = llm_errors[0].get("payload", {})
    assert payload.get("error_type") == "RuntimeError"
    assert "simulated provider 503" in (payload.get("message") or "")


# --- context window guardrail ---------------------------------------------


class _BigContextStubLLM:
    """LLM client that succeeds normally — used to test that context-size
    preflight fires BEFORE the actual call. The handler tracks how many
    times respond() was actually invoked."""

    def __init__(self) -> None:
        self.call_count = 0

    async def respond(self, messages, tools) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            content="ok",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
            model="claude-haiku-4-5-20251001",
        )


async def test_hard_limit_yields_context_overflow(
    session_setup, monkeypatch
) -> None:
    """When estimate >= 90% of the window, the loop yields
    TurnInterrupted(reason='context_overflow') and does NOT call the LLM."""
    sid, graph, audit, audit_path, registry, tool_client = session_setup

    # Force the loop to think we're already in overflow territory by
    # giving it a tiny window. The estimator on a "hi" message returns
    # ~0 tokens; if the window is 0 the ratio is undefined. Better:
    # patch _context_window_for to return 1 so any context overflows.
    from capabledeputy.agent import loop as loop_mod

    monkeypatch.setattr(loop_mod, "_context_window_for", lambda model: 1)

    llm = _BigContextStubLLM()

    terminal = None
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=llm,
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        terminal = evt

    # The terminal event is TurnInterrupted(reason='context_overflow')
    assert isinstance(terminal, TurnInterrupted)
    assert terminal.reason == "context_overflow"

    # LLM was NOT called — guardrail fires before the call
    assert llm.call_count == 0

    # Audit has the LLM_ERROR event (context overflow is recorded as such)
    audit_events = _read_audit_events(audit_path)
    llm_errors = _events_of_type(audit_events, EventType.LLM_ERROR.value)
    assert len(llm_errors) == 1
    assert llm_errors[0]["payload"]["error_type"] == "ContextOverflowError"


# --- normal turn (no false positives) -------------------------------------


class _StubLLM:
    """Normal LLM that returns a clean response with no tool calls."""

    async def respond(self, messages, tools) -> LLMResponse:
        return LLMResponse(
            content="done",
            tool_calls=[],
            finish_reason=FinishReason.STOP,
            model="claude-haiku-4-5-20251001",
        )


async def test_normal_turn_audits_no_llm_error(session_setup) -> None:
    """A clean turn produces NO LLM_ERROR or LLM_CONTEXT_WARNING events.
    Guardrails must not fire spuriously."""
    sid, graph, audit, audit_path, registry, tool_client = session_setup

    terminal = None
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=_StubLLM(),
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        terminal = evt

    assert isinstance(terminal, TurnCompleted)
    audit_events = _read_audit_events(audit_path)
    assert len(_events_of_type(audit_events, EventType.LLM_ERROR.value)) == 0
    assert len(_events_of_type(audit_events, EventType.LLM_CONTEXT_WARNING.value)) == 0


# --- soft warning ---------------------------------------------------------


async def test_soft_warning_fires_and_injects_system_notice(
    session_setup, monkeypatch
) -> None:
    """When estimate is between 80% and 90% of window, LLM_CONTEXT_WARNING
    fires AND a system message gets injected for the LLM."""
    sid, graph, audit, audit_path, registry, tool_client = session_setup
    from capabledeputy.agent import loop as loop_mod

    # Stub the window to a small value so our "hi" message exceeds 80%
    # but not 90%. "hi" is 2 chars → 0 tokens via chars/4.
    # Use a window where 0 tokens / window >= 0.80 — set window to 0 hits div-by-zero.
    # Better: stub the estimator to return a known value.
    monkeypatch.setattr(loop_mod, "_context_window_for", lambda m: 100)
    monkeypatch.setattr(loop_mod, "_estimate_message_tokens", lambda msgs: 85)

    received_messages: list[list[Message]] = []

    class _CaptureLLM:
        async def respond(self, messages, tools):
            received_messages.append(list(messages))
            return LLMResponse(
                content="acknowledged your notice",
                tool_calls=[],
                finish_reason=FinishReason.STOP,
                model="claude-haiku-4-5-20251001",
            )

    terminal = None
    async for evt in run_turn_streaming(
        session_id=sid,
        user_message="hi",
        llm=_CaptureLLM(),
        tool_client=tool_client,
        registry=registry,
        graph=graph,
        audit=audit,
    ):
        terminal = evt

    # Turn completed cleanly (soft warning doesn't interrupt)
    assert isinstance(terminal, TurnCompleted)

    # Warning event audited
    audit_events = _read_audit_events(audit_path)
    warnings = _events_of_type(audit_events, EventType.LLM_CONTEXT_WARNING.value)
    assert len(warnings) == 1
    payload = warnings[0]["payload"]
    assert payload["ratio"] == 0.85
    assert payload["context_tokens_estimate"] == 85
    assert payload["context_window"] == 100

    # System notice was injected into the messages passed to the LLM
    assert len(received_messages) == 1
    msgs = received_messages[0]
    system_notices = [m for m in msgs if m.role == Role.SYSTEM and "NOTICE" in (m.content or "")]
    assert len(system_notices) >= 1
    assert "approaching" in system_notices[-1].content
    assert "/spawn" in system_notices[-1].content
