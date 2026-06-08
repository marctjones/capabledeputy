"""TUI redesign — decision rendering from a typed PolicyDecision (§8.1 #1/#2).

The card is drawn from the engine's typed `PolicyDecision`, never a model
string, and carries the per-session anti-spoof marker. These pin the rendered
output and the marker's determinism.
"""

from __future__ import annotations

from uuid import UUID

from rich.console import Console

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.engine import PolicyDecision
from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.rules import Decision
from capabledeputy.policy.tiers import Tier
from capabledeputy.tui.inline.decision import (
    _MARKER_GLYPHS,
    decision_chip,
    format_labels,
    marker_for_session,
    render_card,
)


def _render(renderable) -> str:
    console = Console(width=80, force_terminal=False, color_system=None)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


_HEALTH_UNTRUSTED = LabelState(
    a=frozenset({CategoryTag("health", Tier.RESTRICTED)}),
    b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
)


# --- the anti-spoof marker ------------------------------------------


def test_marker_is_deterministic_per_session() -> None:
    sid = UUID("11111111-1111-1111-1111-111111111111")
    assert marker_for_session(sid) == marker_for_session(sid)
    assert marker_for_session(sid).glyph in _MARKER_GLYPHS


def test_marker_varies_across_sessions() -> None:
    a = marker_for_session(UUID(int=0))
    b = marker_for_session(UUID(int=5))
    assert (a.glyph, a.style) != (b.glyph, b.style)


# --- chips ----------------------------------------------------------


def test_allow_chip_is_quiet_and_names_the_action() -> None:
    d = PolicyDecision(decision=Decision.ALLOW)
    text = decision_chip(d, action_kind=CapabilityKind.READ_FS, target="notes.txt")
    assert "notes.txt" in text.plain


def test_gated_chip_shows_the_rule() -> None:
    d = PolicyDecision(decision=Decision.DENY, rule="untrusted-meets-egress")
    text = decision_chip(d, action_kind=CapabilityKind.SEND_EMAIL, target="x@y.example")
    assert "untrusted-meets-egress" in text.plain


# --- labels ---------------------------------------------------------


def test_format_labels_shows_tier_and_untrusted() -> None:
    out = format_labels(_HEALTH_UNTRUSTED).plain
    assert "health/restricted" in out
    assert "untrusted" in out


# --- the card: engine facts + marker --------------------------------


def test_card_renders_engine_facts_and_marker() -> None:
    d = PolicyDecision(
        decision=Decision.REQUIRE_APPROVAL,
        rule="health-meets-egress",
        reason="share lab recap",
        labels_snapshot=_HEALTH_UNTRUSTED,
    )
    marker = marker_for_session(UUID(int=3))
    out = _render(
        render_card(
            d,
            action_kind=CapabilityKind.SEND_EMAIL,
            target="dr.lee@clinic.example",
            marker=marker,
        ),
    )
    # engine-authored facts only:
    assert "dr.lee@clinic.example" in out
    assert "health-meets-egress" in out
    assert "share lab recap" in out
    assert "health/restricted" in out
    # the per-session anti-spoof marker is on the card:
    assert marker.glyph in out
    # approval keys (not override):
    assert "[a] approve" in out


def test_override_card_offers_override_keys() -> None:
    d = PolicyDecision(decision=Decision.OVERRIDE_REQUIRED, rule="prohibited")
    out = _render(
        render_card(
            d,
            action_kind=CapabilityKind.QUEUE_PURCHASE,
            target="amazon",
            marker=marker_for_session(UUID(int=1)),
        ),
    )
    assert "[o] override" in out
