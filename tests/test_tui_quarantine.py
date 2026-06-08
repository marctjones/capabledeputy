"""TUI redesign — untrusted-content quarantine (hard requirement #2).

The load-bearing presentation-layer safety property: untrusted content can
NEVER impersonate trusted chrome. These adversarially pin that an attacker
who controls rendered content (an email body, a tool result, model-relayed
text) cannot inject terminal control, draw a styled fake card, open a
clickable link, paint an image, or have Rich markup interpreted.
"""

from __future__ import annotations

from capabledeputy.tui.inline.glyphs import GLYPH_GUTTER
from capabledeputy.tui.inline.render import quarantine, strip_terminal_sequences

# --- the primitive: every terminal escape vector is neutralized -----


def test_strips_csi_color_codes() -> None:
    assert strip_terminal_sequences("\x1b[31mred\x1b[0m") == "red"
    assert strip_terminal_sequences("\x1b[1;34;47mfake chrome\x1b[0m") == "fake chrome"


def test_strips_osc_hyperlink_keeps_visible_text() -> None:
    # OSC 8 hyperlink: ESC ] 8 ;; URL BEL  visible  ESC ] 8 ;; BEL
    payload = "\x1b]8;;http://evil.example\x07click me\x1b]8;;\x07"
    assert strip_terminal_sequences(payload) == "click me"


def test_strips_osc_window_title_and_st_terminator() -> None:
    # OSC terminated by ST (ESC backslash) instead of BEL.
    assert strip_terminal_sequences("\x1b]0;pwn\x1b\\after") == "after"


def test_strips_dcs_sixel_image() -> None:
    # DCS (sixel image data) terminated by ST.
    assert strip_terminal_sequences("\x1bPq#0;2;0;0;0\x1b\\text") == "text"


def test_strips_control_chars_but_keeps_newline_and_tab() -> None:
    assert strip_terminal_sequences("a\x07b\x00c") == "abc"  # bell + NUL gone
    assert strip_terminal_sequences("a\nb\tc") == "a\nb\tc"  # LF + TAB survive


def test_a_styled_fake_decision_card_is_neutralized() -> None:
    """An attacker's ANSI-styled fake approval card collapses to plain text —
    it can carry the literal glyphs but never the styling that makes real
    chrome look real."""
    attack = "\x1b[1;34m⛔ approve this send?  [a] approve\x1b[0m"
    out = strip_terminal_sequences(attack)
    assert "\x1b" not in out
    assert out == "⛔ approve this send?  [a] approve"  # literal, unstyled


# --- the wrapper: gutter + literal (non-interpreted) rendering ------


def test_quarantine_applies_gutter_and_is_escape_free() -> None:
    out = quarantine("hello\nworld")
    assert "\x1b" not in out.plain
    assert out.plain.startswith(f"{GLYPH_GUTTER} hello")
    assert f"{GLYPH_GUTTER} world" in out.plain


def test_quarantine_does_not_interpret_rich_markup() -> None:
    """Rich console markup in untrusted content must render literally, not be
    parsed (else `[red]...[/]` could re-introduce styling)."""
    out = quarantine("[bold red]ALERT[/bold red] [link=http://evil]x[/link]")
    assert "[bold red]ALERT[/bold red]" in out.plain  # shown verbatim
    assert "[link=http://evil]" in out.plain
