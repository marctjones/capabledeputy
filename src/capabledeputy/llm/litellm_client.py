"""LiteLLM-backed LLMClient adapter.

Translates between CapableDeputy's Message/ToolCall/LLMResponse types
and the OpenAI-compatible shape that LiteLLM uses internally. LiteLLM
routes to Anthropic, OpenAI, Gemini, Ollama, and dozens of other
providers behind a single API; the model name selects the provider.

Prompt caching is not configured here in v0.1; for Anthropic, callers
who want it can set the appropriate cache_control headers via
completion_kwargs once Phase 4+ adds explicit support.
"""

from __future__ import annotations

import json
from typing import Any

from capabledeputy.llm.types import (
    FinishReason,
    LLMResponse,
    Message,
    ToolCall,
    ToolDescription,
)


def _sanitize_tool_name(name: str) -> str:
    """Anthropic's tool-name regex is ^[a-zA-Z0-9_-]{1,128}$, which excludes
    dots. CapableDeputy tool names use dots (memory.read, etc.) for clarity,
    so we substitute dots with underscores at the API boundary and reverse
    the mapping when parsing tool calls back."""
    return name.replace(".", "_")


def _message_to_openai(msg: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": msg.role.value, "content": msg.content}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": _sanitize_tool_name(tc.name),
                    "arguments": json.dumps(tc.args),
                },
            }
            for tc in msg.tool_calls
        ]
    if msg.tool_call_id:
        out["tool_call_id"] = msg.tool_call_id
    if msg.name:
        out["name"] = _sanitize_tool_name(msg.name)
    return out


def _tool_to_openai(tool: ToolDescription) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _sanitize_tool_name(tool.name),
            "description": tool.description,
            "parameters": tool.parameters_schema or {"type": "object"},
        },
    }


def _response_from_openai(raw: Any, name_map: dict[str, str]) -> LLMResponse:
    choice = raw.choices[0]
    msg = choice.message
    tool_calls: tuple[ToolCall, ...] = ()
    if getattr(msg, "tool_calls", None):
        tool_calls = tuple(
            ToolCall(
                id=tc.id,
                name=name_map.get(tc.function.name, tc.function.name) or tc.function.name,
                args=json.loads(tc.function.arguments or "{}"),
            )
            for tc in msg.tool_calls
        )
    try:
        finish_reason = FinishReason(choice.finish_reason)
    except ValueError:
        finish_reason = FinishReason.STOP

    usage: dict[str, int] = {}
    if getattr(raw, "usage", None) is not None:
        usage = {
            "prompt_tokens": int(getattr(raw.usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(raw.usage, "completion_tokens", 0) or 0),
        }

    return LLMResponse(
        content=msg.content or "",
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        model=getattr(raw, "model", None),
        usage=usage,
    )


class LiteLLMClient:
    def __init__(self, model: str = "claude-haiku-4-5", **completion_kwargs: Any) -> None:
        self._model = model
        self._kwargs = completion_kwargs

    async def respond(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
    ) -> LLMResponse:
        import litellm

        payload_messages = [_message_to_openai(m) for m in messages]
        payload_tools = [_tool_to_openai(t) for t in tools]

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": payload_messages,
            **self._kwargs,
        }
        if payload_tools:
            kwargs["tools"] = payload_tools

        name_map = {_sanitize_tool_name(t.name): t.name for t in tools}
        raw = await litellm.acompletion(**kwargs)
        return _response_from_openai(raw, name_map)
