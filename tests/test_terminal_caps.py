"""Tests for the terminal capability detection module.

Foundation for issues #15, #17, #18, #19, #20. Each terminal family's
detection is verified against the env vars it sets in practice.
"""

from __future__ import annotations

import pytest

from capabledeputy.cli import terminal_caps


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the env vars that influence detection so each test starts
    from a known baseline. Tests selectively set what they need."""
    for k in (
        "TERM",
        "TERM_PROGRAM",
        "COLORTERM",
        "KITTY_WINDOW_ID",
    ):
        monkeypatch.delenv(k, raising=False)
    terminal_caps.reset_cache()


def test_dumb_terminal_reports_minimal_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    c = terminal_caps.detect()
    assert c.family == "dumb"
    assert c.truecolor is False
    assert c.hyperlinks is False
    assert c.clipboard is False
    assert c.mouse is False


def test_ghostty_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c = terminal_caps.detect()
    assert c.family == "ghostty"
    assert c.truecolor is True
    assert c.hyperlinks is True
    assert c.clipboard is True
    assert c.graphics_kitty is True


def test_kitty_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-kitty")
    monkeypatch.setenv("KITTY_WINDOW_ID", "1")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c = terminal_caps.detect()
    assert c.family == "kitty"
    assert c.truecolor is True
    assert c.hyperlinks is True
    assert c.graphics_kitty is True
    assert c.graphics_sixel is False  # kitty uses its own protocol


def test_iterm2_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c = terminal_caps.detect()
    assert c.family == "iterm2"
    assert c.hyperlinks is True


def test_wezterm_detected_with_sixel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c = terminal_caps.detect()
    assert c.family == "wezterm"
    assert c.graphics_sixel is True
    assert c.graphics_kitty is False


def test_vscode_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """VSCode's integrated terminal supports hyperlinks but not OSC 52."""
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c = terminal_caps.detect()
    assert c.family == "vscode"
    assert c.hyperlinks is True
    assert c.clipboard is False


def test_truecolor_via_colorterm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c = terminal_caps.detect()
    assert c.truecolor is True


def test_not_a_tty_disables_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """When stdout isn't a tty (pytest capture, piped output, ssh
    without -t), all interactive features fall back to off."""
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    c = terminal_caps.detect()
    assert c.family == "ghostty"  # identity still detected
    assert c.hyperlinks is False  # but interactive features off
    assert c.clipboard is False
    assert c.mouse is False


def test_caps_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """caps() returns the same snapshot across calls within a process."""
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c1 = terminal_caps.caps()
    # Mutating env after caps() doesn't change the cached result
    monkeypatch.setenv("TERM", "dumb")
    c2 = terminal_caps.caps()
    assert c1 is c2


def test_reset_cache_picks_up_env_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """reset_cache() lets tests rerun detection against new env."""
    monkeypatch.setenv("TERM", "xterm-ghostty")
    monkeypatch.setenv("TERM_PROGRAM", "ghostty")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c1 = terminal_caps.caps()
    assert c1.family == "ghostty"
    # Change BOTH TERM and TERM_PROGRAM — the detection looks at both,
    # so flipping only one (when TERM still contains "ghostty") would
    # still report ghostty. Real env always shifts together.
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("TERM_PROGRAM", "WezTerm")
    terminal_caps.reset_cache()
    c2 = terminal_caps.caps()
    assert c2.family == "wezterm"


def test_unknown_terminal_minimal_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown terminals get the most conservative caps — assume the
    fewest features so we don't emit unsupported escapes."""
    monkeypatch.setenv("TERM", "screen.linux")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    c = terminal_caps.detect()
    assert c.family == "unknown"
    assert c.hyperlinks is False
    assert c.clipboard is False
