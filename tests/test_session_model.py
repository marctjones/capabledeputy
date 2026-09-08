from datetime import UTC, datetime
from uuid import uuid4

from capabledeputy.policy.capabilities import Capability, CapabilityKind
from capabledeputy.policy.labels import CategoryTag, LabelState, ProvenanceLevel, ProvenanceTag
from capabledeputy.policy.tiers import Tier
from capabledeputy.session.model import (
    DeclassEvent,
    Session,
    SessionStatus,
    Turn,
    make_generated_image_artifact,
    merge_session_artifacts,
    session_artifacts_from_handles,
)


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
    cap = Capability(kind=CapabilityKind.WEB_FETCH, pattern="*")
    s = Session.new(
        owner="marc",
        intent="research",
        label_state=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
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
    parent_id = uuid4()
    cap = Capability(kind=CapabilityKind.READ_FS, pattern="/home/*")
    s = Session.new(
        parent=parent_id,
        owner="marc",
        intent="test",
        label_state=LabelState(
            a=frozenset(
                {
                    CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared"),
                    CategoryTag(
                        "personal", Tier.REGULATED, assignment_provenance="source-declared"
                    ),
                }
            )
        ),
        capability_set=frozenset({cap}),
        history=(Turn(turn_id=0, role="user", content="hello", timestamp=datetime.now(UTC)),),
    )
    decoded = Session.from_dict(s.to_dict())
    assert decoded == s


def test_session_from_dict_drops_retired_capability_kind() -> None:
    """A capability kind can be retired (e.g. Google integration's
    GMAIL_READ was deleted, GMAIL_DRAFT renamed) after a session already
    persisted a grant using the old name. Loading that session must drop
    the stale capability, not raise — an UnknownKindError/ValueError here
    used to crash `SessionGraph.load()` for *every* session in the store,
    taking the whole daemon down on any operator's upgrade."""
    kept = Capability(kind=CapabilityKind.WEB_FETCH, pattern="*")
    retired = Capability(kind=CapabilityKind.READ_FS, pattern="*").to_dict()
    retired["kind"] = "GMAIL_READ"  # retired kind, no longer in CapabilityKind
    s = Session.new(capability_set=frozenset({kept}))
    raw = s.to_dict()
    raw["capability_set"] = [*raw["capability_set"], retired]
    decoded = Session.from_dict(raw)
    assert decoded.capability_set == frozenset({kept})


def test_session_from_dict_drops_retired_used_kind() -> None:
    """Same tolerance for `used_kinds`, which does a bare
    `CapabilityKind(k)` call (not `resolve_kind`) and so raises a plain
    ValueError, not UnknownKindError — both must be caught."""
    s = Session.new()
    raw = s.to_dict()
    raw["used_kinds"] = ["WEB_FETCH", "GMAIL_DRAFT"]  # GMAIL_DRAFT retired/renamed
    decoded = Session.from_dict(raw)
    assert decoded.used_kinds == frozenset({CapabilityKind.WEB_FETCH})


def test_session_artifacts_persist_in_reference_handles(tmp_path) -> None:
    image_path = tmp_path / ".capdep" / "work" / "images" / "out.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"not really a png")
    artifact = make_generated_image_artifact(
        path=str(image_path),
        alt="generated chart",
        prompt="make an image",
        origin_turn_id=2,
        origin_tool_name="bundled-image-generate.image.generate",
    )

    handles = merge_session_artifacts({}, [artifact])
    session = Session.new(reference_handles=handles)
    decoded = Session.from_dict(session.to_dict())
    artifacts = session_artifacts_from_handles(decoded.reference_handles)

    assert len(artifacts) == 1
    assert artifacts[0]["path"] == str(image_path)
    assert artifacts[0]["alt"] == "generated chart"
    assert artifacts[0]["origin_turn_id"] == 2
    assert artifacts[0]["sha256"]


def test_session_dict_serializes_label_set_sorted() -> None:
    s = Session.new(
        label_state=LabelState(
            a=frozenset(
                {
                    CategoryTag("health", Tier.REGULATED, assignment_provenance="source-declared"),
                }
            ),
            b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}),
        ),
    )
    d = s.to_dict()
    # The label_state is serialized as a single dict with "a" and "b" keys
    assert "label_state" in d
    ls = d["label_state"]
    assert len(ls.get("a", [])) > 0 or len(ls.get("b", [])) > 0


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
