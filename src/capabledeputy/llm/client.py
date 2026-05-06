"""LLMClient protocol shared by every adapter."""

from __future__ import annotations

from typing import Protocol

from capabledeputy.llm.types import LLMResponse, Message, ToolDescription


class LLMClient(Protocol):
    async def respond(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
    ) -> LLMResponse: ...
