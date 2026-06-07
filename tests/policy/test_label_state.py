"""Property + unit tests for the 003 redesign label model (R1, consolidated
into policy/labels.py in R4a).

Proves the composition/apply/floor math:
  - composition commutative, associative, idempotent (SC-002 determinism)
  - composition monotone-raising (never lowers a tier / drops a level)
  - a non-declassifier transfer can never remove a tag (Constitution VI)
  - the integrity floor (Biba) is checked correctly
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import (
    AssignmentProvenance,
    CategoryTag,
    LabelError,
    LabelState,
    ProvenanceLevel,
    ProvenanceTag,
    TagTransfer,
    apply_transfer,
    inherit,
    meets_required_floor,
    most_restrictive_inherit,
    tags_for_labels_strings,
)
from capabledeputy.policy.tiers import Tier, compare

# --- strategies ------------------------------------------------------

_categories = st.sampled_from(["health", "finance", "personal", "work"])
_tiers = st.sampled_from(list(Tier))
_provs = st.sampled_from([p.value for p in AssignmentProvenance])
_levels = st.sampled_from(list(ProvenanceLevel))
_risk_ids = st.lists(st.sampled_from(["R-1", "R-2", "R-3"]), max_size=3, unique=True).map(tuple)

_cat_tags = st.builds(
    CategoryTag, category=_categories, tier=_tiers, risk_ids=_risk_ids, assignment_provenance=_provs
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
        assert compare(composed[cat], tier) >= 0


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


def test_tags_for_labels_maps_categories_and_provenance() -> None:
    """The legacy-string→four-axis forward map (`tags_for_labels_strings`,
    used by the daemon RPC paths that still receive flat label strings)
    places confidential.* on Axis A and untrusted/trusted.* on Axis B."""
    health = tags_for_labels_strings(frozenset({"confidential.health"}))
    assert {t.category for t in health.a} == {"health"}
    assert not health.b
    untrusted = tags_for_labels_strings(frozenset({"untrusted.external"}))
    assert {t.level for t in untrusted.b} == {ProvenanceLevel.EXTERNAL_UNTRUSTED}
    assert not untrusted.a


def test_tags_for_labels_unfuses_egress_effects() -> None:
    """Egress strings are Axis-C effects, not propagating tags — they
    contribute nothing to the LabelState (the redesign's un-fusing)."""
    assert tags_for_labels_strings(frozenset({"egress.email"})) == LabelState()
    assert tags_for_labels_strings(frozenset({"egress.purchase"})) == LabelState()
    # A mixed set drops only the egress part.
    mixed = tags_for_labels_strings(frozenset({"confidential.financial", "egress.email"}))
    assert {t.category for t in mixed.a} == {"financial"}


def test_tags_for_labels_empty_is_empty() -> None:
    assert tags_for_labels_strings(frozenset()) == LabelState()


def test_string_path_resolves_tier_from_catalog(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #50 — the flat-string path must carry the catalog's
    default_tier, not a flattened REGULATED. With a catalog that puts
    health at `restricted`, the string `confidential.health` resolves to
    a RESTRICTED tag (was hardcoded REGULATED, which weakened BLP)."""
    from capabledeputy.policy.labels import reset_category_tier_cache

    (tmp_path / "labels.yaml").write_text(
        "categories:\n"
        "  - id: health\n"
        "    kind: stable_core\n"
        "    default_tier: restricted\n"
        "  - id: personal\n"
        "    kind: stable_core\n"
        "    default_tier: regulated\n",
    )
    monkeypatch.setenv("CAPDEP_CONFIGS_DIR", str(tmp_path))
    reset_category_tier_cache()
    try:
        health = tags_for_labels_strings(frozenset({"confidential.health"}))
        (health_tag,) = health.a
        assert health_tag.tier == Tier.RESTRICTED
        personal = tags_for_labels_strings(frozenset({"confidential.personal"}))
        (personal_tag,) = personal.a
        assert personal_tag.tier == Tier.REGULATED
    finally:
        reset_category_tier_cache()


def test_string_path_fails_safe_to_regulated_without_catalog(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #50 — when the catalog is absent/silent, fail-safe to
    REGULATED rather than crashing or under-classifying."""
    from capabledeputy.policy.labels import reset_category_tier_cache

    monkeypatch.setenv("CAPDEP_CONFIGS_DIR", str(tmp_path))  # no labels.yaml
    reset_category_tier_cache()
    try:
        health = tags_for_labels_strings(frozenset({"confidential.health"}))
        (health_tag,) = health.a
        assert health_tag.tier == Tier.REGULATED
    finally:
        reset_category_tier_cache()


def test_certified_declassifier_removes_a_tag() -> None:
    state = LabelState(
        a=frozenset({CategoryTag("health", Tier.RESTRICTED), CategoryTag("work", Tier.SENSITIVE)})
    )
    rem = LabelState(a=frozenset({CategoryTag("health", Tier.RESTRICTED)}))
    out = apply_transfer(state, TagTransfer(removes=rem, is_declassifier=True))
    assert {t.category for t in out.a} == {"work"}


# --- integrity floor (Biba) ------------------------------------------


def test_required_floor_none_always_passes() -> None:
    tainted = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
    assert meets_required_floor(tainted, None) is True


def test_required_floor_refuses_below_floor() -> None:
    tainted = LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}))
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


# --- directional inheritance (delegation / fork) ---------------------


def test_directional_inherit_keeps_parent_provenance() -> None:
    """`inherit` (delegation/fork) is parent-authoritative on provenance:
    derivation cannot launder a category's assignment_provenance away —
    the parent's source wins on a shared category. Distinct from the
    symmetric `most_restrictive_inherit` (in-session accumulation)."""
    parent = LabelState(
        a=frozenset({CategoryTag("work", Tier.SENSITIVE, assignment_provenance="human-declared")}),
    )
    child = LabelState(
        a=frozenset(
            {CategoryTag("work", Tier.RESTRICTED, assignment_provenance="source-declared")},
        ),
    )
    merged = inherit(parent, child)
    (tag,) = merged.a
    assert tag.category == "work"
    assert tag.tier is Tier.RESTRICTED  # tier still rises to most-restrictive
    assert tag.assignment_provenance == "human-declared"  # parent's source wins


def test_directional_inherit_raise_only_inspector_escalates() -> None:
    """The one exception: a child tag from the raise-only inspector may
    override provenance (it can only ADD taint, never clear it)."""
    parent = LabelState(
        a=frozenset({CategoryTag("work", Tier.SENSITIVE, assignment_provenance="human-declared")}),
    )
    child = LabelState(
        a=frozenset(
            {CategoryTag("work", Tier.SENSITIVE, assignment_provenance="raise-only-inspector")},
        ),
    )
    (tag,) = inherit(parent, child).a
    assert tag.assignment_provenance == "raise-only-inspector"


def test_decide_uses_labels_param() -> None:
    """R4b.4 — decide() now uses the labels=LabelState(...) parameter
    directly. The engine now expects LabelState, not separate axes."""
    from capabledeputy.policy.actions import Action
    from capabledeputy.policy.capabilities import Capability, CapabilityKind
    from capabledeputy.policy.engine import decide

    ls = LabelState(a=frozenset({CategoryTag("personal", Tier.REGULATED)}))
    action = Action(kind=CapabilityKind.READ_FS, target="/x")
    caps = frozenset({Capability(kind=CapabilityKind.READ_FS, pattern="*")})
    result = decide(caps, action, labels=ls)
    # Just verify the call succeeds and returns a decision
    assert result is not None
