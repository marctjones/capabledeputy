from dataclasses import replace
from pathlib import Path

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.agent_handlers import make_agent_handlers
from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import FinishReason, LLMResponse, ToolCall
from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label


@pytest.fixture
async def app(tmp_path: Path) -> App:
    return App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=FakeLLMClient([LLMResponse(content="ok")]),
    )


async def test_session_send_runs_agent_loop(app: App) -> None:
    await app.startup()
    s = await app.graph.new()

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "hi"},
    )
    assert result["content"] == "ok"
    assert result["iterations"] == 1


async def test_session_send_no_llm_raises(tmp_path: Path) -> None:
    app = App(
        state_db_path=tmp_path / "state.db",
        audit_log_path=tmp_path / "audit.jsonl",
        llm_client=None,
    )
    await app.startup()
    s = await app.graph.new()
    handlers = make_agent_handlers(app)

    with pytest.raises(RuntimeError, match="no LLM client"):
        await handlers["session.send"]({"session_id": str(s.id), "message": "hi"})


async def test_session_grant_capability_persists(app: App) -> None:
    await app.startup()
    s = await app.graph.new()
    handlers = make_agent_handlers(app)

    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    result = await handlers["session.grant_capability"](
        {"session_id": str(s.id), "capability": cap.to_dict()},
    )
    assert any(c["pattern"] == "/home/*" for c in result["capability_set"])


async def test_session_send_returns_tool_outcomes(app: App) -> None:
    await app.startup()
    fake = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(
                    ToolCall(id="c1", name="memory.write", args={"key": "k", "value": "v"}),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="done", finish_reason=FinishReason.STOP),
        ],
    )
    app.llm_client = fake

    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.WRITE_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "store"},
    )
    assert result["iterations"] == 2
    assert len(result["tool_outcomes"]) == 1
    assert result["tool_outcomes"][0]["decision"] == "allow"


async def test_session_send_label_propagation_visible_in_outcome(app: App) -> None:
    await app.startup()
    app.memory.write("labs", "x", frozenset({Label.CONFIDENTIAL_HEALTH}))

    fake = FakeLLMClient(
        [
            LLMResponse(
                content="",
                tool_calls=(ToolCall(id="c", name="memory.read", args={"key": "labs"}),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
            LLMResponse(content="read", finish_reason=FinishReason.STOP),
        ],
    )
    app.llm_client = fake
    s = await app.graph.new()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="*")
    app.graph._sessions[s.id] = replace(s, capability_set=frozenset({cap}))

    handlers = make_agent_handlers(app)
    result = await handlers["session.send"](
        {"session_id": str(s.id), "message": "x"},
    )
    assert "confidential.health" in result["tool_outcomes"][0]["labels_added"]
