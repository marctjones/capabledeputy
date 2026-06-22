"""Semantic Rich style vocabulary for CLI surfaces.

Color should carry product meaning, not arbitrary decoration. Keep terminal
surfaces on these named tokens so CLI, REPL, and future TUI convergence work
can change presentation without rewriting policy/status renderers.
"""

from __future__ import annotations

from capabledeputy.policy.rules import Decision

STYLE_ASSISTANT = "orange3"
STYLE_USER = "cyan"
STYLE_DIM = "dim"
STYLE_ERROR = "red"
STYLE_SUCCESS = "green"
STYLE_WARNING = "yellow"
STYLE_APPROVAL = STYLE_WARNING
STYLE_OVERRIDE = "magenta"
STYLE_DENY = STYLE_ERROR
STYLE_ALLOW = STYLE_SUCCESS

SPEAKER_GLYPH = "●"

DECISION_STYLE: dict[str, str] = {
    "allow": STYLE_ALLOW,
    "deny": STYLE_DENY,
    "require_approval": STYLE_APPROVAL,
    "override_required": STYLE_OVERRIDE,
}

RICH_DECISION_STYLE: dict[Decision, str] = {
    Decision.ALLOW: STYLE_ALLOW,
    Decision.DENY: STYLE_DENY,
    Decision.REQUIRE_APPROVAL: STYLE_APPROVAL,
    Decision.OVERRIDE_REQUIRED: STYLE_OVERRIDE,
}


def decision_style(decision: Decision | str) -> str:
    """Return the canonical CLI style for a policy decision."""
    if isinstance(decision, Decision):
        return RICH_DECISION_STYLE[decision]
    return DECISION_STYLE.get(decision, STYLE_WARNING)
