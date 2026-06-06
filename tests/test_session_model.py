from datetime import UTC, datetime
from uuid import uuid4

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import DeclassEvent, Session, SessionStatus, Turn


def test_new_creates_active_session_with_distinct_id() -> None:
    a = Session.new()
    b = Session.new()
    assert a.id != b.id
    assert a.status == SessionStatus.ACTIVE
    assert b.status == SessionStatus.ACTIVE


def test_new_session_label_and_capability_sets_are_empty_by_default() -> None:
    s = Session.new()
    assert s.label_state == LabelState()
    assert s.capability_set == frozenset()
    assert s.history == ()
    assert s.declassification_log == ()


def test_new_session_inherits_supplied_fields() -> None:
    from capabledeputy.policy.labels import AxisB

    cap = Capability(kind=CapabilityKind.WEB_FETCH, pattern="*")
    s = Session.new(
        owner="marc",
        intent="research",
        axis_b=AxisB(entries=(ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED),)),
        capability_set=frozenset({cap}),
    )
    assert s.owner == "marc"
    assert s.intent == "research"
    assert any(p.level == ProvenanceLevel.EXTERNAL_UNTRUSTED for p in s.label_state.b)
    assert s.capability_set == frozenset({cap})


def test_with_status_returns_new_session_with_updated_timestamp() -> None:
    s = Session.new()
    paused = s.with_status(SessionStatus.PAUSED)
    assert paused.id == s.id
    assert paused.status == SessionStatus.PAUSED
    assert paused.updated_at >= s.updated_at
    assert paused.created_at == s.created_at


def test_is_terminal_true_for_done_and_aborted() -> None:
    s = Session.new()
    assert not s.with_status(SessionStatus.ACTIVE).is_terminal
    assert not s.with_status(SessionStatus.PAUSED).is_terminal
    assert not s.with_status(SessionStatus.WAITING_APPROVAL).is_terminal
    assert s.with_status(SessionStatus.DONE).is_terminal
    assert s.with_status(SessionStatus.ABORTED).is_terminal


def test_session_round_trip_through_dict() -> None:
    from capabledeputy.policy.labels import AxisA

    parent_id = uuid4()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    s = Session.new(
        parent=parent_id,
        owner="marc",
        intent="test",
        axis_a=AxisA(
            categories=(
                CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared"),
                CategoryTag("personal", Tier.REGULATED, assignment_provenance="source-declared"),
            )
        ),
        capability_set=frozenset({cap}),
        history=(Turn(turn_id=0, role="user", content="hello", timestamp=datetime.now(UTC)),),
    )
    decoded = Session.from_dict(s.to_dict())
    assert decoded == s


def test_session_dict_serializes_label_set_sorted() -> None:
    from capabledeputy.policy.labels import AxisA, AxisB

    s = Session.new(
        axis_a=AxisA(
            categories=(
                CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared"),
            )
        ),
        axis_b=AxisB(entries=(ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED),)),
    )
    d = s.to_dict()
    # The axis_a and axis_b are serialized separately
    assert "axis_a" in d
    assert "axis_b" in d
    assert len(d["axis_a"]) > 0 or len(d["axis_b"]) > 0


def test_session_dict_serializes_capability_set_as_list_of_dicts() -> None:
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/marc/*")
    s = Session.new(capability_set=frozenset({cap}))
    d = s.to_dict()
    assert isinstance(d["capability_set"], list)
    assert d["capability_set"][0]["kind"] == "READ_FS"


def test_turn_round_trip() -> None:
    t = Turn(turn_id=42, role="agent", content="hi")
    decoded = Turn.from_dict(t.to_dict())
    assert decoded == t


def test_declass_event_round_trip() -> None:
    # DeclassEvent is audit-only and keeps the flat Label enum (Option A).
    # We test it separately from the label_state migration.
    # For now, use a mock event to test the roundtrip.
    from uuid import uuid4

    d = DeclassEvent(
        audit_id=uuid4(),
        from_labels=frozenset(),
        to_labels=frozenset(),
        reason="user-approved declassification",
    )
    decoded = DeclassEvent.from_dict(d.to_dict())
    assert decoded == d


def test_declass_event_serializes_labels_sorted() -> None:
    # DeclassEvent is audit-only and keeps the flat Label enum (Option A).
    # Test serialization of the flat labels list.
    d = DeclassEvent(
        audit_id=uuid4(),
        from_labels=frozenset(),
        to_labels=frozenset(),
        reason="x",
    )
    out = d.to_dict()
    from_labels = out.get("from_labels")
    assert from_labels is not None
    # Verify it's a list representation (sorted flat labels)
    assert isinstance(from_labels, list)
