from __future__ import annotations

from pathlib import Path

from capabledeputy.commonmark import (
    capability_matrix,
    plain_text_from_commonmark,
    sanitize_commonmark_source,
)

FIXTURES = Path(__file__).parent / "fixtures" / "commonmark"


def test_capability_matrix_covers_client_surfaces() -> None:
    surfaces = {item["surface"]: item for item in capability_matrix()}

    assert surfaces["CapDepMac"]["level"] == "rich"
    assert surfaces["CLI/TUI"]["level"] == "terminal"
    assert surfaces["MCP-control"]["level"] == "structured"
    assert surfaces["Plain/log surfaces"]["level"] == "plain"


def test_commonmark_sanitizer_preserves_supported_blocks() -> None:
    sanitized = sanitize_commonmark_source((FIXTURES / "basic.md").read_text())

    assert "# Status" in sanitized
    assert "**bold**" in sanitized
    assert "| Name | State |" in sanitized
    assert "```python" in sanitized
    assert "<b>kept in code</b>" in sanitized
    assert "![diagram](/tmp/diagram.png)" in sanitized
    assert "![partial" in sanitized


def test_commonmark_sanitizer_neutralizes_html_control_sequences_and_bad_links() -> None:
    raw = (FIXTURES / "unsafe.md").read_text() + "\n\x1b[31mred\x1b[0m"

    sanitized = sanitize_commonmark_source(raw)

    assert "<script>" not in sanitized
    assert "javascript:alert" not in sanitized
    assert "\x1b" not in sanitized
    assert "unsafe-link" in sanitized
    assert "<strong>kept in code</strong>" in sanitized


def test_plain_text_fallback_keeps_content_without_markup() -> None:
    plain = plain_text_from_commonmark((FIXTURES / "basic.md").read_text())

    assert "Status" in plain
    assert "bold" in plain
    assert 'print("<b>kept in code</b>")' in plain
