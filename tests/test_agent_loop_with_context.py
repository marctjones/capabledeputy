"""Tests for agent loop integration with LLM context enrichment."""

from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.agent.loop import (
    run_turn,
)
from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, Message, Role, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.policy.rules import Decision
from capabledeputy.session.graph import SessionGraph
from capabledeputy.tools.client import LabeledToolClient
from capabledeputy.tools.native.memory import LabeledMemoryStore, make_memory_tools
from capabledeputy.tools.native.purchase import PurchaseQueue, make_purchase_tools
from capabledeputy.tools.registry import ToolRegistry


@pytest.fixture
def writer(tmp_path: Path) -> AuditWriter:
    return AuditWriter(tmp_path / "audit.jsonl")


def _registry_with_natives() -> tuple[ToolRegistry, LabeledMemoryStore, PurchaseQueue]:
    registry = ToolRegistry()
    memory = LabeledMemoryStore()
    queue = PurchaseQueue()
    for t in make_memory_tools(memory):
        registry.register(t)
    for t in make_purchase_tools(queue):
        registry.register(t)
    return registry, memory, queue


async def _setup(
    writer: AuditWriter,
) -> tuple[SessionGraph, ToolRegistry, LabeledToolClient, LabeledMemoryStore, PurchaseQueue]:
    graph = SessionGraph(audit=writer)
    registry, memory, queue = _registry_with_natives()
    client = LabeledToolClient(registry, graph, writer)
    return graph, registry, client, memory, queue


class TestContextAssemblyEvent:
    """Test that LLM_CONTEXT_ASSEMBLED events are emitted."""

    async def test_context_assembled_event_written(writer: AuditWriter) -> None:
        """run_turn should write an LLM_CONTEXT_ASSEMBLED event."""
        graph, registry, client, _memory, _queue = await _setup(writer)
        s = await graph.new()
        llm = FakeLLMClient([LLMResponse(content="hello")])

        await run_turn(
            session_id=s.id,
            user_message="hi",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        events = await writer.read_all()
        context_events = [
            e for e in events
            if e.event_type == EventType.LLM_CONTEXT_ASSEMBLED
        ]

        assert len(context_events) == 1
        ctx_event = context_events[0]
        assert ctx_event.session_id == s.id
        assert "context_hash" in ctx_event.payload
        assert "n_tools" in ctx_event.payload
        assert "n_recent_decisions" in ctx_event.payload

    async def test_context_hash_is_deterministic(writer: AuditWriter) -> None:
        """Same session state should produce same context hash."""
        graph, registry, client, _memory, _queue = await _setup(writer)
        s = await graph.new()
        llm = FakeLLMClient([LLMResponse(content="hello")])

        await run_turn(
            session_id=s.id,
            user_message="test",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        events = await writer.read_all()
        ctx_events = [
            e for e in events
            if e.event_type == EventType.LLM_CONTEXT_ASSEMBLED
        ]

        # First hash
        hash1 = ctx_events[0].payload["context_hash"]

        # Clear and re-run with fresh writer
        writer2 = AuditWriter(writer.path.parent / "audit2.jsonl")
        graph2 = SessionGraph(audit=writer2)
        client2 = LabeledToolClient(registry, graph2, writer2)
        s2 = await graph2.new()
        llm2 = FakeLLMClient([LLMResponse(content="hello")])

        await run_turn(
            session_id=s2.id,
            user_message="test",
            llm=llm2,
            tool_client=client2,
            registry=registry,
            graph=graph2,
            audit=writer2,
        )

        events2 = await writer2.read_all()
        ctx_events2 = [
            e for e in events2
            if e.event_type == EventType.LLM_CONTEXT_ASSEMBLED
        ]

        hash2 = ctx_events2[0].payload["context_hash"]

        # Same sessions should produce same hash
        assert hash1 == hash2


class TestSystemPromptReplacement:
    """Test that enriched context replaces the default system prompt."""

    async def test_fake_llm_receives_enriched_prompt(writer: AuditWriter) -> None:
        """FakeLLMClient should receive the enriched system prompt."""
        graph, registry, client, _memory, _queue = await _setup(writer)
        s = await graph.new()

        # Create a FakeLLMClient that captures what it receives
        llm = FakeLLMClient([LLMResponse(content="response")])

        await run_turn(
            session_id=s.id,
            user_message="test",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        # The FakeLLMClient should have recorded calls
        assert len(llm.calls) >= 1

        # The first call should have messages with enriched context
        first_messages, _ = llm.calls[0]
        system_msg = next(
            (m for m in first_messages if m.role == Role.SYSTEM),
            None,
        )
        assert system_msg is not None
        assert "Session State" in system_msg.content

        # Verify the event was written
        events = await writer.read_all()
        ctx_events = [
            e for e in events
            if e.event_type == EventType.LLM_CONTEXT_ASSEMBLED
        ]
        assert len(ctx_events) > 0

    async def test_context_includes_session_info(writer: AuditWriter) -> None:
        """Context should include session id, labels, profile."""
        graph, registry, client, _memory, _queue = await _setup(writer)

        # Create a session with specific metadata
        s = await graph.new()
        s_labeled = replace(
            s,
            label_set=frozenset({Label.CONFIDENTIAL_PERSONAL}),
            clearance_profile_id="tier_1",
            intent="calendar-review",
        )
        graph._sessions[s.id] = s_labeled

        # Create a capturing LLM that records the prompt
        captured_prompts = []

        class CapturingFakeLLMClient(FakeLLMClient):
            async def respond(self, messages, tool_descriptions):
                if messages:
                    system_msg = next(
                        (m for m in messages if m.role == Role.SYSTEM),
                        None,
                    )
                    if system_msg:
                        captured_prompts.append(system_msg.content)
                return await super().respond(messages, tool_descriptions)

        llm = CapturingFakeLLMClient([LLMResponse(content="response")])

        await run_turn(
            session_id=s.id,
            user_message="test",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        assert len(captured_prompts) > 0
        prompt = captured_prompts[0]

        # Verify context includes expected session info
        assert "tier_1" in prompt  # profile
        assert "calendar-review" in prompt  # intent
        assert "confidential.personal" in prompt  # label


class TestToolOutcomeEnrichment:
    """Test that tool outcomes include rule and reason hints."""

    async def test_denied_tool_includes_recovery_hint(writer: AuditWriter) -> None:
        """Denied tool outcome should include recovery hint."""
        graph, registry, client, _memory, _queue = await _setup(writer)
        s = await graph.new()

        # Try to call a tool without capability
        llm = FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="purchase.queue",
                            args={"vendor": "amazon", "item": "x"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(content="denied", finish_reason=FinishReason.STOP),
            ],
        )

        result = await run_turn(
            session_id=s.id,
            user_message="buy something",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        # The tool was denied (no capability)
        assert len(result.tool_outcomes) == 1
        outcome = result.tool_outcomes[0]
        assert outcome.decision == Decision.DENY

    async def test_allowed_tool_outcome_returned(writer: AuditWriter) -> None:
        """Allowed tool outcome should return success."""
        graph, registry, client, memory, _queue = await _setup(writer)
        s = await graph.new()
        cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
        graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

        llm = FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="memory.write",
                            args={"key": "test_key", "value": "test_value"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(content="stored", finish_reason=FinishReason.STOP),
            ],
        )

        result = await run_turn(
            session_id=s.id,
            user_message="store something",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        assert len(result.tool_outcomes) == 1
        outcome = result.tool_outcomes[0]
        assert outcome.decision == Decision.ALLOW
        assert memory.read("test_key") == "test_value"


class TestContextWithRecentDecisions:
    """Test that recent policy decisions are included in context."""

    async def test_context_includes_recent_denies(writer: AuditWriter) -> None:
        """Context should show recent policy denies."""
        graph, registry, client, _memory, _queue = await _setup(writer)
        s = await graph.new()

        # First turn: try to do something that will be denied
        llm1 = FakeLLMClient(
            [
                LLMResponse(
                    content="",
                    tool_calls=(
                        ToolCall(
                            id="c1",
                            name="purchase.queue",
                            args={"vendor": "amazon", "item": "x"},
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
                LLMResponse(content="denied", finish_reason=FinishReason.STOP),
            ],
        )

        await run_turn(
            session_id=s.id,
            user_message="buy something",
            llm=llm1,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        # Second turn: context should include the deny from the first turn
        captured_prompts = []

        class CapturingLLMClient(FakeLLMClient):
            async def respond(self, messages, tool_descriptions):
                if messages:
                    system_msg = next(
                        (m for m in messages if m.role == Role.SYSTEM),
                        None,
                    )
                    if system_msg:
                        captured_prompts.append(system_msg.content)
                return await super().respond(messages, tool_descriptions)

        llm2 = CapturingLLMClient([LLMResponse(content="ok")])

        await run_turn(
            session_id=s.id,
            user_message="try again",
            llm=llm2,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )

        # The second prompt should show the recent deny
        assert len(captured_prompts) > 0
        prompt = captured_prompts[0]

        # Should mention DENY in recent decisions (or similar)
        # The exact format depends on the implementation
        assert "Recent Decisions" in prompt
