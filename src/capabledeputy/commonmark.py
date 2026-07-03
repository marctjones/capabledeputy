"""Shared CommonMark contract, sanitization, and fallback helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

try:
    from markdown_it import MarkdownIt
except Exception:  # pragma: no cover - import is available through Rich
    MarkdownIt = None  # type: ignore[assignment]


SurfaceLevel = Literal["rich", "terminal", "structured", "plain"]

_ANSI_RE = re.compile(
    r"(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b.)",
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>\n]*>")
_LINK_TARGET_RE = re.compile(r"(\]\()([^)]+)(\))")
_SAFE_SCHEMES = {"", "http", "https", "file", "mailto", "capdep"}
_CODE_FENCE_RE = re.compile(r"^(```|~~~)")


@dataclass(frozen=True)
class CommonMarkSurfaceCapability:
    surface: str
    level: SurfaceLevel
    renders: tuple[str, ...]
    degrades: tuple[str, ...]
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "level": self.level,
            "renders": list(self.renders),
            "degrades": list(self.degrades),
            "notes": self.notes,
        }


COMMONMARK_CAPABILITIES: tuple[CommonMarkSurfaceCapability, ...] = (
    CommonMarkSurfaceCapability(
        surface="CapDepMac",
        level="rich",
        renders=(
            "paragraphs",
            "emphasis",
            "links",
            "lists",
            "blockquotes",
            "inline_code",
            "fenced_code",
            "trusted_image_attachments",
        ),
        degrades=("raw_html", "unsafe_links", "large_tables"),
        notes="Swift AttributedString renders prose; code/images use typed chat blocks.",
    ),
    CommonMarkSurfaceCapability(
        surface="CLI/TUI",
        level="terminal",
        renders=(
            "paragraphs",
            "emphasis",
            "links_when_supported",
            "lists",
            "blockquotes",
            "inline_code",
            "fenced_code",
            "trusted_terminal_images_when_supported",
        ),
        degrades=("raw_html", "unsafe_links", "wide_tables", "unsupported_images"),
        notes="Rich renderables are width-aware; plain output remains deterministic.",
    ),
    CommonMarkSurfaceCapability(
        surface="MCP-control",
        level="structured",
        renders=("text_content", "image_content", "structured_content"),
        degrades=("raw_html", "unsafe_links", "terminal_escapes"),
        notes="Preserves sanitized CommonMark text for hosts that render Markdown.",
    ),
    CommonMarkSurfaceCapability(
        surface="Plain/log surfaces",
        level="plain",
        renders=("plain_text", "code_text", "link_text"),
        degrades=("formatting", "tables", "images"),
        notes="No terminal escapes or raw HTML are emitted.",
    ),
)


def capability_matrix() -> list[dict[str, Any]]:
    return [capability.to_dict() for capability in COMMONMARK_CAPABILITIES]


def sanitize_commonmark_source(source: str) -> str:
    """Return CommonMark safe for client rendering.

    The sanitizer is intentionally conservative: it removes terminal control
    sequences and raw HTML outside fenced code blocks, and neutralizes unsafe
    URI schemes while leaving ordinary CommonMark syntax intact.
    """
    text = _CONTROL_RE.sub("", _ANSI_RE.sub("", source)).replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = _strip_html_outside_code_fences(text)
    text = _LINK_TARGET_RE.sub(_sanitize_link_target, text)
    return text.strip()


def plain_text_from_commonmark(source: str) -> str:
    """Plain fallback for low-capability clients and logs."""
    sanitized = sanitize_commonmark_source(source)
    if not sanitized:
        return ""
    if MarkdownIt is None:
        return _HTML_TAG_RE.sub("", sanitized)
    parser = MarkdownIt("commonmark", {"html": False})
    parts: list[str] = []
    for token in parser.parse(sanitized):
        _append_plain_token(parts, token)
    text = "".join(parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _append_plain_token(parts: list[str], token: Any) -> None:
    if token.type in {"text", "code_inline", "code_block", "fence"}:
        parts.append(token.content)
    elif token.type in {"softbreak", "hardbreak", "paragraph_close", "heading_close"}:
        parts.append("\n")
    elif token.type == "list_item_open":
        parts.append("- ")
    elif token.type == "inline":
        for child in token.children or []:
            _append_plain_token(parts, child)


def _strip_html_outside_code_fences(text: str) -> str:
    lines: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if _CODE_FENCE_RE.match(stripped):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            lines.append(line)
            continue
        lines.append(line if in_fence else _HTML_TAG_RE.sub("", line))
    return "\n".join(lines)


def _sanitize_link_target(match: re.Match[str]) -> str:
    target = match.group(2).strip()
    scheme = urlsplit(target).scheme.lower()
    if scheme not in _SAFE_SCHEMES:
        return f"{match.group(1)}unsafe-link{match.group(3)}"
    return f"{match.group(1)}{target}{match.group(3)}"
