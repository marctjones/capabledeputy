"""LLMClient backed by the `claude` CLI (Claude Code subscription).

Lets developers exercise CapableDeputy's agent loop using their Claude
Code subscription instead of an Anthropic API key. Useful when paid
API access isn't available; less robust than LiteLLMClient because:

  - Tool calls aren't native — the model is instructed to emit a
    specific JSON envelope, and we parse it. ~95% reliable; production
    code should still use LiteLLMClient.
  - Subprocess invocation adds 1-3 seconds per turn vs. direct API.
  - Model parameters (temperature, top_p) aren't exposed.
  - Prompt caching isn't configurable.

Use it for tests and demos. Don't use it for production agent loops.
"""

from __future__ import annotations

import json
from uuid import uuid4

import anyio

from capabledeputy.llm.types import (
    FinishReason,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolDescription,
)

_TOOL_INSTRUCTION = (
    "You have access to these tools:\n\n{tool_descriptions}\n\n"
    "To call one or more tools, respond with ONLY a JSON object on a "
    "single line, with this exact shape:\n"
    '{{"tool_calls": [{{"id": "<unique_id>", "name": "<tool_name>", '
    '"args": {{...}}}}]}}\n\n'
    "If no tool call is needed, respond with normal natural-language "
    "text and nothing else."
)


def _format_tool_descriptions(tools: list[ToolDescription]) -> str:
    if not tools:
        return "(no tools available)"
    return "\n".join(f"- {t.name}: {t.description}" for t in tools)


def _format_messages_for_claude(messages: list[Message]) -> str:
    parts: list[str] = []
    for msg in messages:
        if msg.role == Role.SYSTEM:
            parts.append(f"# System\n{msg.content}")
        elif msg.role == Role.USER:
            parts.append(f"# User\n{msg.content}")
        elif msg.role == Role.ASSISTANT:
            if msg.tool_calls:
                tc_dump = json.dumps(
                    {
                        "tool_calls": [
                            {"id": tc.id, "name": tc.name, "args": tc.args} for tc in msg.tool_calls
                        ],
                    },
                )
                parts.append(f"# Assistant (tool calls)\n{tc_dump}")
            else:
                parts.append(f"# Assistant\n{msg.content}")
        elif msg.role == Role.TOOL:
            parts.append(
                f"# Tool result ({msg.name or 'unknown'}, "
                f"call_id={msg.tool_call_id})\n{msg.content}",
            )
    return "\n\n".join(parts)


def _build_prompt(messages: list[Message], tools: list[ToolDescription]) -> str:
    instruction = _TOOL_INSTRUCTION.format(
        tool_descriptions=_format_tool_descriptions(tools),
    )
    body = _format_messages_for_claude(messages)
    return f"{instruction}\n\n---\n\n{body}"


def _try_parse_tool_calls(text: str) -> tuple[ToolCall, ...] | None:
    stripped = text.strip()
    if not (stripped.startswith("{") and "tool_calls" in stripped):
        return None
    try:
        envelope = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    raw_calls = envelope.get("tool_calls")
    if not isinstance(raw_calls, list):
        return None
    parsed: list[ToolCall] = []
    for raw in raw_calls:
        if not isinstance(raw, dict) or "name" not in raw:
            return None
        parsed.append(
            ToolCall(
                id=str(raw.get("id") or uuid4()),
                name=str(raw["name"]),
                args=raw.get("args") or {},
            ),
        )
    return tuple(parsed)


def parse_claude_response(stdout: str) -> LLMResponse:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"claude -p output was not JSON: {stdout[:200]}") from e

    result_text = envelope.get("result", "")
    if not isinstance(result_text, str):
        result_text = json.dumps(result_text)

    tool_calls = _try_parse_tool_calls(result_text)
    if tool_calls is not None:
        return LLMResponse(
            content="",
            tool_calls=tool_calls,
            finish_reason=FinishReason.TOOL_CALLS,
            model=envelope.get("model"),
        )

    return LLMResponse(
        content=result_text,
        finish_reason=FinishReason.STOP,
        model=envelope.get("model"),
    )


class ClaudeCodeLLMClient:
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        binary: str = "claude",
        timeout: float = 120.0,
    ) -> None:
        self._model = model
        self._binary = binary
        self._timeout = timeout

    async def respond(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
    ) -> LLMResponse:
        prompt = _build_prompt(messages, tools)
        stdout = await self._run_claude(prompt)
        return parse_claude_response(stdout)

    async def _run_claude(self, prompt: str) -> str:
        with anyio.fail_after(self._timeout):
            result = await anyio.run_process(
                [
                    self._binary,
                    "-p",
                    prompt,
                    "--output-format",
                    "json",
                    "--model",
                    self._model,
                ],
                check=False,
            )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"{self._binary} -p exited {result.returncode}: {stderr}",
            )
        return result.stdout.decode("utf-8", errors="replace")
