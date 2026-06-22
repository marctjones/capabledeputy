from __future__ import annotations

from capabledeputy.cli.styles import (
    DECISION_STYLE,
    SPEAKER_GLYPH,
    STYLE_ALLOW,
    STYLE_DENY,
    STYLE_WARNING,
    decision_style,
)
from capabledeputy.policy.rules import Decision


def test_cli_styles_expose_semantic_decision_palette() -> None:
    assert SPEAKER_GLYPH
    assert DECISION_STYLE["allow"] == STYLE_ALLOW
    assert DECISION_STYLE["deny"] == STYLE_DENY
    assert DECISION_STYLE["require_approval"] == STYLE_WARNING


def test_decision_style_accepts_enum_and_string() -> None:
    assert decision_style(Decision.ALLOW) == STYLE_ALLOW
    assert decision_style("deny") == STYLE_DENY
    assert decision_style("unknown") == STYLE_WARNING
