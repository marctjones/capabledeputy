"""`ClaudeCliClient` — an LLMClient backed by the `claude` CLI (print mode).

Uses YOUR logged-in Claude subscription (`claude -p` draws on the subscriber's
Agent-SDK credit pool) instead of the per-token Anthropic API — for the
**subscriber's own local use**. It is NOT for hosted / multi-user backends:
routing other users' requests through subscription credentials violates
Anthropic's terms (use the API via `LiteLLMClient` there). Opt in with
`CAPDEP_LLM_BACKEND=claude-cli`.

SAFETY INVARIANT (load-bearing for a policy engine): capdep mediates tool
calls; the planning model must only *propose* them, never act. So `claude -p`
is run with **all built-in tools disabled** (`--disallowed-tools …`) and a
single turn — it cannot read files, run bash, or fetch the web. It returns one
JSON object that capdep parses into a proposed tool call (or a text reply);
capdep's engine then gates and executes. If you change the flags, preserve this
invariant — otherwise the Claude Code agent would act *behind* the policy gate.
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
# (Allowlist would be more future-proof, but the empty-variadic CLI form is
# fragile; keep this list current if Anthropic adds built-ins.)
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

# A runner takes (stdin_prompt, cli_args) and returns the CLI's stdout. Injected
# in tests so they never spawn the real `claude` binary.
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
        self._model = model  # "sonnet" | "opus" | a full id; None = CLI default
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


# --- prompt construction --------------------------------------------


def _build_prompt(messages: list[Message], tools: list[ToolDescription]) -> str:
    system = "\n".join(m.content for m in messages if m.role is Role.SYSTEM)
    convo: list[str] = []
    for m in messages:
        if m.role is Role.SYSTEM:
            continue
        if m.role is Role.TOOL:
            convo.append(f"tool_result({m.name or m.tool_call_id or ''}): {m.content}")
        elif m.tool_calls:
            for tc in m.tool_calls:
                convo.append(f"assistant called {tc.name}({json.dumps(tc.args)})")
        else:
            convo.append(f"{m.role.value}: {m.content}")
    tool_lines = (
        "\n".join(
            f"- {t.name}: {t.description}  params={json.dumps(t.parameters_schema)}" for t in tools
        )
        or "(no tools available)"
    )
    return (
        (f"{system}\n\n" if system else "")
        + "You are the PLANNING model for a policy-mediated agent. You do NOT "
        "execute tools — a separate policy engine gates and runs them. Respond "
        "with EXACTLY ONE JSON object and nothing else:\n"
        '  to call a tool:    {"tool_call": {"name": "<tool>", "args": { ... }}}\n'
        '  to reply to user:  {"content": "<text>"}\n\n'
        f"Available tools:\n{tool_lines}\n\n"
        "Conversation:\n" + "\n".join(convo) + "\n\n"
        "Respond now with the single JSON object."
    )


# --- response parsing -----------------------------------------------


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1 :] if nl != -1 else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _parse_response(stdout: str) -> LLMResponse:
    # Outer envelope: `claude -p --output-format json`.
    result_text = stdout
    usage: dict[str, int] = {}
    model: str | None = None
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError:
        env = None
    if isinstance(env, dict):
        if env.get("is_error"):
            raise ClaudeCliError(f"claude returned an error: {env.get('result')!r}")
        result_text = str(env.get("result", ""))
        u = env.get("usage") or {}
        usage = {
            "prompt_tokens": int(u.get("input_tokens", 0) or 0),
            "completion_tokens": int(u.get("output_tokens", 0) or 0),
        }
        model = next(iter(env.get("modelUsage", {}) or {}), None)

    # Inner contract: a single {"tool_call": …} or {"content": …} object.
    try:
        obj = json.loads(_strip_fences(result_text))
    except json.JSONDecodeError:
        return LLMResponse(content=result_text, model=model, usage=usage)
    if isinstance(obj, dict) and isinstance(obj.get("tool_call"), dict):
        tc = obj["tool_call"]
        return LLMResponse(
            content="",
            tool_calls=(
                ToolCall(
                    id=str(uuid4()),
                    name=str(tc.get("name", "")),
                    args=dict(tc.get("args") or {}),
                ),
            ),
            finish_reason=FinishReason.TOOL_CALLS,
            model=model,
            usage=usage,
        )
    if isinstance(obj, dict) and "content" in obj:
        return LLMResponse(content=str(obj["content"]), model=model, usage=usage)
    return LLMResponse(content=result_text, model=model, usage=usage)
