from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.agent.loop import (
    AgentLoopExceededError,
    build_tool_descriptions,
    run_turn,
)
from capabledeputy.audit.events import EventType
from capabledeputy.audit.writer import AuditWriter
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
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


async def test_final_answer_no_tools(writer: AuditWriter) -> None:
    graph, registry, client, _memory, _queue = await _setup(writer)
    s = await graph.new()
    llm = FakeLLMClient([LLMResponse(content="hello back")])

    result = await run_turn(
        session_id=s.id,
        user_message="hi",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )
    assert result.content == "hello back"
    assert result.iterations == 1
    assert result.finish_reason == FinishReason.STOP


async def test_history_records_user_and_agent_turns(writer: AuditWriter) -> None:
    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()
    llm = FakeLLMClient([LLMResponse(content="answer")])

    await run_turn(
        session_id=s.id,
        user_message="prompt",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )
    after = graph.get(s.id)
    assert len(after.history) == 2
    assert after.history[0].role == "user"
    assert after.history[0].content == "prompt"
    assert after.history[1].role == "agent"
    assert after.history[1].content == "answer"


async def test_tool_call_followed_by_final_answer(writer: AuditWriter) -> None:
    graph, registry, client, memory, _ = await _setup(writer)
    s = await graph.new()
    cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    llm = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(
                    ToolCall(id="c1", name="memory.write", args={"key": "k", "value": "v"}),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="stored", finish_reason=FinishReason.STOP),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="store v",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )
    assert result.content == "stored"
    assert result.iterations == 2
    assert len(result.tool_outcomes) == 1
    assert result.tool_outcomes[0].decision == Decision.ALLOW
    assert memory.read("k") is not None


async def test_policy_denial_passed_back_to_llm(writer: AuditWriter) -> None:
    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

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
            LLMResponse(
                content="I don't have permission to make purchases.",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="buy stuff",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )
    assert result.tool_outcomes[0].decision == Decision.DENY
    assert "permission" in result.content.lower()


async def test_label_accumulation_blocks_subsequent_egress(writer: AuditWriter) -> None:
    """Canonical: read confidential.health then try to email -> denied."""
    graph, registry, client, memory, _ = await _setup(writer)
    s = await graph.new()
    read_cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    write_cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    purchase_cap = Capability(kind=CapabilityKind.QUEUE_PURCHASE, pattern="*", max_amount=10000)
    graph._sessions[s.id] = replace(
        s,
        capability_set=frozenset({read_cap, write_cap, purchase_cap}),
    )

    memory.write("labs", "BP=120/80", frozenset({Label.CONFIDENTIAL_HEALTH}))

    llm = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(ToolCall(id="c1", name="memory.read", args={"key": "labs"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="",
                tool_calls=(
                    ToolCall(
                        id="c2",
                        name="purchase.queue",
                        args={"vendor": "pharmacy", "item": "rx", "amount": 50},
                    ),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(
                content="Cannot continue: health data cannot be combined with purchases.",
                finish_reason=FinishReason.STOP,
            ),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="read my labs and order something",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )
    assert len(result.tool_outcomes) == 2
    assert result.tool_outcomes[0].decision == Decision.ALLOW
    assert Label.CONFIDENTIAL_HEALTH in result.tool_outcomes[0].labels_added
    assert result.tool_outcomes[1].decision == Decision.DENY
    assert result.tool_outcomes[1].rule == "health-meets-egress"

    after = graph.get(s.id)
    assert Label.CONFIDENTIAL_HEALTH in after.label_set


async def test_tool_not_found_handled_gracefully(writer: AuditWriter) -> None:
    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()
    llm = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(ToolCall(id="c1", name="does.not.exist", args={}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="ok", finish_reason=FinishReason.STOP),
        ],
    )

    result = await run_turn(
        session_id=s.id,
        user_message="x",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )
    assert result.tool_outcomes[0].decision == Decision.DENY
    assert "tool not found" in (result.tool_outcomes[0].reason or "")


async def test_max_iterations_raises(writer: AuditWriter) -> None:
    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    looping_response = LLMResponse(
        content="",
        tool_calls=(ToolCall(id="c", name="memory.read", args={"key": "x"}),),
        finish_reason=FinishReason.TOOL_CALLS,
    )
    llm = FakeLLMClient([looping_response] * 5)

    with pytest.raises(AgentLoopExceededError):
        await run_turn(
            session_id=s.id,
            user_message="loop",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
            max_iterations=3,
        )


async def test_audit_emits_llm_events(writer: AuditWriter) -> None:
    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()
    llm = FakeLLMClient([LLMResponse(content="ok")])

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
    types = [e.event_type for e in events]
    assert EventType.LLM_REQUEST_SENT in types
    assert EventType.LLM_RESPONSE_RECEIVED in types


async def test_terminal_session_rejected(writer: AuditWriter) -> None:
    from capabledeputy.session.graph import SessionStateError

    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()
    await graph.abort(s.id)
    llm = FakeLLMClient([LLMResponse(content="ok")])

    with pytest.raises(SessionStateError):
        await run_turn(
            session_id=s.id,
            user_message="hi",
            llm=llm,
            tool_client=client,
            registry=registry,
            graph=graph,
            audit=writer,
        )


def test_build_tool_descriptions_lists_registered_tools() -> None:
    registry, _, _ = _registry_with_natives()
    descriptions = build_tool_descriptions(registry)
    names = {d.name for d in descriptions}
    assert "memory.read" in names
    assert "memory.write" in names
    assert "purchase.queue" in names


async def test_no_tools_notice_injected_when_session_has_no_caps(
    writer: AuditWriter,
) -> None:
    """A session with zero capabilities sees an empty tool list. The
    LLM gets an explicit notice telling it not to hallucinate calls
    and to instruct the user to /grant capabilities. This guards
    against the failure mode observed in the test drive transcript
    where the LLM made up `inbox.search`, `email.forward`, etc."""
    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()  # no caps granted
    llm = FakeLLMClient([LLMResponse(content="I have no tools.")])

    await run_turn(
        session_id=s.id,
        user_message="forward the dinner email to my wife",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )

    [(messages, tools)] = llm.calls
    assert tools == []
    # First message is the default system prompt; the notice is a
    # second system message appended for this turn only.
    system_messages = [m for m in messages if m.role.value == "system"]
    notice = next(
        (m for m in system_messages if "NOTICE" in m.content),
        None,
    )
    assert notice is not None, "expected no-tools NOTICE in system messages"
    assert "tool list is empty" in notice.content
    assert "/grant" in notice.content


async def test_no_tools_notice_absent_when_caps_present(
    writer: AuditWriter,
) -> None:
    """Sanity check: when the session DOES have caps, the notice is
    not injected (would otherwise clutter every turn)."""
    graph, registry, client, _, _ = await _setup(writer)
    s = await graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))
    llm = FakeLLMClient([LLMResponse(content="hi")])

    await run_turn(
        session_id=s.id,
        user_message="hi",
        llm=llm,
        tool_client=client,
        registry=registry,
        graph=graph,
        audit=writer,
    )

    [(messages, tools)] = llm.calls
    assert len(tools) > 0
    system_messages = [m for m in messages if m.role.value == "system"]
    for m in system_messages:
        assert "NOTICE" not in m.content
