"""Unit tests for ClaudeCodeLLMClient.

The subprocess invocation isn't tested here (it requires the `claude`
binary to be installed and authenticated); tests for it are gated under
the `claude_code` pytest mark in tests/integration/. These tests cover
the parsing and prompt-rendering logic directly.
"""

from __future__ import annotations

import json

import pytest

from capabledeputy.llm.claude_code_client import (
    ClaudeCodeLLMClient,
    _build_prompt,
    _try_parse_tool_calls,
    parse_claude_response,
)
from capabledeputy.llm.types import (
    FinishReason,
    Message,
    Role,
    ToolCall,
    ToolDescription,
)


def test_parse_text_response() -> None:
    raw = json.dumps({"result": "hello world", "model": "claude-sonnet-4-6"})
    response = parse_claude_response(raw)
    assert response.content == "hello world"
    assert response.tool_calls == ()
    assert response.finish_reason == FinishReason.STOP
    assert response.model == "claude-sonnet-4-6"


def test_parse_tool_call_response() -> None:
    inner = {
        "tool_calls": [
            {"id": "c1", "name": "memory.read", "args": {"key": "labs"}},
        ],
    }
    raw = json.dumps({"result": json.dumps(inner)})
    response = parse_claude_response(raw)
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "memory.read"
    assert response.tool_calls[0].args == {"key": "labs"}
    assert response.finish_reason == FinishReason.TOOL_CALLS


def test_parse_multiple_tool_calls() -> None:
    inner = {
        "tool_calls": [
            {"id": "c1", "name": "a", "args": {}},
            {"id": "c2", "name": "b", "args": {"x": 1}},
        ],
    }
    raw = json.dumps({"result": json.dumps(inner)})
    response = parse_claude_response(raw)
    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].name == "a"
    assert response.tool_calls[1].args == {"x": 1}


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(RuntimeError, match="not JSON"):
        parse_claude_response("not json at all")


def test_parse_text_that_mentions_tool_calls_falls_back_to_text() -> None:
    raw = json.dumps({"result": "I considered tool_calls but decided not to"})
    response = parse_claude_response(raw)
    assert response.content.startswith("I considered")
    assert response.tool_calls == ()


def test_try_parse_returns_none_for_non_json() -> None:
    assert _try_parse_tool_calls("hello") is None


def test_try_parse_returns_none_when_no_tool_calls_key() -> None:
    assert _try_parse_tool_calls('{"foo": "bar"}') is None


def test_try_parse_returns_none_for_malformed_calls() -> None:
    assert _try_parse_tool_calls('{"tool_calls": [{"missing_name": true}]}') is None


def test_try_parse_synthesizes_id_when_missing() -> None:
    parsed = _try_parse_tool_calls('{"tool_calls": [{"name": "x", "args": {}}]}')
    assert parsed is not None
    assert parsed[0].name == "x"
    assert len(parsed[0].id) > 0


def test_build_prompt_includes_tool_descriptions() -> None:
    tools = [
        ToolDescription(name="memory.read", description="Read from memory"),
        ToolDescription(name="memory.write", description="Write to memory"),
    ]
    messages = [Message(role=Role.USER, content="hi")]
    prompt = _build_prompt(messages, tools)
    assert "memory.read" in prompt
    assert "memory.write" in prompt
    assert "Read from memory" in prompt
    assert "tool_calls" in prompt


def test_build_prompt_includes_message_history() -> None:
    messages = [
        Message(role=Role.SYSTEM, content="be careful"),
        Message(role=Role.USER, content="do thing"),
        Message(role=Role.ASSISTANT, content="ok"),
        Message(role=Role.TOOL, content="result", tool_call_id="c1", name="t"),
    ]
    prompt = _build_prompt(messages, [])
    assert "be careful" in prompt
    assert "do thing" in prompt
    assert "result" in prompt


def test_build_prompt_renders_tool_calls_in_history() -> None:
    tc = ToolCall(id="c1", name="x", args={"k": "v"})
    messages = [Message(role=Role.ASSISTANT, content="", tool_calls=(tc,))]
    prompt = _build_prompt(messages, [])
    assert '"name": "x"' in prompt or '"name":"x"' in prompt


async def test_client_respond_uses_run_claude_helper() -> None:
    client = ClaudeCodeLLMClient(model="test-model")
    fake_output = json.dumps({"result": "scripted answer", "model": "test-model"})

    async def fake_run(prompt: str) -> str:
        return fake_output

    client._run_claude = fake_run  # type: ignore[method-assign]
    response = await client.respond(
        [Message(role=Role.USER, content="hi")],
        [],
    )
    assert response.content == "scripted answer"
    assert response.model == "test-model"


async def test_client_respond_extracts_tool_calls_from_subprocess_output() -> None:
    client = ClaudeCodeLLMClient()
    inner = {"tool_calls": [{"id": "c1", "name": "memory.read", "args": {"k": "x"}}]}
    fake_output = json.dumps({"result": json.dumps(inner)})

    async def fake_run(prompt: str) -> str:
        return fake_output

    client._run_claude = fake_run  # type: ignore[method-assign]
    response = await client.respond(
        [Message(role=Role.USER, content="please read")],
        [ToolDescription(name="memory.read", description="read")],
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "memory.read"
    assert response.finish_reason == FinishReason.TOOL_CALLS
