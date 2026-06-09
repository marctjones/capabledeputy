"""`ClaudeCliClient` — subscription-backed LLM via the `claude` CLI.

Uses an injected fake runner so tests never spawn the real binary. The
load-bearing test is the safety invariant: the planner is invoked with every
built-in tool disabled, so it can never act behind capdep's policy gate.
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

_MSGS = [
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


# --- parsing the two-level (CLI envelope → inner contract) response --


async def test_parses_a_tool_call() -> None:
    inner = json.dumps({"tool_call": {"name": "fs.read", "args": {"path": "~/notes.txt"}}})
    runner = _FakeRunner(_envelope(inner))
    resp = await ClaudeCliClient(runner=runner).respond(_MSGS, _TOOLS)
    assert resp.finish_reason is FinishReason.TOOL_CALLS
    assert resp.tool_calls[0].name == "fs.read"
    assert resp.tool_calls[0].args == {"path": "~/notes.txt"}


async def test_parses_a_content_reply() -> None:
    runner = _FakeRunner(_envelope(json.dumps({"content": "here is a summary"})))
    resp = await ClaudeCliClient(runner=runner).respond(_MSGS, _TOOLS)
    assert resp.finish_reason is FinishReason.STOP
    assert resp.content == "here is a summary"


async def test_strips_markdown_fences() -> None:
    inner = "```json\n" + json.dumps({"content": "x"}) + "\n```"
    resp = await ClaudeCliClient(runner=_FakeRunner(_envelope(inner))).respond(_MSGS, _TOOLS)
    assert resp.content == "x"


async def test_non_json_result_becomes_content() -> None:
    resp = await ClaudeCliClient(runner=_FakeRunner(_envelope("just prose"))).respond(_MSGS, _TOOLS)
    assert resp.content == "just prose"


async def test_error_envelope_raises() -> None:
    runner = _FakeRunner(_envelope("boom", is_error=True))
    with pytest.raises(ClaudeCliError):
        await ClaudeCliClient(runner=runner).respond(_MSGS, _TOOLS)


# --- the safety invariant -------------------------------------------


async def test_safety_all_builtin_tools_are_disabled() -> None:
    """The planner is invoked with every built-in tool disabled + a single
    turn — it cannot read files / run bash / fetch the web behind the gate."""
    runner = _FakeRunner(_envelope(json.dumps({"content": "ok"})))
    await ClaudeCliClient(runner=runner).respond(_MSGS, _TOOLS)
    _prompt, args = runner.calls[0]
    assert "--disallowed-tools" in args
    for tool in DISABLED_TOOLS:
        assert tool in args
    assert "--max-turns" in args and "1" in args


# --- prompt construction + model flag -------------------------------


async def test_prompt_carries_tools_contract_and_conversation() -> None:
    runner = _FakeRunner(_envelope(json.dumps({"content": "ok"})))
    await ClaudeCliClient(runner=runner).respond(_MSGS, _TOOLS)
    prompt, _ = runner.calls[0]
    assert "fs.read" in prompt  # the tool schema
    assert "tool_call" in prompt  # the JSON contract
    assert "read my notes" in prompt  # the conversation


async def test_model_flag_passed_when_set() -> None:
    runner = _FakeRunner(_envelope(json.dumps({"content": "ok"})))
    await ClaudeCliClient(model="opus", runner=runner).respond(_MSGS, _TOOLS)
    _, args = runner.calls[0]
    assert "--model" in args
    assert "opus" in args


# --- the backend factory --------------------------------------------


def test_factory_selects_backend(monkeypatch) -> None:
    from capabledeputy.llm.factory import make_llm_client
    from capabledeputy.llm.litellm_client import LiteLLMClient

    monkeypatch.setenv("CAPDEP_LLM_BACKEND", "claude-cli")
    assert isinstance(make_llm_client(), ClaudeCliClient)
    monkeypatch.setenv("CAPDEP_LLM_BACKEND", "litellm")
    assert isinstance(make_llm_client("claude-haiku-4-5"), LiteLLMClient)
