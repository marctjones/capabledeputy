"""E2E smoke test for the chat REPL UX pipeline.

Exercises the full path:
  build App with FakeLLMClient
  → wire daemon-side handlers (session.send, agent loop, audit)
  → drive one turn via the canonical `session.send` RPC handler
  → pipe the returned `result` dict through `_render_turn`
  → capture the rendered output
  → assert every new UX surface lands

What this covers that the unit tests don't:
  - The chat REPL's render functions receive shapes the daemon
    actually produces, not hand-rolled mocks. If `_outcome_to_dict`
    in agent_handlers ever drops a field that `_render_outcomes_table`
    expects, this test catches it.
  - The agent loop's audit-event payload (now including
    `context_window`, which the toolbar's ctx segment reads) is
    asserted end-to-end.
  - Tool-card formatters resolve correctly against the actual
    output shape of `memory.write`.

What this DOESN'T cover:
  - Unix-socket round-trip — handlers are called in-process. The
    socket layer is already covered by daemon-server tests.
  - LLM behavior — FakeLLMClient replays a canned response sequence.
    No real model is invoked; tests are deterministic and free.
  - Interactive keybindings — prompt-toolkit needs a real TTY. The
    streaming consumer's behavior on Ctrl-C is covered by the
    cancel-RPC unit tests in test_agent_handlers.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.cli.chat import (
    _render_outcomes_table,
    _render_turn,
    console,
)
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind


def _read_audit(audit_path: Path) -> list[dict]:
    events = []
    if not audit_path.exists():
        return events
    for line in audit_path.read_text().splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


@pytest.fixture
async def memory_write_turn(tmp_path: Path):
    """One real agent turn driven through the daemon handler.

    Pre-seeds a session with WRITE_FS so `memory.write` is allowed,
    feeds the agent loop a canned two-message LLM response sequence
    (tool call → final answer), runs `session.send` via the real
    handler, and yields the resulting dict + audit path.
    """
    fake = FakeLLMClient(
        [
            LLMResponse(
                content="I'll save that for you.",
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="memory.write",
                        args={"key": "shopping_list", "value": "eggs, milk, bread"},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="Saved. Your shopping list now has eggs, milk, and bread.",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=fake,
    )
    await app.startup()

    session = await app.graph.new(intent="e2e-smoke")
    cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    # Direct insert mirrors how existing test_agent_handlers tests
    # seed capabilities (graph.grant_capability would also work but
    # this path is what the other tests use, for consistency).
    app.graph._sessions[session.id] = replace(
        app.graph._sessions[session.id],
        capability_set=frozenset({cap}),
    )

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(session.id), "message": "remember my shopping list: eggs, milk, bread"},
    )
    return {
        "result": result,
        "session_id": str(session.id),
        "audit_path": tmp_path / "audit.jsonl",
        "app": app,
    }


async def test_e2e_speaker_prefix_appears_in_rendered_turn(memory_write_turn) -> None:
    """The `● agent` prefix lands in the rendered turn output."""
    with console.capture() as cap:
        _render_turn(memory_write_turn["result"])
    out = cap.get()
    assert "●" in out
    assert "agent" in out


async def test_e2e_tool_card_renders_with_memory_icon(memory_write_turn) -> None:
    """The memory.write call renders as a card with the 🧠 icon, the
    tool name, an args summary, and the key from the args."""
    with console.capture() as cap:
        _render_outcomes_table(memory_write_turn["result"]["tool_outcomes"])
    out = cap.get()
    assert "🧠" in out
    assert "memory.write" in out
    assert "shopping_list" in out  # the `key=` arg appears in the summary


async def test_e2e_turn_header_includes_iteration_count(memory_write_turn) -> None:
    """`turn N · 2 iters · 1 tool call` should appear in the header
    line for a tool-using turn."""
    with console.capture() as cap:
        _render_turn(memory_write_turn["result"])
    out = cap.get()
    # The turn took 2 iterations (tool call → final response)
    assert "2 iters" in out
    assert "1 tool call" in out


async def test_e2e_audit_event_carries_context_window(memory_write_turn) -> None:
    """The `llm.request_sent` audit event now includes
    `context_window` — the toolbar's ctx segment reads this. Asserts
    the daemon-side plumbing wrote the field correctly."""
    events = _read_audit(memory_write_turn["audit_path"])
    request_events = [e for e in events if e.get("event_type") == "llm.request_sent"]
    assert request_events, "expected at least one llm.request_sent event"
    payload = request_events[0]["payload"]
    assert "context_tokens_estimate" in payload
    assert "context_window" in payload
    assert payload["context_window"] > 0
    # FakeLLMClient default model is unset, so we fall through to the
    # default window. Just verify the field is populated, not a
    # specific value.


async def test_e2e_audit_event_carries_provider_usage(memory_write_turn) -> None:
    """The `llm.response_received` audit event now carries
    `prompt_tokens` / `completion_tokens` — the toolbar's usage
    segment sums these for session + month-to-date. FakeLLMClient
    reports usage={}, so the values are 0, but the keys MUST be
    present so the consumer's int() coercion is exercised end-to-end
    and the drift detector catches a regression."""
    events = _read_audit(memory_write_turn["audit_path"])
    response_events = [
        e for e in events if e.get("event_type") == "llm.response_received"
    ]
    assert response_events, "expected at least one llm.response_received event"
    payload = response_events[0]["payload"]
    assert "prompt_tokens" in payload
    assert "completion_tokens" in payload
    # Fake client doesn't supply usage — values default to 0 but the
    # plumbing is still exercised.
    assert payload["prompt_tokens"] == 0
    assert payload["completion_tokens"] == 0


async def test_e2e_audit_event_sequence_for_tool_turn(memory_write_turn) -> None:
    """Verifies the canonical audit-event sequence for a one-tool
    turn: request → response → policy → dispatch → returned →
    request → response → (no more tool calls)."""
    events = _read_audit(memory_write_turn["audit_path"])
    sid = memory_write_turn["session_id"]
    types = [e.get("event_type") for e in events if e.get("session_id") == sid]

    # Must contain these in order somewhere in the trace
    expected_subsequence = [
        "llm.request_sent",
        "llm.response_received",
        "policy.decided",
        "tool.dispatched",
        "tool.returned",
        "llm.request_sent",
        "llm.response_received",
    ]
    # Walk the events and check the expected types appear in order
    idx = 0
    for t in types:
        if idx < len(expected_subsequence) and t == expected_subsequence[idx]:
            idx += 1
    assert idx == len(expected_subsequence), (
        f"audit event sequence missing expected pattern; got types {types}"
    )


async def test_e2e_session_send_returns_renderable_shape(memory_write_turn) -> None:
    """Guards against future drift in `_outcome_to_dict`: the result
    dict from session.send must carry every field that `_render_turn`
    and `_render_outcomes_table` read."""
    result = memory_write_turn["result"]
    assert "content" in result
    assert "iterations" in result
    assert "finish_reason" in result
    assert "tool_outcomes" in result
    [outcome] = result["tool_outcomes"]
    # Fields the renderer reads — drift detector
    for key in ("decision", "tool_name", "tool_args", "output", "rule", "error", "labels_added"):
        assert key in outcome, f"outcome dict missing {key!r} that _render_outcomes_table reads"
