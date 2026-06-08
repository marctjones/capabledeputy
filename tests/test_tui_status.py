"""TUI redesign — the trust-state status line (§3 / §8.1 #7)."""

from __future__ import annotations

from uuid import UUID

from capabledeputy.policy.labels import (
    CategoryTag,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
)
from capabledeputy.policy.tiers import Tier
from capabledeputy.tui.inline.decision import marker_for_session
from capabledeputy.tui.inline.status import TrustState, render_status

_MARK = marker_for_session(UUID(int=7))


def test_status_shows_session_purpose_clearance_and_marker() -> None:
    state = TrustState(
        session_name="morning-triage",
        purpose="daily-life",
        clearance="restricted",
    )
    out = render_status(state, _MARK).plain
    assert "morning-triage" in out
    assert "purpose:daily-life" in out
    assert "clearance:restricted" in out
    assert _MARK.glyph in out  # the anti-spoof marker leads the line


def test_unknown_fields_render_dash_not_blank() -> None:
    """A missing value must read as '—', never blank — a blank could be
    mistaken for 'nothing sensitive here'."""
    out = render_status(TrustState(session_name="s"), _MARK).plain
    assert "purpose:—" in out
    assert "clearance:—" in out


def test_status_surfaces_taint_and_advisories() -> None:
    state = TrustState(
        session_name="s",
        labels=LabelState(
            a=frozenset({CategoryTag("health", Tier.RESTRICTED)}),
            b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
        ),
        advisories=2,
    )
    out = render_status(state, _MARK).plain
    assert "health/restricted" in out
    assert "untrusted" in out
    assert "2" in out  # advisory count
