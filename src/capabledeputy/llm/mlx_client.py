"""LLMClient backed by Apple's MLX LM runtime on Apple Silicon.

This adapter keeps the CapableDeputy message/tool abstractions intact
while running generation locally through `mlx-lm`. Tool calls remain
instruction-followed rather than native API primitives, but prompts are
rendered through the model's own chat template when available. That
keeps Qwen-family MLX models aligned with their intended formatting and
avoids the prompt-echo/pathology that generic plain-text wrappers can
trigger.

MLX is the preferred default on macOS because it is Apple-native and
keeps almost all local planning/extraction traffic on-device.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import AsyncIterator
from importlib import import_module
from typing import Any, ClassVar
from uuid import uuid4

from anyio.to_thread import run_sync

from capabledeputy.llm.types import (
    FinishReason,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolDescription,
)

DEFAULT_MLX_MODEL = "Qwen/Qwen3-4B-MLX-4bit"

_TOOL_INSTRUCTION = (
    "You have access to these tools:\n\n{tool_descriptions}\n\n"
    "To call one or more tools, respond with ONLY a JSON object on a "
    "single line, with this exact shape:\n"
    '{{"tool_calls": [{{"id": "<unique_id>", "name": "<tool_name>", '
    '"args": {{...}}}}]}}\n\n'
    "If no tool call is needed, respond with normal natural-language "
    "text and nothing else."
)


def _compact_schema_summary(schema: dict[str, Any]) -> str:
    props = schema.get("properties") if isinstance(schema, dict) else None
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(props, dict) or not props:
        return "{}"
    req_set = set(required or [])
    parts: list[str] = []
    for key, spec in props.items():
        if not isinstance(spec, dict):
            parts.append(f"{key}?")
            continue
        typ = str(spec.get("type", "any"))
        suffix = "" if key in req_set else "?"
        parts.append(f"{key}{suffix}: {typ}")
    summary = ", ".join(parts)
    if req_set:
        summary += f" (required: {', '.join(sorted(req_set))})"
    return "{" + summary + "}"


def _format_tool_descriptions(tools: list[ToolDescription]) -> str:
    if not tools:
        return "(no tools available)"
    lines: list[str] = []
    for tool in tools:
        schema = _compact_schema_summary(tool.parameters_schema)
        lines.append(f"- {tool.name}: {tool.description}  args={schema}")
    return "\n".join(lines)


def _format_messages_for_prompt(messages: list[Message]) -> str:
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
    body = _format_messages_for_prompt(messages)
    return f"{instruction}\n\n---\n\n{body}"


def _tool_instruction_for_chat(tools: list[ToolDescription]) -> str:
    return _TOOL_INSTRUCTION.format(
        tool_descriptions=_format_tool_descriptions(tools),
    )


def _messages_to_chat(
    messages: list[Message],
    tools: list[ToolDescription],
) -> list[dict[str, str]]:
    """Render CapDep messages into a provider-neutral chat transcript.

    We keep roles limited to `system`/`user`/`assistant` because those
    are broadly supported by MLX chat templates. Tool results are folded
    back into a user turn that explicitly names the tool and call id.
    """
    chat: list[dict[str, str]] = []
    if tools:
        tool_instruction = _tool_instruction_for_chat(tools)
        injected = False
        for msg in messages:
            if msg.role == Role.SYSTEM:
                content = msg.content
                if not injected:
                    content = f"{content}\n\n{tool_instruction}" if content else tool_instruction
                    injected = True
                chat.append({"role": "system", "content": content})
            elif msg.role == Role.USER:
                chat.append({"role": "user", "content": msg.content})
            elif msg.role == Role.ASSISTANT:
                if msg.tool_calls:
                    tc_dump = json.dumps(
                        {
                            "tool_calls": [
                                {"id": tc.id, "name": tc.name, "args": tc.args}
                                for tc in msg.tool_calls
                            ],
                        },
                    )
                    chat.append({"role": "assistant", "content": tc_dump})
                else:
                    chat.append({"role": "assistant", "content": msg.content})
            elif msg.role == Role.TOOL:
                chat.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool result ({msg.name or 'unknown'}, call_id={msg.tool_call_id})\n"
                            f"{msg.content}"
                        ),
                    },
                )
        if not injected:
            chat.insert(0, {"role": "system", "content": tool_instruction})
        return chat

    for msg in messages:
        if msg.role == Role.SYSTEM:
            chat.append({"role": "system", "content": msg.content})
        elif msg.role == Role.USER:
            chat.append({"role": "user", "content": msg.content})
        elif msg.role == Role.ASSISTANT:
            chat.append({"role": "assistant", "content": msg.content})
        elif msg.role == Role.TOOL:
            chat.append(
                {
                    "role": "user",
                    "content": (
                        f"Tool result ({msg.name or 'unknown'}, call_id={msg.tool_call_id})\n"
                        f"{msg.content}"
                    ),
                },
            )
    return chat


_THINK_BLOCK_RE = re.compile(
    r"<(?:think|thinking)>.*?</(?:think|thinking)>",
    re.DOTALL | re.IGNORECASE,
)
_LEADING_UNCLOSED_THINK_RE = re.compile(
    r"^\s*<(?:think|thinking)>.*?(?=(?:```(?:json)?\s*)?\{|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_reasoning_blocks(text: str) -> str:
    """Remove model-emitted reasoning blocks before parsing/rendering.

    Some models emit `<think>...</think>` or `<thinking>...</thinking>`
    when reasoning mode is enabled. CapDep should never feed those
    traces into its JSON parsers or surface them as if they were the
    final structured answer.
    """
    without_closed_blocks = _THINK_BLOCK_RE.sub("", text)
    without_unclosed_leading_block = _LEADING_UNCLOSED_THINK_RE.sub("", without_closed_blocks)
    return without_unclosed_leading_block.strip()


def _strip_markdown_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()


def _first_json_object(text: str) -> str | None:
    """Extract the first balanced JSON object from model output."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _json_candidate(text: str) -> str:
    stripped = _strip_markdown_fence(_strip_reasoning_blocks(text))
    if stripped.startswith("{"):
        return stripped
    if "tool_calls" in stripped:
        extracted = _first_json_object(stripped)
        if extracted is not None:
            return extracted
    return stripped


def _try_parse_tool_calls(text: str) -> tuple[ToolCall, ...] | None:
    stripped = _json_candidate(text)
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


def finalize_mlx_text(
    text: str,
    tools: list[ToolDescription],
    *,
    model: str,
) -> LLMResponse:
    """Turn raw MLX generation output into a typed LLMResponse."""
    if tools:
        return parse_mlx_response(text, model=model)
    return LLMResponse(
        content=_strip_markdown_fence(_strip_reasoning_blocks(text)),
        finish_reason=FinishReason.STOP,
        model=model,
    )


def _strip_leading_tool_json(text: str) -> str:
    """Drop MLX tool-call JSON prefixes that leaked before user-facing prose."""
    stripped = _strip_markdown_fence(_strip_reasoning_blocks(text))
    if _try_parse_tool_calls(stripped) is not None:
        return ""
    if "tool_calls" not in stripped:
        return stripped
    for marker in ("}]}", "}]"):
        idx = stripped.find(marker)
        if idx == -1:
            continue
        tail = stripped[idx + len(marker) :].lstrip()
        if tail and not tail.startswith("{"):
            return tail
    return stripped


def parse_mlx_response(text: str, *, model: str) -> LLMResponse:
    tool_calls = _try_parse_tool_calls(text)
    if tool_calls is not None:
        return LLMResponse(
            content="",
            tool_calls=tool_calls,
            finish_reason=FinishReason.TOOL_CALLS,
            model=model,
        )
    return LLMResponse(
        content=_strip_leading_tool_json(text),
        finish_reason=FinishReason.STOP,
        model=model,
    )


class MLXLLMClient:
    _cache_lock: ClassVar[threading.Lock] = threading.Lock()
    _loaded_models: ClassVar[dict[str, tuple[Any, Any]]] = {}

    def __init__(
        self,
        model: str = DEFAULT_MLX_MODEL,
        *,
        max_tokens: int = 2048,
        trust_remote_code: bool = False,
        enable_thinking: bool = False,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._trust_remote_code = trust_remote_code
        self._enable_thinking = enable_thinking

    async def respond(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
        *,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        accumulated = ""
        async for chunk in self.respond_streaming(
            messages,
            tools,
            max_tokens=max_tokens,
        ):
            accumulated += chunk
        return finalize_mlx_text(accumulated, tools, model=self._model)

    async def respond_streaming(
        self,
        messages: list[Message],
        tools: list[ToolDescription],
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield incremental text deltas as MLX generates them."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None | BaseException] = asyncio.Queue()
        limit = max_tokens if max_tokens is not None else self._max_tokens

        trace_ctx = None
        try:
            from capabledeputy.debug.chat_trace import snapshot_context

            trace_ctx = snapshot_context()
        except Exception:
            trace_ctx = None

        def worker() -> None:
            try:
                model, tokenizer = self._load_model_sync()
                prompt = self._render_prompt(tokenizer, messages, tools)
                stream_generate = import_module("mlx_lm").stream_generate
                accumulated = ""
                for response in stream_generate(
                    model,
                    tokenizer,
                    prompt,
                    max_tokens=limit,
                ):
                    # mlx_lm yields detokenizer.last_segment per token — not
                    # cumulative text. Slicing against a running prefix corrupts
                    # output into the garbled "Ihaveolsableision..." strings.
                    delta = response.text
                    if delta:
                        prev_len = len(accumulated)
                        accumulated += delta
                        if trace_ctx is not None:
                            try:
                                from capabledeputy.debug.chat_trace import log_mlx_chunk

                                log_mlx_chunk(
                                    trace_ctx,
                                    delta=delta,
                                    cumulative=accumulated,
                                    previous_len=prev_len,
                                )
                            except Exception:
                                pass
                        loop.call_soon_threadsafe(queue.put_nowait, delta)
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except BaseException as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = await queue.get()
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    def _generate_sync(self, model: Any, tokenizer: Any, prompt: str) -> str:
        try:
            generate = import_module("mlx_lm").generate
        except RuntimeError as e:
            raise RuntimeError(
                "MLX runtime could not initialize. On macOS this usually means "
                "the current process cannot access a Metal device.",
            ) from e

        return str(
            generate(
                model,
                tokenizer,
                prompt=prompt,
                verbose=False,
                max_tokens=self._max_tokens,
            ),
        )

    def _render_prompt(
        self,
        tokenizer: Any,
        messages: list[Message],
        tools: list[ToolDescription],
    ) -> str:
        if getattr(tokenizer, "chat_template", None):
            chat = _messages_to_chat(messages, tools)
            try:
                return str(
                    tokenizer.apply_chat_template(
                        chat,
                        tokenize=False,
                        add_generation_prompt=True,
                        enable_thinking=self._enable_thinking,
                    ),
                )
            except TypeError:
                return str(
                    tokenizer.apply_chat_template(
                        chat,
                        tokenize=False,
                        add_generation_prompt=True,
                    ),
                )
        return _build_prompt(messages, tools)

    def _load_model_sync(self) -> tuple[Any, Any]:
        with self._cache_lock:
            cached = self._loaded_models.get(self._model)
            if cached is not None:
                return cached
            try:
                load = import_module("mlx_lm").load
            except RuntimeError as e:
                raise RuntimeError(
                    "MLX runtime could not initialize. On macOS this usually means "
                    "the current process cannot access a Metal device.",
                ) from e

            loaded = load(
                self._model,
                tokenizer_config={"trust_remote_code": self._trust_remote_code},
            )
            pair = (loaded[0], loaded[1])
            self._loaded_models[self._model] = pair
            return pair
