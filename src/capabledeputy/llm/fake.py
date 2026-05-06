"""Deterministic LLM client for tests.

FakeLLMClient is initialized with a scripted list of LLMResponse
objects and returns them in order. This is the cassette format for
Phase 4 — recording cassettes against real APIs is a Phase 5+
feature. Tests build cassettes by hand.
"""

from __future__ import annotations

from collections.abc import Iterable

from capabledeputy.llm.types import LLMResponse, Message, ToolDescription


class FakeLLMClient:
    def __init__(self, responses: Iterable[LLMResponse]) -> None:
        self._queue: list[LLMResponse] = list(responses)
        self.calls: list[tuple[list[Message], list[ToolDescription]]] = []

    async def respond(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
    ) -> LLMResponse:
        if not self._queue:
            raise RuntimeError("FakeLLMClient: ran out of scripted responses")
        self.calls.append((list(messages), list(tools)))
        return self._queue.pop(0)
