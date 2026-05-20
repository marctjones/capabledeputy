"""T098 — Reference Handle unforgeable + audit trail (FR-047 / SC-021).

Five invariants:
  1. Unforgeable: planner-side construction or cross-session use of
     a handle id fails at bind time.
  2. Planner never holds the raw value: the ReferenceHandle struct
     has no `bound_value` field.
  3. Bind emits a `pattern3.handle_bind` event with non-empty
     destination_canonical_id (where-the-secret-landed, FR-047).
  4. A handle ever bound has >=1 entry in bind_trail.
  5. Handles are not capabilities — `bind` does NOT check decide();
     the dispatcher must call decide() first (separation enforced
     architecturally; the store is the substrate, the engine is
     the oracle).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from capabledeputy.patterns.reference_handle import (
    HandleBindEvent,
    ReferenceHandle,
    ReferenceHandleError,
    ReferenceHandleStore,
    ResolvedLabels,
    is_planner_safe_token,
)


def _store() -> ReferenceHandleStore:
    return ReferenceHandleStore()


def _labels() -> ResolvedLabels:
    return ResolvedLabels(
        axis_a=("health",),
        axis_b=("principal-direct",),
    )


# --- 1. Unforgeable -------------------------------------------------


def test_unknown_handle_id_refused() -> None:
    """A planner-fabricated id (random UUID4) is not in the store ⇒
    bind refuses with ReferenceHandleError."""
    store = _store()
    session_id = uuid4()
    fabricated = uuid4()
    with pytest.raises(ReferenceHandleError):
        store.bind(
            session_id=session_id,
            handle_id=fabricated,
            destination_canonical_id="https://api.example.com/post",
            tool="api.post",
            audit_id=uuid4(),
        )


def test_cross_session_use_refused() -> None:
    """Handle issued in session A cannot be bound in session B."""
    store = _store()
    session_a = uuid4()
    session_b = uuid4()
    handle = store.issue(session_a, "secret-value", _labels())
    with pytest.raises(ReferenceHandleError):
        store.bind(
            session_id=session_b,
            handle_id=handle.id,
            destination_canonical_id="https://api.example.com/post",
            tool="api.post",
            audit_id=uuid4(),
        )


# --- 2. Planner never holds the raw value ---------------------------


def test_reference_handle_struct_has_no_value_field() -> None:
    """Compile-time guarantee: the ReferenceHandle dataclass exposes
    NO bound_value field. The planner can only see id + session_id +
    labels + issued_at."""
    handle = ReferenceHandle(
        id=uuid4(),
        session_id=uuid4(),
        labels=_labels(),
        issued_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    assert not hasattr(handle, "bound_value")
    assert not hasattr(handle, "value")
    assert not hasattr(handle, "raw")


def test_issued_handle_does_not_expose_value() -> None:
    """A store-issued handle never carries the value either —
    the value lives in the store's private dict."""
    store = _store()
    session = uuid4()
    handle = store.issue(session, "secret-value-xyz", _labels())
    # Inspect every public field — no value leak.
    serialized = (handle.id, handle.session_id, handle.labels, handle.issued_at)
    for field in serialized:
        assert "secret-value-xyz" not in repr(field)


# --- 3. Bind emits where-landed event -------------------------------


def test_bind_emits_event_with_destination() -> None:
    store = _store()
    session = uuid4()
    handle = store.issue(session, "the-value", _labels())
    audit_id = uuid4()
    value = store.bind(
        session_id=session,
        handle_id=handle.id,
        destination_canonical_id="https://api.example.com/post",
        tool="api.post",
        audit_id=audit_id,
    )
    assert value == "the-value"
    trail = store.bind_trail(handle.id)
    assert len(trail) == 1
    event = trail[0]
    assert isinstance(event, HandleBindEvent)
    assert event.destination_canonical_id == "https://api.example.com/post"
    assert event.tool == "api.post"
    assert event.audit_id == audit_id


def test_bind_with_empty_destination_refused() -> None:
    """FR-047 — audit demands a non-empty destination. Empty ⇒ refuse
    (so the where-landed record is never blank)."""
    store = _store()
    session = uuid4()
    handle = store.issue(session, "value", _labels())
    with pytest.raises(ReferenceHandleError):
        store.bind(
            session_id=session,
            handle_id=handle.id,
            destination_canonical_id="",
            tool="api.post",
            audit_id=uuid4(),
        )


# --- 4. Bind trail persistence --------------------------------------


def test_bind_trail_records_each_use() -> None:
    """A handle bound twice (e.g., into a header AND a query param)
    produces TWO events in the trail."""
    store = _store()
    session = uuid4()
    handle = store.issue(session, "v", _labels())
    store.bind(
        session_id=session,
        handle_id=handle.id,
        destination_canonical_id="d1",
        tool="t1",
        audit_id=uuid4(),
    )
    store.bind(
        session_id=session,
        handle_id=handle.id,
        destination_canonical_id="d2",
        tool="t2",
        audit_id=uuid4(),
    )
    assert len(store.bind_trail(handle.id)) == 2


def test_destroy_session_keeps_bind_trail() -> None:
    """Per the contract: terminal session destroys the stored value
    but RETAINS bind_trail for audit."""
    store = _store()
    session = uuid4()
    handle = store.issue(session, "v", _labels())
    store.bind(
        session_id=session,
        handle_id=handle.id,
        destination_canonical_id="d",
        tool="t",
        audit_id=uuid4(),
    )
    store.destroy_session_handles(session)
    assert not store.has_handle(handle.id)
    assert len(store.bind_trail(handle.id)) == 1


# --- planner-safe-token check ---------------------------------------


def test_uuid_string_is_planner_safe() -> None:
    """A UUID string is the only thing a planner should hold —
    is_planner_safe_token returns True for them."""
    assert is_planner_safe_token(str(uuid4()))


def test_raw_value_is_not_planner_safe() -> None:
    """A raw labeled value (e.g., an SSN or email) is not a
    UUID-shaped string ⇒ flagged."""
    assert not is_planner_safe_token("alice@example.com")
    assert not is_planner_safe_token("123-45-6789")
    assert not is_planner_safe_token("the quick brown fox")
