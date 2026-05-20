"""T070 — Write-discipline verification (FR-044 / SC-015).

The marquee invariant: only a verified version-preserving write
earns `reversible/system`. Unverifiable or in-place writes are
`irreversible`. These tests focus on the write-discipline contract
at module boundaries — the per-condition unit tests live in
test_reversibility.py.
"""

from __future__ import annotations

from capabledeputy.policy.bindings import (
    BindingSet,
    SourceLocationLabelBinding,
    WriteDiscipline,
)
from capabledeputy.policy.reversibility import (
    ReversalAgent,
    ReversibilityDegree,
    WriteResult,
    verify_write_discipline,
)
from capabledeputy.policy.tiers import Tier


def test_binding_requires_version_preserving_write() -> None:
    """An operator binding can demand version-preserving writes.
    Resolver carries the discipline forward so the dispatcher knows
    which port to use."""
    binding = SourceLocationLabelBinding(
        name="audit-log",
        scope_pattern_canonical="file:///audit/*",
        category="audit",
        default_tier=Tier.REGULATED,
        write_discipline=WriteDiscipline.VERSION_PRESERVING,
    )
    bindings = BindingSet(bindings=(binding,))
    res = bindings.resolve("file:///audit/2026-05-19.log")
    assert res.write_discipline == WriteDiscipline.VERSION_PRESERVING


def test_in_place_default_for_unflagged_binding() -> None:
    """If the operator didn't flag `version-preserving`, the binding
    defaults to in-place — and the engine knows it cannot promise
    reversibility for writes here."""
    binding = SourceLocationLabelBinding(
        name="scratch",
        scope_pattern_canonical="file:///tmp/*",
        category="scratch",
        default_tier=Tier.SENSITIVE,
    )
    bindings = BindingSet(bindings=(binding,))
    res = bindings.resolve("file:///tmp/work.txt")
    assert res.write_discipline == WriteDiscipline.IN_PLACE


def test_verified_versioned_write_earns_reversible_system() -> None:
    """End-to-end: a port returns a verifiable WriteResult; the
    verifier confirms; the resulting label is reversible/system."""
    result = WriteResult(
        prior_version_handle="snap:doc-v1",
        post_state_hash="abc123",
        attestation="signed:operator@2026-05-19",
    )
    label = verify_write_discipline(
        result,
        observed_prior_hash="pre-state-hash",
        expected_pre_state_hash="pre-state-hash",
    )
    assert label.degree == ReversibilityDegree.REVERSIBLE
    assert label.agent == ReversalAgent.SYSTEM


def test_unverifiable_write_stays_irreversible() -> None:
    """No prior_version_handle ⇒ no verification possible ⇒
    irreversible/external. This is the fail-closed default that
    keeps the autonomy boost out of reach without explicit proof."""
    result = WriteResult(
        prior_version_handle=None,
        post_state_hash="abc",
        attestation="",
    )
    label = verify_write_discipline(
        result,
        observed_prior_hash="anything",
        expected_pre_state_hash="anything",
    )
    assert label.degree == ReversibilityDegree.IRREVERSIBLE
    assert label.agent == ReversalAgent.EXTERNAL
