"""Tests for trusted markdown + inline terminal media rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console
from rich.text import Text

from capabledeputy.cli import terminal_caps
from capabledeputy.cli.markdown_media import (
    render_trusted_markdown,
    split_markdown_segments,
)
from capabledeputy.cli.terminal_graphics import (
    kitty_image_sequence,
    resolve_trusted_image_source,
)


def test_split_markdown_segments_preserves_order() -> None:
    content = "Intro\n\n![chart](/tmp/a.png)\n\nTail"
    segments = split_markdown_segments(content)
    assert [s.kind for s in segments] == ["markdown", "image", "markdown"]
    assert segments[1].body == "/tmp/a.png"
    assert segments[1].alt == "chart"


def test_resolve_trusted_image_source_expands_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    home_image = tmp_path / "pic.png"
    home_image.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert resolve_trusted_image_source(str(image)) == image.resolve()
    assert resolve_trusted_image_source(f"~/pic.png") == home_image.resolve()


def test_kitty_image_sequence_chunks_payload() -> None:
    path = Path("/tmp/fake.png")
    # monkeypatch read_bytes via a real temp file
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp.write(b"x" * 5000)
        tmp.flush()
        seq = kitty_image_sequence(Path(tmp.name), max_cols=40)
    assert "\x1b_G" in seq
    assert "a=T" in seq
    assert "m=1" in seq or "m=0" in seq


def test_render_trusted_markdown_without_graphics(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal_caps.reset_cache()
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    renderable = render_trusted_markdown("**hi** and `code`")
    console = Console(width=80, record=True, force_terminal=False)
    console.print(renderable)
    out = console.export_text()
    assert "hi" in out


def test_render_trusted_markdown_emits_kitty_for_local_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    terminal_caps.reset_cache()
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    image = tmp_path / "plot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    from capabledeputy.cli.terminal_graphics import emit_inline_image, inline_graphics_enabled

    assert inline_graphics_enabled()
    assert "\x1b_G" in emit_inline_image(image)