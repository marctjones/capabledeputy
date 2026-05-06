from datetime import UTC, datetime
from uuid import uuid4

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import Label
from capabledeputy.session.model import DeclassEvent, Session, SessionStatus, Turn


def test_new_creates_active_session_with_distinct_id() -> None:
    a = Session.new()
    b = Session.new()
    assert a.id != b.id
    assert a.status == SessionStatus.ACTIVE
    assert b.status == SessionStatus.ACTIVE


def test_new_session_label_and_capability_sets_are_empty_by_default() -> None:
    s = Session.new()
    assert s.label_set == frozenset()
    assert s.capability_set == frozenset()
    assert s.history == ()
    assert s.declassification_log == ()


def test_new_session_inherits_supplied_fields() -> None:
    cap = Capability(kind=CapabilityKind.WEB_FETCH, pattern="*")
    s = Session.new(
        owner="marc",
        intent="research",
        label_set=frozenset({Label.UNTRUSTED_EXTERNAL}),
        capability_set=frozenset({cap}),
    )
    assert s.owner == "marc"
    assert s.intent == "research"
    assert s.label_set == frozenset({Label.UNTRUSTED_EXTERNAL})
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
    parent_id = uuid4()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    s = Session.new(
        parent=parent_id,
        owner="marc",
        intent="test",
        label_set=frozenset({Label.CONFIDENTIAL_HEALTH, Label.CONFIDENTIAL_PERSONAL}),
        capability_set=frozenset({cap}),
        history=(Turn(turn_id=0, role="user", content="hello", timestamp=datetime.now(UTC)),),
    )
    decoded = Session.from_dict(s.to_dict())
    assert decoded == s


def test_session_dict_serializes_label_set_sorted() -> None:
    s = Session.new(
        label_set=frozenset(
            {
                Label.UNTRUSTED_EXTERNAL,
                Label.CONFIDENTIAL_HEALTH,
                Label.EGRESS_EMAIL,
            },
        ),
    )
    d = s.to_dict()
    assert d["label_set"] == [
        "confidential.health",
        "egress.email",
        "untrusted.external",
    ]


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
    d = DeclassEvent(
        audit_id=uuid4(),
        from_labels=frozenset({Label.CONFIDENTIAL_HEALTH}),
        to_labels=frozenset(),
        reason="user-approved declassification",
    )
    decoded = DeclassEvent.from_dict(d.to_dict())
    assert decoded == d


def test_declass_event_serializes_labels_sorted() -> None:
    d = DeclassEvent(
        audit_id=uuid4(),
        from_labels=frozenset(
            {Label.UNTRUSTED_EXTERNAL, Label.CONFIDENTIAL_HEALTH},
        ),
        to_labels=frozenset(),
        reason="x",
    )
    out = d.to_dict()
    assert out["from_labels"] == ["confidential.health", "untrusted.external"]
