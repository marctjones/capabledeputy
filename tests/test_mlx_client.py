"""Unit tests for the MLX-backed LLM client.

These cover prompt rendering and output parsing without requiring a
loaded local model. Real MLX/Metal probes are run manually because they
depend on host GPU visibility and cached model weights.
"""

from __future__ import annotations

import pytest

from capabledeputy.llm.mlx_client import (
    DEFAULT_MLX_MODEL,
    MLXLLMClient,
    _build_prompt,
    _messages_to_chat,
    _strip_reasoning_blocks,
    _try_parse_tool_calls,
    parse_mlx_response,
)
from capabledeputy.llm.types import FinishReason, Message, Role, ToolDescription


def test_parse_plain_text_response() -> None:
    response = parse_mlx_response("hello world", model=DEFAULT_MLX_MODEL)
    assert response.content == "hello world"
    assert response.tool_calls == ()
    assert response.finish_reason == FinishReason.STOP
    assert response.model == DEFAULT_MLX_MODEL


def test_parse_tool_call_response() -> None:
    response = parse_mlx_response(
        '{"tool_calls":[{"id":"c1","name":"memory.read","args":{"key":"labs"}}]}',
        model=DEFAULT_MLX_MODEL,
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "memory.read"
    assert response.tool_calls[0].args == {"key": "labs"}
    assert response.finish_reason == FinishReason.TOOL_CALLS


def test_try_parse_returns_none_for_non_json() -> None:
    assert _try_parse_tool_calls("hello") is None


def test_strip_reasoning_blocks_removes_think_variants() -> None:
    assert _strip_reasoning_blocks("<think>secret</think>\nfinal") == "final"
    assert _strip_reasoning_blocks("<thinking>secret</thinking>\nfinal") == "final"
    assert _strip_reasoning_blocks("<think>secret\n{\"ok\": true}") == '{"ok": true}'
    assert _strip_reasoning_blocks("<thinking>secret only") == ""


def test_parse_tool_call_response_ignores_reasoning_block() -> None:
    response = parse_mlx_response(
        (
            '<think>reasoning</think>{"tool_calls":'
            '[{"id":"c1","name":"memory.read","args":{"key":"labs"}}]}'
        ),
        model=DEFAULT_MLX_MODEL,
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "memory.read"
    assert response.finish_reason == FinishReason.TOOL_CALLS


def test_parse_tool_call_response_accepts_fenced_json() -> None:
    response = parse_mlx_response(
        '```json\n{"tool_calls":[{"name":"memory.read","args":{"key":"labs"}}]}\n```',
        model=DEFAULT_MLX_MODEL,
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "memory.read"
    assert response.finish_reason == FinishReason.TOOL_CALLS


def test_parse_tool_call_response_extracts_json_after_preface() -> None:
    response = parse_mlx_response(
        'Final answer:\n{"tool_calls":[{"name":"memory.read","args":{"key":"labs"}}]}',
        model=DEFAULT_MLX_MODEL,
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].args == {"key": "labs"}


def test_build_prompt_includes_tools_and_history() -> None:
    prompt = _build_prompt(
        [
            Message(role=Role.SYSTEM, content="be careful"),
            Message(role=Role.USER, content="read the note"),
        ],
        [ToolDescription(name="memory.read", description="Read from memory")],
    )
    assert "memory.read" in prompt
    assert "Read from memory" in prompt
    assert "be careful" in prompt
    assert "read the note" in prompt
    assert "tool_calls" in prompt


def test_messages_to_chat_injects_tool_instruction_into_system() -> None:
    chat = _messages_to_chat(
        [
            Message(role=Role.SYSTEM, content="be careful"),
            Message(role=Role.USER, content="read the note"),
        ],
        [ToolDescription(name="memory.read", description="Read from memory")],
    )
    assert chat[0]["role"] == "system"
    assert "be careful" in chat[0]["content"]
    assert "memory.read" in chat[0]["content"]
    assert "tool_calls" in chat[0]["content"]
    assert chat[1] == {"role": "user", "content": "read the note"}


def test_messages_to_chat_keeps_no_tools_prompt_clean() -> None:
    chat = _messages_to_chat(
        [
            Message(role=Role.SYSTEM, content="extract only json"),
            Message(role=Role.USER, content="schema here"),
        ],
        [],
    )
    assert chat == [
        {"role": "system", "content": "extract only json"},
        {"role": "user", "content": "schema here"},
    ]


def test_render_prompt_uses_chat_template_with_thinking_flag() -> None:
    client = MLXLLMClient(model=DEFAULT_MLX_MODEL)

    class _FakeTokenizer:
        chat_template = "stub"

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def apply_chat_template(self, messages, **kwargs):
            self.calls.append({"messages": messages, **kwargs})
            return "rendered"

    tokenizer = _FakeTokenizer()
    prompt = client._render_prompt(  # type: ignore[attr-defined]
        tokenizer,
        [Message(role=Role.SYSTEM, content="be careful"), Message(role=Role.USER, content="hi")],
        [ToolDescription(name="memory.read", description="read")],
    )
    assert prompt == "rendered"
    assert tokenizer.calls[0]["enable_thinking"] is False
    rendered_messages = tokenizer.calls[0]["messages"]
    assert isinstance(rendered_messages, list)
    assert "memory.read" in rendered_messages[0]["content"]


def test_render_prompt_can_enable_thinking() -> None:
    client = MLXLLMClient(model=DEFAULT_MLX_MODEL, enable_thinking=True)

    class _FakeTokenizer:
        chat_template = "stub"

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def apply_chat_template(self, messages, **kwargs):
            self.calls.append({"messages": messages, **kwargs})
            return "rendered"

    tokenizer = _FakeTokenizer()
    client._render_prompt(  # type: ignore[attr-defined]
        tokenizer,
        [Message(role=Role.USER, content="hi")],
        [],
    )
    assert tokenizer.calls[0]["enable_thinking"] is True


async def test_client_respond_uses_generate_helper() -> None:
    client = MLXLLMClient(model=DEFAULT_MLX_MODEL)

    class _FakeTokenizer:
        chat_template = None

    def fake_load():
        return object(), _FakeTokenizer()

    def fake_generate(model, tokenizer, prompt: str) -> str:
        return "scripted answer"

    client._load_model_sync = fake_load  # type: ignore[method-assign]
    client._generate_sync = fake_generate  # type: ignore[method-assign]
    response = await client.respond(
        [Message(role=Role.USER, content="hi")],
        [],
    )
    assert response.content == "scripted answer"
    assert response.model == DEFAULT_MLX_MODEL


async def test_client_respond_extracts_tool_calls() -> None:
    client = MLXLLMClient(model=DEFAULT_MLX_MODEL)

    class _FakeTokenizer:
        chat_template = None

    def fake_load():
        return object(), _FakeTokenizer()

    def fake_generate(model, tokenizer, prompt: str) -> str:
        return '{"tool_calls":[{"id":"c1","name":"memory.read","args":{"k":"x"}}]}'

    client._load_model_sync = fake_load  # type: ignore[method-assign]
    client._generate_sync = fake_generate  # type: ignore[method-assign]
    response = await client.respond(
        [Message(role=Role.USER, content="please read")],
        [ToolDescription(name="memory.read", description="read")],
    )
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "memory.read"
    assert response.finish_reason == FinishReason.TOOL_CALLS


async def test_client_respond_without_tools_does_not_parse_json_as_tool_calls() -> None:
    client = MLXLLMClient(model=DEFAULT_MLX_MODEL)

    class _FakeTokenizer:
        chat_template = None

    def fake_load():
        return object(), _FakeTokenizer()

    def fake_generate(model, tokenizer, prompt: str) -> str:
        return '{"medication_name":"lisinopril","dosage_mg":10,"frequency":"daily"}'

    client._load_model_sync = fake_load  # type: ignore[method-assign]
    client._generate_sync = fake_generate  # type: ignore[method-assign]
    response = await client.respond(
        [Message(role=Role.USER, content="extract")],
        [],
    )
    assert response.content == '{"medication_name":"lisinopril","dosage_mg":10,"frequency":"daily"}'
    assert response.tool_calls == ()
    assert response.finish_reason == FinishReason.STOP


async def test_client_respond_without_tools_strips_reasoning_blocks() -> None:
    client = MLXLLMClient(model=DEFAULT_MLX_MODEL)

    class _FakeTokenizer:
        chat_template = None

    def fake_load():
        return object(), _FakeTokenizer()

    def fake_generate(model, tokenizer, prompt: str) -> str:
        return "<think>hidden</think>{\"ok\":true}"

    client._load_model_sync = fake_load  # type: ignore[method-assign]
    client._generate_sync = fake_generate  # type: ignore[method-assign]
    response = await client.respond(
        [Message(role=Role.USER, content="extract")],
        [],
    )
    assert response.content == '{"ok":true}'


async def test_client_respond_without_tools_strips_json_fence() -> None:
    client = MLXLLMClient(model=DEFAULT_MLX_MODEL)

    class _FakeTokenizer:
        chat_template = None

    def fake_load():
        return object(), _FakeTokenizer()

    def fake_generate(model, tokenizer, prompt: str) -> str:
        return '```json\n{"ok":true}\n```'

    client._load_model_sync = fake_load  # type: ignore[method-assign]
    client._generate_sync = fake_generate  # type: ignore[method-assign]
    response = await client.respond(
        [Message(role=Role.USER, content="extract")],
        [],
    )
    assert response.content == '{"ok":true}'


def test_client_rejects_empty_mlx_spec() -> None:
    with pytest.raises(ValueError, match="mlx/<repo-or-path>"):
        from capabledeputy.llm.factory import make_llm_client

        make_llm_client("mlx/")
