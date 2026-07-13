"""Tests for MCP inline media enrichment."""

from __future__ import annotations

import json
from pathlib import Path

import mcp.types as mcp_types
import pytest

from capabledeputy.cli import terminal_caps
from capabledeputy.mcp_server.media_results import (
    build_mcp_result,
    collect_media_from_result,
    format_terminal_agent_markdown,
    image_content_from_path,
    terminal_media_enabled,
)


def test_build_mcp_result_includes_json_and_terminal_view(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    terminal_caps.reset_cache()
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    image = tmp_path / "plot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n\x00" * 20)

    payload = {
        "content": f"Here is the chart:\n\n![plot]({image})\n",
        "iterations": 1,
    }
    result = build_mcp_result(payload, meta={"io.capabledeputy/surface": "control"})

    assert result.isError is False
    assert len(result.content) >= 2
    assert isinstance(result.content[0], mcp_types.TextContent)
    assert json.loads(result.content[0].text)["content"].startswith("Here is the chart")
    terminal_blocks = [
        block for block in result.content[1:] if isinstance(block, mcp_types.TextContent)
    ]
    image_blocks = [block for block in result.content if isinstance(block, mcp_types.ImageContent)]
    assert terminal_blocks or image_blocks
    if terminal_media_enabled():
        assert any("CapDep terminal view" in block.text for block in terminal_blocks)
    assert image_blocks
    assert result.meta is not None
    assert result.meta["io.capabledeputy/commonmark"]["sanitized"] is True
    surfaces = {
        item["surface"] for item in result.meta["io.capabledeputy/commonmark"]["capabilities"]
    }
    assert {"CapDepMac", "CLI/TUI", "MCP-control", "Plain/log surfaces"} <= surfaces


def test_image_content_from_path_encodes_png(tmp_path: Path) -> None:
    image = tmp_path / "a.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    content = image_content_from_path(image, alt="chart")
    assert content is not None
    assert content.mimeType == "image/png"
    assert content.data


def test_collect_media_from_tool_output_path(tmp_path: Path) -> None:
    image = tmp_path / "shot.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    payload = {"decision": "allow", "output": {"path": str(image), "alt": "shot"}}
    _, images = collect_media_from_result(payload)
    assert len(images) == 1
    assert images[0].mimeType == "image/jpeg"


def test_format_terminal_agent_markdown_renders_code_block() -> None:
    rendered = format_terminal_agent_markdown("```python\nprint(1)\n```")
    # rich syntax-highlights the code block with per-token ANSI color codes, so
    # the raw string interleaves escapes with the source. Strip ANSI before the
    # content check so the assertion is robust to rich version / color settings.
    import re

    plain = re.sub(r"\x1b\[[0-9;]*m", "", rendered)
    assert "print(1)" in plain


def test_format_terminal_agent_markdown_sanitizes_commonmark() -> None:
    rendered = format_terminal_agent_markdown("<b>bold</b> \x1b[31mred\x1b[0m")
    assert "<b>" not in rendered
    assert "\x1b" not in rendered
    assert "bold red" in " ".join(rendered.split())


def test_terminal_media_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAPDEP_TERMINAL_MEDIA", "0")
    assert terminal_media_enabled() is False
