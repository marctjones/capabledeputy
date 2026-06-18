"""LLMClient backed by the `claude` CLI in print mode.

This backend is for a subscriber's own local use. It shells out to a logged-in
Claude CLI session instead of using an Anthropic API key. The planning model is
run with Claude Code built-in tools disabled so CapDep remains the only policy
gate for tool execution.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from uuid import uuid4

from capabledeputy.llm.types import (
    FinishReason,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolDescription,
)

# Built-in Claude Code tools disabled so the planner cannot act behind the gate.
# Keep this list current if Anthropic adds built-ins.
DISABLED_TOOLS = (
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Bash",
    "BashOutput",
    "KillShell",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
)

Runner = Callable[[str, list[str]], Awaitable[str]]


class ClaudeCliError(RuntimeError):
    """The `claude` CLI failed or returned an error envelope."""


async def _default_runner(prompt: str, args: list[str]) -> str:
    binary = os.environ.get("CAPDEP_CLAUDE_BIN", "claude")
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(prompt.encode())
    if proc.returncode != 0:
        raise ClaudeCliError(
            f"claude exited {proc.returncode}: {err.decode('utf-8', 'replace')[:400]}",
        )
    return out.decode("utf-8", "replace")


class ClaudeCliClient:
    def __init__(self, *, model: str | None = None, runner: Runner | None = None) -> None:
        self._model = model
        self._runner = runner or _default_runner

    async def respond(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
    ) -> LLMResponse:
        prompt = _build_prompt(messages, tools)
        args = [
            "-p",
            "--output-format",
            "json",
            "--max-turns",
            "1",
            "--disallowed-tools",
            *DISABLED_TOOLS,
        ]
        if self._model:
            args += ["--model", self._model]
        stdout = await self._runner(prompt, args)
        return _parse_response(stdout)


def _build_prompt(messages: list[Message], tools: list[ToolDescription]) -> str:
    system = "\n".join(message.content for message in messages if message.role is Role.SYSTEM)
    convo: list[str] = []
    for message in messages:
        if message.role is Role.SYSTEM:
            continue
        if message.role is Role.TOOL:
            convo.append(
                f"tool_result({message.name or message.tool_call_id or ''}): {message.content}",
            )
        elif message.tool_calls:
            for tool_call in message.tool_calls:
                convo.append(f"assistant called {tool_call.name}({json.dumps(tool_call.args)})")
        else:
            convo.append(f"{message.role.value}: {message.content}")

    tool_lines = (
        "\n".join(
            f"- {tool.name}: {tool.description}  "
            f"params={json.dumps(tool.parameters_schema)}"
            for tool in tools
        )
        or "(no tools available)"
    )
    return (
        (f"{system}\n\n" if system else "")
        + "You are the PLANNING model for a policy-mediated agent. You do NOT "
        "execute tools. A separate policy engine gates and runs them. Respond "
        "with EXACTLY ONE JSON object and nothing else:\n"
        '  to call a tool:    {"tool_call": {"name": "<tool>", "args": { ... }}}\n'
        '  to reply to user:  {"content": "<text>"}\n\n'
        f"Available tools:\n{tool_lines}\n\n"
        "Conversation:\n"
        + "\n".join(convo)
        + "\n\nRespond now with the single JSON object."
    )


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        stripped = stripped[newline + 1 :] if newline != -1 else stripped
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _parse_response(stdout: str) -> LLMResponse:
    result_text = stdout
    usage: dict[str, int] = {}
    model: str | None = None
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        envelope = None
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            raise ClaudeCliError(f"claude returned an error: {envelope.get('result')!r}")
        result_text = str(envelope.get("result", ""))
        raw_usage = envelope.get("usage") or {}
        usage = {
            "prompt_tokens": int(raw_usage.get("input_tokens", 0) or 0),
            "completion_tokens": int(raw_usage.get("output_tokens", 0) or 0),
        }
        model = next(iter(envelope.get("modelUsage", {}) or {}), None)

    try:
        parsed = json.loads(_strip_fences(result_text))
    except json.JSONDecodeError:
        return LLMResponse(content=result_text, model=model, usage=usage)

    if isinstance(parsed, dict) and isinstance(parsed.get("tool_call"), dict):
        raw_call = parsed["tool_call"]
        return LLMResponse(
            content="",
            tool_calls=(
                ToolCall(
                    id=str(uuid4()),
                    name=str(raw_call.get("name", "")),
                    args=dict(raw_call.get("args") or {}),
                ),
            ),
            finish_reason=FinishReason.TOOL_CALLS,
            model=model,
            usage=usage,
        )
    if isinstance(parsed, dict) and "content" in parsed:
        return LLMResponse(content=str(parsed["content"]), model=model, usage=usage)
    return LLMResponse(content=result_text, model=model, usage=usage)
