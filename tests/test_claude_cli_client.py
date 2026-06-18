"""Claude CLI backend tests with an injected runner.

The load-bearing safety check is that the planner is invoked with every
built-in Claude Code tool disabled, so it cannot act behind CapDep's policy
gate.
"""

from __future__ import annotations

import json

import pytest

from capabledeputy.llm.claude_cli import (
    DISABLED_TOOLS,
    ClaudeCliClient,
    ClaudeCliError,
)
from capabledeputy.llm.types import FinishReason, Message, Role, ToolDescription

_MESSAGES = [
    Message(role=Role.SYSTEM, content="be careful"),
    Message(role=Role.USER, content="read my notes"),
]
_TOOLS = [
    ToolDescription(
        name="fs.read",
        description="read a file",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    ),
]


def _envelope(result_text: str, *, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": is_error,
            "result": result_text,
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "modelUsage": {"claude-opus-4-8": {}},
        },
    )


class _FakeRunner:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, prompt: str, args: list[str]) -> str:
        self.calls.append((prompt, args))
        return self.stdout


async def test_parses_a_tool_call() -> None:
    inner = json.dumps({"tool_call": {"name": "fs.read", "args": {"path": "~/notes.txt"}}})
    runner = _FakeRunner(_envelope(inner))
    response = await ClaudeCliClient(runner=runner).respond(_MESSAGES, _TOOLS)
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.tool_calls[0].name == "fs.read"
    assert response.tool_calls[0].args == {"path": "~/notes.txt"}


async def test_parses_a_content_reply() -> None:
    runner = _FakeRunner(_envelope(json.dumps({"content": "here is a summary"})))
    response = await ClaudeCliClient(runner=runner).respond(_MESSAGES, _TOOLS)
    assert response.finish_reason is FinishReason.STOP
    assert response.content == "here is a summary"


async def test_strips_markdown_fences() -> None:
    inner = "```json\n" + json.dumps({"content": "x"}) + "\n```"
    response = await ClaudeCliClient(runner=_FakeRunner(_envelope(inner))).respond(
        _MESSAGES,
        _TOOLS,
    )
    assert response.content == "x"


async def test_non_json_result_becomes_content() -> None:
    response = await ClaudeCliClient(runner=_FakeRunner(_envelope("just prose"))).respond(
        _MESSAGES,
        _TOOLS,
    )
    assert response.content == "just prose"


async def test_error_envelope_raises() -> None:
    runner = _FakeRunner(_envelope("boom", is_error=True))
    with pytest.raises(ClaudeCliError):
        await ClaudeCliClient(runner=runner).respond(_MESSAGES, _TOOLS)


async def test_safety_all_builtin_tools_are_disabled() -> None:
    runner = _FakeRunner(_envelope(json.dumps({"content": "ok"})))
    await ClaudeCliClient(runner=runner).respond(_MESSAGES, _TOOLS)
    _prompt, args = runner.calls[0]
    assert "--disallowed-tools" in args
    for tool in DISABLED_TOOLS:
        assert tool in args
    assert "--max-turns" in args
    assert "1" in args


async def test_prompt_carries_tools_contract_and_conversation() -> None:
    runner = _FakeRunner(_envelope(json.dumps({"content": "ok"})))
    await ClaudeCliClient(runner=runner).respond(_MESSAGES, _TOOLS)
    prompt, _args = runner.calls[0]
    assert "fs.read" in prompt
    assert "tool_call" in prompt
    assert "read my notes" in prompt


async def test_model_flag_passed_when_set() -> None:
    runner = _FakeRunner(_envelope(json.dumps({"content": "ok"})))
    await ClaudeCliClient(model="opus", runner=runner).respond(_MESSAGES, _TOOLS)
    _prompt, args = runner.calls[0]
    assert "--model" in args
    assert "opus" in args


def test_factory_selects_backend(monkeypatch) -> None:
    from capabledeputy.llm.factory import make_llm_client
    from capabledeputy.llm.litellm_client import LiteLLMClient

    monkeypatch.setenv("CAPDEP_LLM_BACKEND", "claude-cli")
    assert isinstance(make_llm_client(), ClaudeCliClient)
    monkeypatch.setenv("CAPDEP_LLM_BACKEND", "litellm")
    assert isinstance(make_llm_client("claude-haiku-4-5"), LiteLLMClient)
