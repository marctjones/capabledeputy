"""Tests for Ratification Authorization (T124-T127, FR-014, Q3 2026-05-25).

The spec clarification locked in:
- Hard-floor ratifications default to dual-control
- Non-hard-floor ratifications default to single-authorized
- Invoker MUST NOT equal attester (dual-control)
- Unauthorized principals' ratification attempts are refused + audited
- AI principals (names like "ai-agent") MUST be refused (FR-014 last-line)
- RATIFICATION_APPLIED audit event carries the required payload shape
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from capabledeputy.audit.events import Event, EventType
from capabledeputy.policy.ratification import (
    Ratification,
    RatificationPolicies,
    RatificationPolicy,
    RatificationPolicyEntry,
    RatificationRefusal,
    RatificationRefusalReason,
    RatificationSeverity,
    RatificationState,
    RatificationTargetKind,
    attest_ratification,
    request_ratification,
)


@pytest.fixture
def default_policies() -> RatificationPolicies:
    """Standard policy set: hard-floor → dual-control, others → single-authorized."""
    return RatificationPolicies(
        by_severity={
            RatificationSeverity.HARD_FLOOR: RatificationPolicyEntry(
                severity=RatificationSeverity.HARD_FLOOR,
                policy=RatificationPolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice", "bob"}),
                attester_principal_ids=frozenset({"alice", "bob", "carol"}),
            ),
            RatificationSeverity.HIGH_IMPACT: RatificationPolicyEntry(
                severity=RatificationSeverity.HIGH_IMPACT,
                policy=RatificationPolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"alice"}),
            ),
            RatificationSeverity.ROUTINE: RatificationPolicyEntry(
                severity=RatificationSeverity.ROUTINE,
                policy=RatificationPolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"alice", "bob"}),
                attester_principal_ids=frozenset({"alice", "bob"}),
            ),
        }
    )


# --- Hard-floor defaults to dual-control --------------------------------


def test_hard_floor_ratification_defaults_to_pending_attestation(
    default_policies: RatificationPolicies,
) -> None:
    """Hard-floor ratifications default to PENDING_ATTESTATION because the
    policy is dual-control."""
    result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.RULE,
        target_id="rule-123",
        severity=RatificationSeverity.HARD_FLOOR,
        invoker="alice",
    )
    assert isinstance(result, Ratification)
    assert result.state is RatificationState.PENDING_ATTESTATION
    assert result.attester_principal is None


def test_hard_floor_requires_attester_confirmation(
    default_policies: RatificationPolicies,
) -> None:
    """Hard-floor ratifications need attestation by a second principal."""
    req_result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.RULE,
        target_id="rule-456",
        severity=RatificationSeverity.HARD_FLOOR,
        invoker="alice",
    )
    assert isinstance(req_result, Ratification)

    # Second principal attests
    attest_result = attest_ratification(
        req_result,
        attester="carol",
        confirmed=True,
    )
    assert isinstance(attest_result, Ratification)
    assert attest_result.state is RatificationState.APPLIED
    assert attest_result.attester_principal == "carol"


# --- Non-hard-floor defaults to single-authorized -----------------------


def test_routine_ratification_applies_immediately(
    default_policies: RatificationPolicies,
) -> None:
    """Routine ratifications default to APPLIED (single-authorized) without
    needing attestation."""
    result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="label-789",
        severity=RatificationSeverity.ROUTINE,
        invoker="alice",
    )
    assert isinstance(result, Ratification)
    assert result.state is RatificationState.APPLIED
    assert result.applied_at is not None


def test_high_impact_ratification_applies_immediately(
    default_policies: RatificationPolicies,
) -> None:
    """High-impact ratifications also default to APPLIED (single-authorized)."""
    result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.PROFILE,
        target_id="profile-999",
        severity=RatificationSeverity.HIGH_IMPACT,
        invoker="alice",
    )
    assert isinstance(result, Ratification)
    assert result.state is RatificationState.APPLIED


# --- Invoker MUST NOT equal attester (dual-control) --------------------


def test_invoker_cannot_be_attester_dual_control(
    default_policies: RatificationPolicies,
) -> None:
    """In dual-control, the attester must be a distinct principal from the
    invoker (FR-036 principle)."""
    req_result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.RULE,
        target_id="rule-999",
        severity=RatificationSeverity.HARD_FLOOR,
        invoker="alice",
    )
    assert isinstance(req_result, Ratification)

    # Alice tries to attest her own request
    attest_result = attest_ratification(
        req_result,
        attester="alice",
        confirmed=True,
    )
    assert isinstance(attest_result, RatificationRefusal)
    assert attest_result.reason is RatificationRefusalReason.ATTESTER_SAME_AS_INVOKER


# --- Unauthorized principal's ratification attempt is refused + audited


def test_unauthorized_principal_refused(
    default_policies: RatificationPolicies,
) -> None:
    """An unauthorized invoker's ratification request is refused."""
    result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="label-xxx",
        severity=RatificationSeverity.ROUTINE,
        invoker="unauthorized-user",
    )
    assert isinstance(result, RatificationRefusal)
    assert result.reason is RatificationRefusalReason.UNAUTHORIZED_INVOKER
    assert result.severity == RatificationSeverity.ROUTINE
    assert result.invoker == "unauthorized-user"


def test_unauthorized_attester_refused(
    default_policies: RatificationPolicies,
) -> None:
    """An unauthorized attester is refused even if the invoker was
    authorized."""
    req_result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.RULE,
        target_id="rule-yyy",
        severity=RatificationSeverity.HARD_FLOOR,
        invoker="bob",
    )
    assert isinstance(req_result, Ratification)

    # An unauthorized attester tries to attest
    attest_result = attest_ratification(
        req_result,
        attester="unauthorized-user",
        confirmed=True,
    )
    assert isinstance(attest_result, RatificationRefusal)
    assert attest_result.reason is RatificationRefusalReason.ATTESTER_UNAUTHORIZED


# --- AI principals MUST be refused (FR-014 last-line) -------------------


def test_ai_principal_invoker_refused() -> None:
    """AI principals (names starting with 'ai-') MUST be refused for
    invocation (FR-014 last-line requirement)."""
    policies = RatificationPolicies(
        by_severity={
            RatificationSeverity.ROUTINE: RatificationPolicyEntry(
                severity=RatificationSeverity.ROUTINE,
                policy=RatificationPolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"ai-agent"}),
                attester_principal_ids=frozenset(),
            ),
        }
    )
    result = request_ratification(
        policies=policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="label-ai",
        severity=RatificationSeverity.ROUTINE,
        invoker="ai-agent",
    )
    assert isinstance(result, RatificationRefusal)
    assert result.reason is RatificationRefusalReason.AI_PRINCIPAL_REFUSED
    assert result.invoker == "ai-agent"


def test_ai_principal_attester_refused() -> None:
    """AI principals MUST be refused for attestation too."""
    policies = RatificationPolicies(
        by_severity={
            RatificationSeverity.HARD_FLOOR: RatificationPolicyEntry(
                severity=RatificationSeverity.HARD_FLOOR,
                policy=RatificationPolicy.DUAL_CONTROL,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset({"ai-assistant", "bob"}),
            ),
        }
    )
    req_result = request_ratification(
        policies=policies,
        target_kind=RatificationTargetKind.RULE,
        target_id="rule-ai",
        severity=RatificationSeverity.HARD_FLOOR,
        invoker="alice",
    )
    assert isinstance(req_result, Ratification)

    # AI attester tries to confirm
    attest_result = attest_ratification(
        req_result,
        attester="ai-assistant",
        confirmed=True,
    )
    assert isinstance(attest_result, RatificationRefusal)
    assert attest_result.reason is RatificationRefusalReason.AI_PRINCIPAL_REFUSED


def test_ai_principal_case_insensitive() -> None:
    """Check 'AI-' prefix case-insensitively."""
    policies = RatificationPolicies(
        by_severity={
            RatificationSeverity.ROUTINE: RatificationPolicyEntry(
                severity=RatificationSeverity.ROUTINE,
                policy=RatificationPolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"AI-Model"}),
                attester_principal_ids=frozenset(),
            ),
        }
    )
    # Capital AI- prefix should also be caught
    result = request_ratification(
        policies=policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="label-ai2",
        severity=RatificationSeverity.ROUTINE,
        invoker="AI-Model",
    )
    assert isinstance(result, RatificationRefusal)
    assert result.reason is RatificationRefusalReason.AI_PRINCIPAL_REFUSED


# --- RATIFICATION_APPLIED audit event shape ----------------------------


def test_ratification_applied_audit_event_shape() -> None:
    """Verify RATIFICATION_APPLIED event can be emitted with the correct
    payload structure: {ratification_id, target_kind, invoker, attester,
    severity, audit_id}."""
    ratification_id = uuid4()
    invoker = "alice"
    attester = "bob"
    severity = RatificationSeverity.HIGH_IMPACT.value
    target_kind = RatificationTargetKind.RULE.value
    audit_id = uuid4()

    event = Event(
        event_type=EventType.RATIFICATION_APPLIED,
        session_id=uuid4(),
        payload={
            "ratification_id": str(ratification_id),
            "target_kind": target_kind,
            "invoker": invoker,
            "attester": attester,
            "severity": severity,
            "audit_id": str(audit_id),
        },
    )

    assert event.event_type == EventType.RATIFICATION_APPLIED
    assert event.payload["ratification_id"] == str(ratification_id)
    assert event.payload["target_kind"] == target_kind
    assert event.payload["invoker"] == invoker
    assert event.payload["attester"] == attester
    assert event.payload["severity"] == severity
    assert event.payload["audit_id"] == str(audit_id)


# --- Comprehensive integration scenarios --------------------------------


def test_hard_floor_dual_control_flow(default_policies: RatificationPolicies) -> None:
    """Complete flow: hard-floor ratification requires two distinct humans."""
    # Alice invokes a hard-floor ratification
    invoke_result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.RULE,
        target_id="hard-rule-123",
        severity=RatificationSeverity.HARD_FLOOR,
        invoker="alice",
    )
    assert isinstance(invoke_result, Ratification)
    assert invoke_result.state is RatificationState.PENDING_ATTESTATION
    assert invoke_result.invoker_principal == "alice"
    assert invoke_result.attester_principal is None

    # Carol (not alice) attests
    attest_result = attest_ratification(
        invoke_result,
        attester="carol",
        confirmed=True,
    )
    assert isinstance(attest_result, Ratification)
    assert attest_result.state is RatificationState.APPLIED
    assert attest_result.attester_principal == "carol"
    assert attest_result.applied_at is not None


def test_single_authorized_flow(default_policies: RatificationPolicies) -> None:
    """Single-authorized ratifications apply immediately without attestation."""
    result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="routine-label-456",
        severity=RatificationSeverity.ROUTINE,
        invoker="bob",
    )
    assert isinstance(result, Ratification)
    assert result.state is RatificationState.APPLIED
    assert result.invoker_principal == "bob"
    assert result.applied_at is not None


def test_attestation_refusal_blocks_application(
    default_policies: RatificationPolicies,
) -> None:
    """When an attester confirms=False, the ratification is refused."""
    req_result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.PROFILE,
        target_id="profile-denied",
        severity=RatificationSeverity.HARD_FLOOR,
        invoker="bob",
    )
    assert isinstance(req_result, Ratification)

    # Carol refuses the ratification
    deny_result = attest_ratification(
        req_result,
        attester="carol",
        confirmed=False,
    )
    assert isinstance(deny_result, RatificationRefusal)
    assert deny_result.reason is RatificationRefusalReason.ATTESTATION_REFUSED


def test_ratification_requires_authorized_invoker(
    default_policies: RatificationPolicies,
) -> None:
    """Ratifications fail-closed: invokers must be pre-authorized."""
    result = request_ratification(
        policies=default_policies,
        target_kind=RatificationTargetKind.RULE,
        target_id="rule-forbidden",
        severity=RatificationSeverity.HIGH_IMPACT,
        invoker="eve",  # not authorized for high-impact
    )
    assert isinstance(result, RatificationRefusal)
    assert result.reason is RatificationRefusalReason.UNAUTHORIZED_INVOKER
    assert result.severity == RatificationSeverity.HIGH_IMPACT


def test_empty_authorization_fails_closed() -> None:
    """When no principals are authorized for a severity, all attempts
    fail with POLICY_DISALLOWED (fail-closed design)."""
    policies = RatificationPolicies(
        by_severity={
            RatificationSeverity.ROUTINE: RatificationPolicyEntry(
                severity=RatificationSeverity.ROUTINE,
                policy=RatificationPolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset(),  # No one authorized
                attester_principal_ids=frozenset(),
            ),
        }
    )
    result = request_ratification(
        policies=policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="label-no-auth",
        severity=RatificationSeverity.ROUTINE,
        invoker="alice",
    )
    assert isinstance(result, RatificationRefusal)
    assert result.reason is RatificationRefusalReason.UNAUTHORIZED_INVOKER


def test_ratification_preserves_metadata() -> None:
    """Ratifications preserve all metadata through the FSM."""
    policies = RatificationPolicies(
        by_severity={
            RatificationSeverity.HIGH_IMPACT: RatificationPolicyEntry(
                severity=RatificationSeverity.HIGH_IMPACT,
                policy=RatificationPolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"ops"}),
                attester_principal_ids=frozenset(),
            ),
        }
    )
    result = request_ratification(
        policies=policies,
        target_kind=RatificationTargetKind.PROFILE,
        target_id="profile-metadata-test",
        severity=RatificationSeverity.HIGH_IMPACT,
        invoker="ops",
    )
    assert isinstance(result, Ratification)
    assert result.target_kind == RatificationTargetKind.PROFILE
    assert result.target_id == "profile-metadata-test"
    assert result.severity == RatificationSeverity.HIGH_IMPACT
    assert result.invoker_principal == "ops"


def test_ratification_id_uniqueness() -> None:
    """Each ratification gets a unique ID."""
    policies = RatificationPolicies(
        by_severity={
            RatificationSeverity.ROUTINE: RatificationPolicyEntry(
                severity=RatificationSeverity.ROUTINE,
                policy=RatificationPolicy.SINGLE_AUTHORIZED,
                authorized_principal_ids=frozenset({"alice"}),
                attester_principal_ids=frozenset(),
            ),
        }
    )
    r1 = request_ratification(
        policies=policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="label-1",
        severity=RatificationSeverity.ROUTINE,
        invoker="alice",
    )
    r2 = request_ratification(
        policies=policies,
        target_kind=RatificationTargetKind.LABEL,
        target_id="label-2",
        severity=RatificationSeverity.ROUTINE,
        invoker="alice",
    )
    assert isinstance(r1, Ratification)
    assert isinstance(r2, Ratification)
    assert r1.id != r2.id
    assert r1.audit_id != r2.audit_id
