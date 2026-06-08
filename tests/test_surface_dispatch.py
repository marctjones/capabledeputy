"""Tests for the surface-dispatch logic (#15 Phase B).

Verifies the --mode auto|line|rich flag picks the right surface for
each combination of (operator choice, detected terminal family,
isatty). Doesn't actually launch a Textual app or REPL — patches
the leaf launcher functions and asserts on which one was called.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from capabledeputy.cli import chat as chat_module
from capabledeputy.cli import terminal_caps


@pytest.fixture(autouse=True)
def reset_caps_cache():
    """Each test starts with a fresh capability cache."""
    terminal_caps.reset_cache()
    yield
    terminal_caps.reset_cache()


def _stub_caps(family: str, is_tty: bool = True) -> terminal_caps.TerminalCaps:
    return terminal_caps.TerminalCaps(
        term="x",
        term_program=family,
        colorterm="",
        is_tty=is_tty,
        truecolor=True,
        hyperlinks=True,
        clipboard=True,
        graphics_sixel=False,
        graphics_kitty=False,
        mouse=True,
        family=family,
    )


def test_mode_line_always_picks_line() -> None:
    with (
        patch.object(chat_module, "_repl_loop") as line,
        patch.object(
            chat_module,
            "_run_rich_surface",
        ) as rich,
    ):
        chat_module._dispatch_surface("sid", "line")
    line.assert_called_once_with("sid", no_stream=False)
    rich.assert_not_called()


def test_mode_rich_with_tty_picks_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terminal_caps, "caps", lambda: _stub_caps("ghostty"))
    with (
        patch.object(chat_module, "_repl_loop") as line,
        patch.object(
            chat_module,
            "_run_rich_surface",
        ) as rich,
    ):
        chat_module._dispatch_surface("sid", "rich")
    rich.assert_called_once_with("sid")
    line.assert_not_called()


def test_mode_rich_without_tty_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """--mode rich requires a TTY; failing to one would surprise scripts."""
    import typer

    monkeypatch.setattr(
        terminal_caps,
        "caps",
        lambda: _stub_caps("ghostty", is_tty=False),
    )
    with pytest.raises(typer.Exit):
        chat_module._dispatch_surface("sid", "rich")


def test_mode_auto_picks_rich_on_modern_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal_caps, "caps", lambda: _stub_caps("kitty"))
    with (
        patch.object(chat_module, "_repl_loop") as line,
        patch.object(
            chat_module,
            "_run_rich_surface",
        ) as rich,
    ):
        chat_module._dispatch_surface("sid", "auto")
    rich.assert_called_once_with("sid")
    line.assert_not_called()


def test_mode_auto_picks_line_on_basic_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal_caps, "caps", lambda: _stub_caps("xterm"))
    with (
        patch.object(chat_module, "_repl_loop") as line,
        patch.object(
            chat_module,
            "_run_rich_surface",
        ) as rich,
    ):
        chat_module._dispatch_surface("sid", "auto")
    line.assert_called_once_with("sid", no_stream=False)
    rich.assert_not_called()


def test_mode_auto_picks_line_on_dumb_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal_caps, "caps", lambda: _stub_caps("dumb", is_tty=False))
    with (
        patch.object(chat_module, "_repl_loop") as line,
        patch.object(
            chat_module,
            "_run_rich_surface",
        ) as rich,
    ):
        chat_module._dispatch_surface("sid", "auto")
    line.assert_called_once_with("sid", no_stream=False)
    rich.assert_not_called()


def test_mode_auto_picks_line_when_not_a_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a modern-terminal family loses to non-tty (ssh-without-tty,
    pytest stdout capture, output piped to a file). Auto picks line."""
    monkeypatch.setattr(
        terminal_caps,
        "caps",
        lambda: _stub_caps("ghostty", is_tty=False),
    )
    with (
        patch.object(chat_module, "_repl_loop") as line,
        patch.object(
            chat_module,
            "_run_rich_surface",
        ) as rich,
    ):
        chat_module._dispatch_surface("sid", "auto")
    line.assert_called_once_with("sid", no_stream=False)
    rich.assert_not_called()


def test_invalid_mode_errors() -> None:
    import typer

    with pytest.raises(typer.Exit):
        chat_module._dispatch_surface("sid", "magic")


def test_each_modern_family_picks_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    """All five families covered by the dispatcher's rich allow-list
    actually dispatch to rich."""
    for fam in ("ghostty", "kitty", "iterm2", "wezterm", "alacritty"):
        monkeypatch.setattr(terminal_caps, "caps", lambda f=fam: _stub_caps(f))
        with (
            patch.object(chat_module, "_repl_loop") as line,
            patch.object(
                chat_module,
                "_run_rich_surface",
            ) as rich,
        ):
            chat_module._dispatch_surface("sid", "auto")
        assert rich.call_count == 1, f"{fam}: expected rich; line was called"
        assert line.call_count == 0, f"{fam}: line was called when rich expected"


def test_unknown_terminal_picks_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown / weird terminal families fall back to line for safety."""
    monkeypatch.setattr(terminal_caps, "caps", lambda: _stub_caps("screen.linux"))
    with (
        patch.object(chat_module, "_repl_loop") as line,
        patch.object(
            chat_module,
            "_run_rich_surface",
        ) as rich,
    ):
        chat_module._dispatch_surface("sid", "auto")
    line.assert_called_once_with("sid", no_stream=False)
    rich.assert_not_called()
