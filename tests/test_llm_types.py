from capabledeputy.llm.types import (
    FinishReason,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolDescription,
)


def test_role_values_are_lowercase() -> None:
    assert Role.SYSTEM == "system"
    assert Role.USER == "user"
    assert Role.ASSISTANT == "assistant"
    assert Role.TOOL == "tool"


def test_finish_reason_values() -> None:
    assert FinishReason.STOP == "stop"
    assert FinishReason.TOOL_CALLS == "tool_calls"


def test_tool_call_construction() -> None:
    tc = ToolCall(id="call-1", name="memory.read", args={"key": "x"})
    assert tc.id == "call-1"
    assert tc.name == "memory.read"
    assert tc.args == {"key": "x"}


def test_message_defaults() -> None:
    msg = Message(role=Role.USER, content="hi")
    assert msg.tool_calls == ()
    assert msg.tool_call_id is None
    assert msg.name is None


def test_message_with_tool_calls() -> None:
    tc = ToolCall(id="c1", name="t", args={})
    msg = Message(role=Role.ASSISTANT, tool_calls=(tc,))
    assert msg.tool_calls == (tc,)


def test_tool_description_default_schema_empty() -> None:
    td = ToolDescription(name="foo", description="bar")
    assert td.parameters_schema == {}


def test_llm_response_defaults() -> None:
    resp = LLMResponse(content="hello")
    assert resp.tool_calls == ()
    assert resp.finish_reason == FinishReason.STOP
    assert resp.model is None
    assert resp.usage == {}


def test_llm_response_with_tool_calls() -> None:
    tc = ToolCall(id="c1", name="t", args={})
    resp = LLMResponse(
        content="",
        tool_calls=(tc,),
        finish_reason=FinishReason.TOOL_CALLS,
    )
    assert resp.tool_calls == (tc,)
    assert resp.finish_reason == FinishReason.TOOL_CALLS
