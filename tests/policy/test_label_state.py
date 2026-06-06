"""Property + unit tests for the 003 redesign label model (R1).

Proves the composition/apply/floor math before any consumer is wired:
  - composition is commutative, associative, idempotent (SC-002 determinism)
  - composition is monotone-raising (never lowers a tier / drops a level)
  - a non-declassifier transfer can never remove a tag (Constitution VI)
  - the integrity floor (Biba) is checked correctly
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.label_state import (
    CategoryTag,
    LabelError,
    LabelState,
    ProvenanceTag,
    TagTransfer,
    apply_transfer,
    meets_required_floor,
    most_restrictive_inherit,
)
from capabledeputy.policy.labels import AssignmentProvenance, ProvenanceLevel
from capabledeputy.policy.tiers import Tier, compare

# --- strategies ------------------------------------------------------

_categories = st.sampled_from(["health", "finance", "personal", "work"])
_tiers = st.sampled_from(list(Tier))
_provs = st.sampled_from(list(AssignmentProvenance))
_levels = st.sampled_from(list(ProvenanceLevel))
_risk_ids = st.frozensets(st.sampled_from(["R-1", "R-2", "R-3"]), max_size=3)

_cat_tags = st.builds(
    CategoryTag, category=_categories, tier=_tiers, risk_ids=_risk_ids, assigned_by=_provs
)
_prov_tags = st.builds(ProvenanceTag, level=_levels)
_label_states = st.builds(
    LabelState,
    a=st.frozensets(_cat_tags, max_size=4),
    b=st.frozensets(_prov_tags, max_size=3),
)


def _tier_by_cat(state: LabelState) -> dict[str, Tier]:
    return {t.category: t.tier for t in most_restrictive_inherit(state).a}


# --- composition algebra (determinism) -------------------------------


@given(_label_states, _label_states)
def test_compose_commutative(x: LabelState, y: LabelState) -> None:
    assert most_restrictive_inherit(x, y) == most_restrictive_inherit(y, x)


@given(_label_states, _label_states, _label_states)
def test_compose_associative(x: LabelState, y: LabelState, z: LabelState) -> None:
    left = most_restrictive_inherit(most_restrictive_inherit(x, y), z)
    right = most_restrictive_inherit(x, most_restrictive_inherit(y, z))
    assert left == right


@given(_label_states)
def test_compose_idempotent(x: LabelState) -> None:
    once = most_restrictive_inherit(x)
    assert most_restrictive_inherit(x, x) == once
    assert most_restrictive_inherit(once, once) == once


# --- monotonicity (never lowers protection) --------------------------


@given(_label_states, _label_states)
def test_compose_monotone_tier(x: LabelState, y: LabelState) -> None:
    composed = _tier_by_cat(most_restrictive_inherit(x, y))
    for cat, tier in _tier_by_cat(x).items():
        assert compare(composed[cat], tier) >= 0  # composed >= x's tier


@given(_label_states, _label_states)
def test_compose_preserves_provenance_levels(x: LabelState, y: LabelState) -> None:
    composed = most_restrictive_inherit(x, y)
    present = {t.level for t in composed.b}
    assert {t.level for t in x.b} <= present
    assert {t.level for t in y.b} <= present


# --- apply / remove discipline ---------------------------------------


@given(_label_states, st.builds(LabelState, a=st.frozensets(_cat_tags, max_size=3)))
def test_add_only_transfer_never_removes(state: LabelState, adds: LabelState) -> None:
    result = apply_transfer(state, TagTransfer(adds=adds))  # removes=None
    before = _tier_by_cat(state)
    after = _tier_by_cat(result)
    for cat, tier in before.items():
        assert cat in after and compare(after[cat], tier) >= 0
    assert {t.level for t in state.b} <= {t.level for t in result.b}


def test_non_declassifier_removal_is_rejected_at_construction() -> None:
    rem = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
    with pytest.raises(LabelError):
        TagTransfer(adds=LabelState(), removes=rem, is_declassifier=False)


def test_certified_declassifier_removes_a_tag() -> None:
    state = LabelState(
        a=frozenset({CategoryTag("health", Tier.RESTRICTED), CategoryTag("work", Tier.SENSITIVE)})
    )
    rem = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
    out = apply_transfer(state, TagTransfer(removes=rem, is_declassifier=True))
    cats = {t.category for t in out.a}
    assert cats == {"work"}


# --- integrity floor (Biba) ------------------------------------------


def test_required_floor_none_always_passes() -> None:
    tainted = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    assert meets_required_floor(tainted, None) is True


def test_required_floor_refuses_below_floor() -> None:
    tainted = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    # action requires at least system-internal trust -> untrusted input fails
    assert meets_required_floor(tainted, ProvenanceLevel.SYSTEM_INTERNAL) is False


def test_required_floor_allows_at_or_above_floor() -> None:
    trusted = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)}))
    assert meets_required_floor(trusted, ProvenanceLevel.SYSTEM_INTERNAL) is True


def test_required_floor_lowest_floor_accepts_everything() -> None:
    tainted = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    assert meets_required_floor(tainted, ProvenanceLevel.EXTERNAL_UNTRUSTED) is True


# --- effect class / operation ----------------------------------------


def test_effect_class_values_are_canonical() -> None:
    assert EffectClass.EXECUTE_SANDBOX.value == "EXECUTE.sandbox"
    assert EffectClass("OBSERVE") is EffectClass.OBSERVE


def test_operation_carries_subtype_and_floor() -> None:
    op = Operation(
        effect_class=EffectClass.MUTATE_LOCAL,
        subtype="calendar.delete",
        required_floor=ProvenanceLevel.SYSTEM_INTERNAL,
    )
    assert op.effect_class is EffectClass.MUTATE_LOCAL
    assert op.subtype == "calendar.delete"
    assert op.required_floor is ProvenanceLevel.SYSTEM_INTERNAL
