"""Trusted-agent markdown rendering with optional inline terminal media."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from rich.console import RenderableType
from rich.markdown import Markdown
from rich.text import Text

from capabledeputy.cli.terminal_caps import caps
from capabledeputy.cli.terminal_graphics import (
    emit_inline_image,
    inline_graphics_enabled,
    resolve_trusted_image_source,
)
from capabledeputy.commonmark import sanitize_commonmark_source

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


@dataclass(frozen=True)
class MarkdownSegment:
    kind: Literal["markdown", "image"]
    body: str
    alt: str = ""


def split_markdown_segments(content: str) -> list[MarkdownSegment]:
    """Split markdown into prose chunks and image references, in order."""
    sanitized = sanitize_commonmark_source(content)
    if not sanitized:
        return []
    segments: list[MarkdownSegment] = []
    cursor = 0
    for match in _IMAGE_RE.finditer(sanitized):
        if match.start() > cursor:
            prose = sanitized[cursor : match.start()].strip()
            if prose:
                segments.append(MarkdownSegment("markdown", prose))
        segments.append(MarkdownSegment("image", match.group(2).strip(), alt=match.group(1)))
        cursor = match.end()
    tail = sanitized[cursor:].strip()
    if tail:
        segments.append(MarkdownSegment("markdown", tail))
    if not segments:
        segments.append(MarkdownSegment("markdown", sanitized))
    return segments


def markdown_code_theme() -> str:
    c = caps()
    if c.truecolor and c.family in ("ghostty", "kitty", "iterm2", "wezterm", "alacritty"):
        return "one-dark"
    return "monokai"


def render_markdown_chunk(text: str) -> Markdown:
    theme = markdown_code_theme()
    c = caps()
    return Markdown(
        sanitize_commonmark_source(text),
        code_theme=theme,
        hyperlinks=c.hyperlinks,
        inline_code_lexer="python",
        inline_code_theme=theme,
    )


def iter_trusted_markdown_renderables(content: str) -> Iterator[RenderableType]:
    """Yield Rich renderables for trusted agent markdown + inline images."""
    segments = split_markdown_segments(content)
    graphics = inline_graphics_enabled()
    for segment in segments:
        if segment.kind == "markdown":
            if segment.body.strip():
                yield render_markdown_chunk(segment.body)
            continue
        path = resolve_trusted_image_source(segment.body)
        if path is None:
            alt = segment.alt or "image"
            yield Text.from_markup(
                f"[dim](image unavailable: {alt} — {segment.body})[/dim]",
            )
            continue
        if graphics:
            seq = emit_inline_image(path)
            if seq:
                yield Text.from_ansi(seq + "\n")
                if segment.alt:
                    yield Text(segment.alt, style="dim italic")
                continue
        yield Text.from_markup(
            f"[dim](inline image — open locally: {path})[/dim]",
        )


def render_trusted_markdown(content: str) -> RenderableType:
    from rich.console import Group

    items = list(iter_trusted_markdown_renderables(content))
    if not items:
        return Text("")
    if len(items) == 1:
        return items[0]
    return Group(*items)
