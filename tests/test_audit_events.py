from uuid import uuid4

from capabledeputy.audit.events import Event, EventType


def test_event_round_trip() -> None:
    sid = uuid4()
    event = Event(
        event_type=EventType.SESSION_CREATED,
        session_id=sid,
        payload={"intent": "test"},
    )
    decoded = Event.from_dict(event.to_dict())
    assert decoded.event_type == EventType.SESSION_CREATED
    assert decoded.session_id == sid
    assert decoded.payload == {"intent": "test"}
    assert decoded.audit_id == event.audit_id
    assert decoded.timestamp == event.timestamp


def test_event_to_dict_serializes_session_id() -> None:
    sid = uuid4()
    event = Event(event_type=EventType.SESSION_CREATED, session_id=sid)
    d = event.to_dict()
    assert d["session_id"] == str(sid)


def test_event_to_dict_serializes_none_session_id() -> None:
    event = Event(event_type=EventType.LLM_REQUEST_SENT)
    d = event.to_dict()
    assert d["session_id"] is None


def test_event_type_values_are_dotted_namespaces() -> None:
    valid_namespaces = {
        "session",
        "llm",
        "mode",
        "policy",
        "label",
        "capability",
        "tool",
        "approval",
        "delegation",  # 002 capability delegation chains
        # 003 v0.9 labeling framework (T014).
        "binding",
        "override",
        "ratification",  # FR-014 Q3 ratification authorization
        "pattern3",
        "isolation_region",
        "envelope",
        "risk_register",
        "residual_risk",
        # 004 P0 programmatic primitive applications.
        "inspector",
        "decision_inspector",
        "declassifier",
        # Issue 003 / Q4 — decide() latency tracking (SC-023).
        "decision",
        # Cookbook Pattern ⑥ — shadow mode.
        "enforcement",
    }
    for et in EventType:
        head, sep, _ = et.value.partition(".")
        assert sep == "."
        assert head in valid_namespaces, f"{et.value} has unexpected namespace {head}"


def test_event_type_taxonomy_matches_design() -> None:
    expected = {
        "session.created",
        "session.forked",
        "session.paused",
        "session.resumed",
        "session.merged",
        "session.aborted",
        "session.done",
        "llm.context_assembled",
        "llm.request_sent",
        "llm.response_received",
        "llm.response_parsed",
        # Issue #36 — LLM error + context-window warning audit events.
        "llm.error",
        "llm.context_warning",
        # Issue #36 (Q4) — decide() latency exceeded SC-023 thresholds.
        "decision.latency_degraded",
        "mode.selected",
        "policy.decided",
        "label.propagated",
        "capability.checked",
        "capability.granted",
        "tool.dispatched",
        "tool.returned",
        "approval.requested",
        "approval.approved",
        "approval.denied",
        "approval.deferred",
        "approval.expired",
        # 002 capability delegation chains (T001 / FR-011 / data-model).
        "delegation.granted",
        "delegation.refused",
        "capability.cascade_revoked",
        # 003 v0.9 labeling framework (T014).
        "binding.applied",
        "override.granted",
        "override.attested",
        "override.refused",
        "override.expired",
        "override.use_refused",
        "ratification.applied",
        "pattern3.handle_bind",
        "isolation_region.created",
        "isolation_region.discarded",
        "envelope.dial_changed",
        "risk_register.audit",
        "residual_risk.exception",
        # 003 US2 T046 — FR-031 asymmetry refusal.
        "policy.relaxation_refused",
        # 004 P0 — programmatic primitive applications.
        "inspector.applied",
        "decision_inspector.applied",
        "declassifier.applied",
        # Cookbook Pattern ⑥ — shadow mode.
        "policy.shadowed",
        "enforcement.mode_changed",
    }
    actual = {et.value for et in EventType}
    assert actual == expected, f"missing: {expected - actual}, extra: {actual - expected}"


def test_event_audit_ids_are_unique() -> None:
    a = Event(event_type=EventType.SESSION_CREATED)
    b = Event(event_type=EventType.SESSION_CREATED)
    assert a.audit_id != b.audit_id


def test_event_payload_default_is_empty_dict() -> None:
    event = Event(event_type=EventType.SESSION_CREATED)
    assert event.payload == {}
