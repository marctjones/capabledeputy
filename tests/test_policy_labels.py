from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.tiers import Tier


def test_label_state_to_dict_and_from_dict() -> None:
    """Test LabelState serialization."""
    state = LabelState(
        a=frozenset({CategoryTag(category="health", tier=Tier.REGULATED)}),
        b=frozenset({ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
    )
    d = state.to_dict()
    restored = LabelState.from_dict(d)
    assert restored == state


def test_label_state_from_dict_empty() -> None:
    """Empty or None dict yields LabelState()."""
    assert LabelState.from_dict(None) == LabelState()
    assert LabelState.from_dict({}) == LabelState()


def test_category_tag_to_dict_and_from_dict() -> None:
    """Test CategoryTag serialization."""
    tag = CategoryTag(category="health", tier=Tier.REGULATED, risk_ids=("R1", "R2"))
    d = tag.to_dict()
    restored = CategoryTag.from_dict(d)
    assert restored == tag


def test_provenance_tag_to_dict_and_from_dict() -> None:
    """Test ProvenanceTag serialization."""
    tag = ProvenanceTag(level=ProvenanceLevel.EXTERNAL_UNTRUSTED)
    d = tag.to_dict()
    restored = ProvenanceTag.from_dict(d)
    assert restored == tag
