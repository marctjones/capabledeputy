"""Glyph vocabulary + semantic styles (TUI redesign §5).

"Every character is a design decision": one small, learnable set. Color is
SIGNAL, never decoration — each Decision maps to exactly one accent so the
interface stays calm and fights alert-blindness.
"""

from __future__ import annotations

from capabledeputy.policy.rules import Decision

# --- glyph vocabulary (the inline chip leaders + chrome marks) ---
GLYPH_ACTION = "◇"  # a tool call
GLYPH_ALLOW = "✓"  # allowed (quiet)
GLYPH_WARN = "⚑"  # advisory / WARN (non-blocking)
GLYPH_APPROVE = "⛔"  # needs approval (expands a card)
GLYPH_DENY = "✗"  # denied
GLYPH_OVERRIDE = "⚖"  # override required
GLYPH_TIER = "⬤"  # a tier dot on a label
GLYPH_UNTRUSTED = "●"  # untrusted provenance marker
GLYPH_GUTTER = "▏"  # left gutter bar for quarantined untrusted content
GLYPH_PROMPT = "›"  # noqa: RUF001 — the input prompt (intentional glyph, not '>')

# --- semantic styles (Rich style strings). One accent per decision. ---
STYLE_ALLOW = "dim"
STYLE_WARN = "yellow"
STYLE_APPROVE = "bold blue"
STYLE_DENY = "bold red"
STYLE_OVERRIDE = "bold magenta"
STYLE_UNTRUSTED = "yellow"
STYLE_CHROME = "white"
STYLE_GUTTER = "dim"

_DECISION_GLYPH: dict[Decision, str] = {
    Decision.ALLOW: GLYPH_ALLOW,
    Decision.WARN: GLYPH_WARN,
    Decision.REQUIRE_APPROVAL: GLYPH_APPROVE,
    Decision.OVERRIDE_REQUIRED: GLYPH_OVERRIDE,
    Decision.DENY: GLYPH_DENY,
}

_DECISION_STYLE: dict[Decision, str] = {
    Decision.ALLOW: STYLE_ALLOW,
    Decision.WARN: STYLE_WARN,
    Decision.REQUIRE_APPROVAL: STYLE_APPROVE,
    Decision.OVERRIDE_REQUIRED: STYLE_OVERRIDE,
    Decision.DENY: STYLE_DENY,
}


def glyph_for(decision: Decision) -> str:
    """The chip leader glyph for a decision."""
    return _DECISION_GLYPH[decision]


def style_for(decision: Decision) -> str:
    """The single semantic accent for a decision."""
    return _DECISION_STYLE[decision]
