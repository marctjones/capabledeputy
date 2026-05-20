"""T061 — Reversibility label composition + write-discipline (FR-037 / FR-044 / SC-015).

A reversibility label is `(degree, agent)`. Composition across
multiple inputs is most-restrictive: worst degree, worst agent.
Write-discipline verification earns `reversible/system` only when
the version-preserving write is verifiable end-to-end.
"""

from __future__ import annotations

from capabledeputy.policy.reversibility import (
    MutabilityDegree,
    MutabilityLabel,
    ReversalAgent,
    ReversibilityDegree,
    ReversibilityLabel,
    WriteResult,
    compose_mutability,
    compose_reversibility,
    reversibility_for_write_into,
    verify_write_discipline,
)


def _r(degree: ReversibilityDegree, agent: ReversalAgent) -> ReversibilityLabel:
    return ReversibilityLabel(degree=degree, agent=agent)


def _m(degree: MutabilityDegree, agent: ReversalAgent) -> MutabilityLabel:
    return MutabilityLabel(degree=degree, agent=agent)


# --- composition primitives ------------------------------------------


def test_single_label_passes_through() -> None:
    label = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    assert compose_reversibility(label) == label


def test_most_restrictive_degree_wins() -> None:
    a = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    b = _r(ReversibilityDegree.IRREVERSIBLE, ReversalAgent.SYSTEM)
    composed = compose_reversibility(a, b)
    assert composed.degree == ReversibilityDegree.IRREVERSIBLE


def test_most_restrictive_agent_wins() -> None:
    a = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    b = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.EXTERNAL)
    composed = compose_reversibility(a, b)
    assert composed.agent == ReversalAgent.EXTERNAL


def test_friction_between_reversible_and_irreversible() -> None:
    """Middle rung — reversible-with-friction sits between
    reversible and irreversible."""
    a = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    b = _r(ReversibilityDegree.REVERSIBLE_WITH_FRICTION, ReversalAgent.HUMAN)
    composed = compose_reversibility(a, b)
    assert composed.degree == ReversibilityDegree.REVERSIBLE_WITH_FRICTION
    assert composed.agent == ReversalAgent.HUMAN


def test_compose_mutability_most_restrictive() -> None:
    a = _m(MutabilityDegree.IN_PLACE, ReversalAgent.SYSTEM)
    b = _m(MutabilityDegree.IMMUTABLE, ReversalAgent.HUMAN)
    composed = compose_mutability(a, b)
    assert composed.degree == MutabilityDegree.IMMUTABLE
    assert composed.agent == ReversalAgent.HUMAN


# --- effect x mutability composition ---------------------------------


def test_write_into_immutable_target_is_irreversible() -> None:
    """SC-016 — even a 'reversible' effect default composes to
    irreversible when writing into an immutable target."""
    effect_default = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    target = _m(MutabilityDegree.IMMUTABLE, ReversalAgent.EXTERNAL)
    result = reversibility_for_write_into(effect_default, target)
    assert result.degree == ReversibilityDegree.IRREVERSIBLE


def test_write_into_append_only_target_is_irreversible() -> None:
    """Append-only: append is fine, but un-appending isn't — once
    written, the row exists."""
    effect_default = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    target = _m(MutabilityDegree.APPEND_ONLY, ReversalAgent.HUMAN)
    result = reversibility_for_write_into(effect_default, target)
    assert result.degree == ReversibilityDegree.IRREVERSIBLE


def test_write_into_in_place_target_keeps_effect_default() -> None:
    """An in-place target lets the effect's own reversibility govern
    — there's no mutability constraint forcing irreversibility."""
    effect_default = _r(ReversibilityDegree.REVERSIBLE, ReversalAgent.SYSTEM)
    target = _m(MutabilityDegree.IN_PLACE, ReversalAgent.SYSTEM)
    result = reversibility_for_write_into(effect_default, target)
    assert result == effect_default


# --- write-discipline verification (T083 / FR-044) -------------------


def test_unverifiable_write_is_irreversible() -> None:
    """No prior_version_handle ⇒ no verification possible ⇒
    irreversible/external (fail-closed default)."""
    result = WriteResult(
        prior_version_handle=None,
        post_state_hash="post-hash",
        attestation="attested",
    )
    label = verify_write_discipline(
        result,
        observed_prior_hash="anything",
        expected_pre_state_hash="anything",
    )
    assert label.degree == ReversibilityDegree.IRREVERSIBLE
    assert label.agent == ReversalAgent.EXTERNAL


def test_prior_hash_mismatch_is_irreversible() -> None:
    """A hash mismatch indicates the prior version was tampered with
    or never existed — verification fails closed."""
    result = WriteResult(
        prior_version_handle="snap:v1",
        post_state_hash="post-hash",
        attestation="attested",
    )
    label = verify_write_discipline(
        result,
        observed_prior_hash="aaaa",
        expected_pre_state_hash="bbbb",
    )
    assert label.degree == ReversibilityDegree.IRREVERSIBLE


def test_missing_attestation_is_irreversible() -> None:
    result = WriteResult(
        prior_version_handle="snap:v1",
        post_state_hash="post-hash",
        attestation="",
    )
    label = verify_write_discipline(
        result,
        observed_prior_hash="aaaa",
        expected_pre_state_hash="aaaa",
    )
    assert label.degree == ReversibilityDegree.IRREVERSIBLE


def test_all_verified_yields_reversible_system() -> None:
    """The happy path: prior_version_handle present, hashes match,
    attestation present ⇒ reversible/system (FR-044)."""
    result = WriteResult(
        prior_version_handle="snap:v1",
        post_state_hash="post-hash",
        attestation="signed:operator@2026-05-19",
    )
    label = verify_write_discipline(
        result,
        observed_prior_hash="aaaa",
        expected_pre_state_hash="aaaa",
    )
    assert label.degree == ReversibilityDegree.REVERSIBLE
    assert label.agent == ReversalAgent.SYSTEM


def test_none_observed_prior_hash_is_irreversible() -> None:
    """observed_prior_hash=None means the caller couldn't read the
    prior-version handle — verification cannot proceed."""
    result = WriteResult(
        prior_version_handle="snap:v1",
        post_state_hash="post-hash",
        attestation="signed",
    )
    label = verify_write_discipline(
        result,
        observed_prior_hash=None,
        expected_pre_state_hash="aaaa",
    )
    assert label.degree == ReversibilityDegree.IRREVERSIBLE
