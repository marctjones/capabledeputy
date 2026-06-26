"""Enrich MCP tool results with inline terminal media and MCP image blocks.

Used by the control client (Codex, Grok, Claude Code, etc.) so hosts
running in Ghostty/kitty/iTerm2 can show trusted agent markdown images
inline, while capable MCP UIs can render ``ImageContent`` attachments.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from rich.console import Console

from capabledeputy.cli.markdown_media import (
    render_markdown_chunk,
    split_markdown_segments,
)
from capabledeputy.cli.terminal_graphics import (
    emit_inline_image,
    inline_graphics_enabled,
    resolve_trusted_image_source,
)

_IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"},
)
_IMAGE_PATH_KEYS = frozenset(
    {"path", "image_path", "file", "image", "screenshot", "plot", "chart", "output_path"},
)
_MAX_MCP_IMAGE_BYTES = 4 * 1024 * 1024
_AGENT_TEXT_KEYS = ("content", "partial_content", "message", "text", "markdown")
_TOOL_OUTPUT_KEYS = ("output", "result", "turn", "history", "events")


def terminal_media_enabled() -> bool:
    """Whether to append terminal graphics escapes to MCP text blocks."""
    flag = (os.environ.get("CAPDEP_TERMINAL_MEDIA") or "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    # Default on for graphics-capable terminal families (including piped
    # MCP subprocesses that inherit Ghostty/kitty TERM_PROGRAM).
    return inline_graphics_enabled()


def format_terminal_agent_markdown(content: str) -> str:
    """Render trusted markdown to terminal text, including inline graphics."""
    if not content.strip():
        return ""
    parts: list[str] = []
    graphics = inline_graphics_enabled()
    for segment in split_markdown_segments(content):
        if segment.kind == "markdown":
            if not segment.body.strip():
                continue
            console = Console(width=100, force_terminal=True, color_system="truecolor")
            with console.capture() as capture:
                console.print(render_markdown_chunk(segment.body))
            parts.append(capture.get().rstrip())
            continue
        path = resolve_trusted_image_source(segment.body)
        if path is None:
            alt = segment.alt or "image"
            parts.append(f"(image unavailable: {alt} — {segment.body})")
            continue
        if graphics:
            seq = emit_inline_image(path)
            if seq:
                parts.append(seq.rstrip())
                if segment.alt:
                    parts.append(segment.alt)
                continue
        parts.append(f"(image: {path})")
    return "\n".join(part for part in parts if part).strip()


def image_content_from_path(path: Path, *, alt: str = "") -> mcp_types.ImageContent | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > _MAX_MCP_IMAGE_BYTES:
        return None
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"):
        return None
    meta = {"io.capabledeputy/alt": alt} if alt else None
    return mcp_types.ImageContent(
        type="image",
        data=base64.standard_b64encode(data).decode("ascii"),
        mimeType=mime,
        **({"_meta": meta} if meta else {}),  # pyright: ignore[reportArgumentType]
    )


def iter_image_sources_in_value(value: Any) -> Iterator[tuple[str, str]]:
    """Yield ``(source, alt)`` image references from nested daemon payloads."""
    if isinstance(value, str):
        for segment in split_markdown_segments(value):
            if segment.kind == "image":
                yield segment.body, segment.alt
        stripped = value.strip()
        if _looks_like_image_path(stripped):
            yield stripped, ""
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _IMAGE_PATH_KEYS and isinstance(child, str) and _looks_like_image_path(child):
                yield child, str(value.get("alt") or value.get("title") or "")
            else:
                yield from iter_image_sources_in_value(child)
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_image_sources_in_value(item)


def _looks_like_image_path(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    if raw.startswith(("http://", "https://", "file://", "~/", "/")):
        suffix = Path(raw.split("?", 1)[0]).suffix.lower()
        return suffix in _IMAGE_EXTENSIONS or raw.startswith(("http://", "https://"))
    return Path(raw).suffix.lower() in _IMAGE_EXTENSIONS


def collect_media_from_result(result: Any) -> tuple[list[str], list[mcp_types.ImageContent]]:
    """Collect terminal markdown sections and MCP image blocks."""
    terminal_sections: list[str] = []
    images: list[mcp_types.ImageContent] = []
    seen_paths: set[str] = set()

    for text in _iter_agent_text_blobs(result):
        rendered = format_terminal_agent_markdown(text)
        if rendered and (_has_markdown_media(text) or rendered != text.strip()):
            terminal_sections.append(rendered)

    for source, alt in iter_image_sources_in_value(result):
        path = resolve_trusted_image_source(source)
        if path is None:
            continue
        key = str(path.resolve())
        if key in seen_paths:
            continue
        seen_paths.add(key)
        image = image_content_from_path(path, alt=alt)
        if image is not None:
            images.append(image)

    return terminal_sections, images


def _has_markdown_media(text: str) -> bool:
    if "![" in text and "](" in text:
        return True
    return "```" in text


def _iter_agent_text_blobs(result: Any) -> Iterator[str]:
    if isinstance(result, str):
        if result.strip():
            yield result
        return
    if not isinstance(result, dict):
        return
    for key in _AGENT_TEXT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            yield value
    for key in _TOOL_OUTPUT_KEYS:
        child = result.get(key)
        if child is not None:
            yield from _iter_agent_text_blobs(child)
    history = result.get("history")
    if isinstance(history, list):
        for turn in history:
            if isinstance(turn, dict) and str(turn.get("role") or "") in {"agent", "assistant"}:
                yield from _iter_agent_text_blobs(turn)
    events = result.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if isinstance(payload, dict):
                yield from _iter_agent_text_blobs(payload)
                result_obj = payload.get("result")
                if isinstance(result_obj, dict):
                    yield from _iter_agent_text_blobs(result_obj)


def build_mcp_result(
    result: Any,
    *,
    meta: dict[str, Any] | None = None,
    is_error: bool = False,
) -> mcp_types.CallToolResult:
    """Build an MCP tool result with JSON text, optional terminal view, images."""
    structured = result if isinstance(result, dict) else None
    if isinstance(result, dict | list):
        text = json.dumps(result, indent=2)
    else:
        text = str(result)

    content: list[mcp_types.TextContent | mcp_types.ImageContent] = [
        mcp_types.TextContent(type="text", text=text),
    ]

    if not is_error:
        terminal_sections, images = collect_media_from_result(result)
        if terminal_sections and terminal_media_enabled():
            rendered = "\n\n--- CapDep terminal view ---\n\n" + "\n\n".join(terminal_sections)
            content.append(mcp_types.TextContent(type="text", text=rendered))
        content.extend(images)

    call_meta = dict(meta or {})
    if not is_error and content and len(content) > 1:
        call_meta["io.capabledeputy/terminal_media"] = terminal_media_enabled()
        call_meta["io.capabledeputy/image_blocks"] = sum(
            1 for block in content if isinstance(block, mcp_types.ImageContent)
        )

    return mcp_types.CallToolResult(
        content=content,
        structuredContent=structured,
        isError=is_error,
        **({"_meta": call_meta} if call_meta else {}),  # pyright: ignore[reportArgumentType]
    )