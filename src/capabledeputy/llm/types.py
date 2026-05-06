"""Message and response types for LLM interactions.

Designed to be OpenAI-compatible (which LiteLLM also speaks) so the
same Message and ToolCall types work for Anthropic, OpenAI, Gemini,
and local models. Provider-specific quirks live behind the LLMClient
adapters in client.py and litellm_client.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolDescription:
    name: str
    description: str
    parameters_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason = FinishReason.STOP
    model: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
