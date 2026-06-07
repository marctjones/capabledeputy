"""Terminal capability detection.

Used by the chat REPL + (future) surface-convergence work (#15) to
decide whether to emit modern-terminal-only escape sequences:

  - OSC 8 hyperlinks  → Ghostty, kitty, iTerm2, WezTerm, Alacritty, modern xterm
  - OSC 52 clipboard  → most of the above
  - Sixel graphics    → WezTerm, mintty, some xterm builds
  - Kitty graphics    → kitty, Ghostty (via kitty-protocol shim)
  - 24-bit color      → COLORTERM=truecolor terminals

Detection prefers env-var heuristics over escape-sequence probes,
because probes require a TTY (breaks under ssh-without-tty, pytest
capture, etc.). Env-var heuristics are best-effort but reliable for
the terminals that matter.

Foundation for issues #15, #17, #18, #19, #20. Standalone module
(no imports from chat.py / textual) so future surface-convergence
work can rely on it from any layer.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalCaps:
    """Snapshot of detected terminal capabilities. Captured once at
    chat startup; cached for the session's lifetime."""

    # Identity
    term: str  # $TERM (e.g. xterm-256color, xterm-ghostty, dumb)
    term_program: str  # $TERM_PROGRAM (Ghostty / iTerm.app / WezTerm / vscode / ...)
    colorterm: str  # $COLORTERM (truecolor / 24bit / empty)

    # Capabilities
    is_tty: bool  # stdout connected to a terminal at all?
    truecolor: bool  # 24-bit color supported
    hyperlinks: bool  # OSC 8 supported
    clipboard: bool  # OSC 52 supported
    graphics_sixel: bool  # sixel graphics
    graphics_kitty: bool  # kitty graphics protocol
    mouse: bool  # mouse reporting available

    # Named terminal (for nicer messages + targeted optimizations)
    family: str  # "ghostty" | "kitty" | "iterm2" | "wezterm" | "alacritty" | "xterm" | "vscode" | "dumb" | "unknown"  # noqa: E501


def _has_env_value(name: str, *needles: str) -> bool:
    val = (os.environ.get(name) or "").lower()
    return any(n in val for n in needles)


def _is_dumb() -> bool:
    """Detect terminals that genuinely don't support escape sequences
    (TERM=dumb is the classic; ssh without -t can also strip
    capabilities)."""
    term = (os.environ.get("TERM") or "").lower()
    return term == "dumb" or term == ""


def _family() -> str:
    """Name the terminal so we can apply targeted heuristics and
    print friendlier messages."""
    if _is_dumb():
        return "dumb"
    term_program = (os.environ.get("TERM_PROGRAM") or "").lower()
    term = (os.environ.get("TERM") or "").lower()

    if "ghostty" in term_program or "ghostty" in term:
        return "ghostty"
    if "kitty" in term_program or "kitty" in term or os.environ.get("KITTY_WINDOW_ID"):
        return "kitty"
    if "iterm" in term_program or "iterm" in term:
        return "iterm2"
    if "wezterm" in term_program or "wezterm" in term:
        return "wezterm"
    if "alacritty" in term_program or "alacritty" in term:
        return "alacritty"
    if "vscode" in term_program:
        return "vscode"
    if "xterm" in term:
        return "xterm"
    return "unknown"


def detect() -> TerminalCaps:
    """Snapshot the current process's terminal capabilities."""
    term = os.environ.get("TERM") or ""
    term_program = os.environ.get("TERM_PROGRAM") or ""
    colorterm = os.environ.get("COLORTERM") or ""
    is_tty = bool(sys.stdout.isatty())
    fam = _family()

    # Truecolor: explicit COLORTERM signal is authoritative. Some
    # terminals (kitty, Ghostty, modern iTerm2) support truecolor but
    # don't set COLORTERM — name-match those families.
    truecolor = (
        _has_env_value("COLORTERM", "truecolor", "24bit")
        or fam in ("ghostty", "kitty", "iterm2", "wezterm", "alacritty")
    )

    # Hyperlinks (OSC 8): supported by every named modern family +
    # current xterm. Older xterm builds may not have it but we err on
    # "yes" for xterm because the escape is silently swallowed on
    # non-supporting terminals (no broken rendering).
    hyperlinks = (
        fam in ("ghostty", "kitty", "iterm2", "wezterm", "alacritty", "xterm", "vscode")
        and is_tty
    )

    # Clipboard (OSC 52): same set as hyperlinks, modulo vscode which
    # doesn't typically support it without explicit setting.
    clipboard = (
        fam in ("ghostty", "kitty", "iterm2", "wezterm", "alacritty", "xterm")
        and is_tty
    )

    # Graphics: separate protocols.
    #   sixel: WezTerm, mintty, some xterm builds
    #   kitty: kitty (native), Ghostty (kitty-protocol shim)
    graphics_sixel = fam in ("wezterm",) and is_tty
    graphics_kitty = fam in ("kitty", "ghostty") and is_tty

    # Mouse reporting available in essentially every modern terminal.
    mouse = is_tty and not _is_dumb()

    return TerminalCaps(
        term=term,
        term_program=term_program,
        colorterm=colorterm,
        is_tty=is_tty,
        truecolor=truecolor,
        hyperlinks=hyperlinks,
        clipboard=clipboard,
        graphics_sixel=graphics_sixel,
        graphics_kitty=graphics_kitty,
        mouse=mouse,
        family=fam,
    )


# Module-level cache. Detection is cheap but the values don't change
# within a process. Callers that want a fresh detection (e.g. tests)
# call `detect()` directly.
_CACHED: TerminalCaps | None = None


def caps() -> TerminalCaps:
    """Cached terminal capabilities for the current process."""
    global _CACHED
    if _CACHED is None:
        _CACHED = detect()
    return _CACHED


def reset_cache() -> None:
    """Drop the cached detection — used by tests that mutate env."""
    global _CACHED
    _CACHED = None
