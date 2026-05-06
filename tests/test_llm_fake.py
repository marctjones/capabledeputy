import pytest

from capabledeputy.llm.fake import FakeLLMClient
from capabledeputy.llm.types import LLMResponse, Message, Role


async def test_returns_scripted_responses_in_order() -> None:
    client = FakeLLMClient(
        [
            LLMResponse(content="first"),
            LLMResponse(content="second"),
        ],
    )
    a = await client.respond([Message(role=Role.USER, content="x")], [])
    b = await client.respond([Message(role=Role.USER, content="y")], [])
    assert a.content == "first"
    assert b.content == "second"


async def test_records_calls_for_assertions() -> None:
    client = FakeLLMClient([LLMResponse(content="ok")])
    msgs = [Message(role=Role.USER, content="hello")]
    await client.respond(msgs, [])
    assert client.calls == [(msgs, [])]


async def test_raises_when_out_of_responses() -> None:
    client = FakeLLMClient([LLMResponse(content="only one")])
    await client.respond([], [])
    with pytest.raises(RuntimeError, match="ran out of scripted responses"):
        await client.respond([], [])


async def test_initial_state_has_empty_calls_log() -> None:
    client = FakeLLMClient([])
    assert client.calls == []
