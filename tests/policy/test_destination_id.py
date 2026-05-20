"""T064 — Canonical destination id always wins over raw input (FR-048 / SC-022).

The audit/decision surface must talk about the *canonical* URI the
binding resolver locked onto — never the raw string the model
typed. This blocks an obvious bypass (model writes
`HTTPS://teams.SharePoint.com/foo`, audit hashes it differently
from another model writing `https://teams.sharepoint.com/foo` for
the same place). It also makes the SC-022 invariant testable:
unidentifiable destinations must produce deny/escalate, never "no
rule matched."
"""

from __future__ import annotations

import pytest

from capabledeputy.policy.bindings import (
    BindingError,
    BindingSet,
    SourceLocationLabelBinding,
)
from capabledeputy.policy.tiers import Tier


def _binding() -> SourceLocationLabelBinding:
    return SourceLocationLabelBinding(
        name="team-site",
        scope_pattern_canonical="https://teams.sharepoint.com/*",
        category="proprietary_work",
        default_tier=Tier.SENSITIVE,
    )


def test_raw_and_canonical_resolve_identically() -> None:
    """Case-varying input ⇒ same canonical_destination_id ⇒ same
    decision-relevant identity."""
    bindings = BindingSet(bindings=(_binding(),))
    a = bindings.resolve("https://teams.sharepoint.com/sites/x")
    b = bindings.resolve("HTTPS://Teams.SharePoint.COM/sites/x")
    assert a.canonical_destination_id == b.canonical_destination_id


def test_unidentifiable_destination_fails_closed() -> None:
    """Garbage input — refused; no 'best-effort' fall-through."""
    bindings = BindingSet(bindings=(_binding(),))
    with pytest.raises(BindingError):
        bindings.resolve("not-a-uri")


def test_canonical_id_is_stable_across_fragments() -> None:
    """Fragments don't change identity — they're locator state."""
    bindings = BindingSet(bindings=(_binding(),))
    a = bindings.resolve("https://teams.sharepoint.com/sites/x#top")
    b = bindings.resolve("https://teams.sharepoint.com/sites/x")
    assert a.canonical_destination_id == b.canonical_destination_id


def test_unbound_does_not_silently_pass() -> None:
    """SC-022 — unbound destination ⇒ raise, not return a permissive
    default. The policy chokepoint should convert the raise into
    deny/escalate; never 'no rule matched'."""
    bindings = BindingSet(bindings=(_binding(),))
    with pytest.raises(BindingError):
        bindings.resolve("file:///random/path")
