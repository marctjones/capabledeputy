"""Untrusted-content quarantine rendering (TUI redesign §8.1 #3 / hard req #2).

Untrusted content — email bodies, tool results, web pages, any model-relayed
text — must NEVER be able to impersonate trusted chrome: it cannot draw a fake
decision card, forge the status line, inject terminal color/cursor control,
emit a clickable hyperlink (OSC 8) or an inline image (kitty/iTerm/sixel). This
module forces every untrusted block into one safe shape: escape-stripped
plaintext with a permanent left gutter. It is the UI's analogue of the
untrusted-egress floor — a structural property, not a cosmetic one.

`strip_terminal_sequences` is the pure, load-bearing primitive; `quarantine`
wraps it into a gutter-marked Rich `Text` built from a PLAIN string, so Rich
console-markup in the content is shown literally, never interpreted.
"""

from __future__ import annotations

import re

from rich.text import Text

from capabledeputy.tui.inline.glyphs import GLYPH_GUTTER, STYLE_GUTTER

# OSC: ESC ] ... terminated by BEL (\x07) or ST (ESC \). Covers hyperlinks
# (OSC 8), window title, and the iTerm/kitty image protocols.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
# DCS / SOS / PM / APC: ESC P|X|^|_ ... ST. Sixel images ride DCS.
_DCS_SOS_PM_APC = re.compile(r"\x1b[PX^_][^\x1b]*\x1b\\")
# CSI: ESC [ ... final byte. Color, cursor movement, erase.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Any remaining ESC-introduced two-char sequence (e.g. ESC c reset).
_OTHER_ESC = re.compile(r"\x1b.")
# C0 controls except TAB(0x09)/LF(0x0a), DEL(0x7f), and C1 (0x80-0x9f).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_terminal_sequences(raw: str) -> str:
    """Remove every terminal control/escape sequence from untrusted text.

    Order matters: OSC and DCS/SOS/PM/APC (which embed arbitrary payloads
    terminated by ST) are removed before the generic CSI / lone-ESC passes,
    then stray control characters. Newlines and tabs survive; everything else
    that could move the cursor, set color, open a link, or paint an image is
    gone. Pure function — the anti-impersonation primitive, unit-tested.
    """
    s = _OSC.sub("", raw)
    s = _DCS_SOS_PM_APC.sub("", s)
    s = _CSI.sub("", s)
    s = _OTHER_ESC.sub("", s)
    return _CONTROL.sub("", s)


def quarantine(raw: str, *, gutter_style: str = STYLE_GUTTER) -> Text:
    """Render untrusted content as a gutter-marked, escape-stripped plaintext
    block.

    The result is a Rich `Text` assembled from a PLAIN string via `append`
    (not `Text.from_markup`), so any Rich console markup like `[red]x[/red]`
    in the content renders literally instead of being interpreted. Each line
    gets the `▏` gutter so the block is unmistakably *content*, never chrome.
    """
    clean = strip_terminal_sequences(raw)
    out = Text()
    for i, line in enumerate(clean.split("\n")):
        if i:
            out.append("\n")
        out.append(f"{GLYPH_GUTTER} ", style=gutter_style)
        out.append(line, style=gutter_style)
    return out
