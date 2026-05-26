"""Ratification Authorization (FR-014, Q3 2026-05-25).

Reuses the Override Policy state machine pattern: per-severity
{single-authorized | dual-control} with a per-severity authorization
mapping. Hard-floor-touching ratifications default to dual-control;
non-hard-floor default to single-authorized. AI is NEVER authorized.

Ratifications are the mechanism for human approval of AI-suggested
labels/profiles/rules, following the "AI-suggests → human-ratifies →
engine-applies" path. Once ratified, a suggestion becomes durable.
Unlike Override Grants (which are time-boxed and one-shot), ratified
suggestions are persistent until explicitly revoked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import yaml


class RatificationError(RuntimeError):
    """Ratification config malformed or ratification-FSM violation.
    Fail-closed per Principle VI."""


class RatificationSeverity(StrEnum):
    """Severity classification for ratifications, determining the
    authorization policy that applies."""

    HARD_FLOOR = "hard-floor"  # ratifying changes that touch FR-026d hard floors
    HIGH_IMPACT = "high-impact"  # ratifying changes to prohibited/clearance/integrity-related rules
    ROUTINE = "routine"  # ordinary rule additions/edits


class RatificationPolicy(StrEnum):
    """Authorization model for a severity level."""

    SINGLE_AUTHORIZED = "single-authorized"
    DUAL_CONTROL = "dual-control"


class RatificationTargetKind(StrEnum):
    """The type of thing being ratified."""

    LABEL = "label"
    PROFILE = "profile"
    RULE = "rule"


class RatificationState(StrEnum):
    """Ratification FSM state. Transitions:
    PENDING_ATTESTATION → APPLIED | REFUSED | EXPIRED
    APPLIED, REFUSED, EXPIRED are terminal."""

    PENDING_ATTESTATION = "pending_attestation"
    APPLIED = "applied"
    REFUSED = "refused"
    EXPIRED = "expired"


class RatificationRefusalReason(StrEnum):
    """Structured refusal of a ratification request or attestation."""

    POLICY_DISALLOWED = "policy_disallowed"
    UNAUTHORIZED_INVOKER = "unauthorized_invoker"
    ATTESTER_SAME_AS_INVOKER = "attester_same_as_invoker"
    ATTESTER_UNAUTHORIZED = "attester_unauthorized"
    ATTESTATION_REFUSED = "attestation_refused"
    AI_PRINCIPAL_REFUSED = "ai_principal_refused"
    RATIFICATION_EXPIRED = "ratification_expired"
    UNKNOWN_RATIFICATION = "unknown_ratification"


@dataclass(frozen=True)
class RatificationPolicyEntry:
    """One operator-declared entry: how ratification behaves at a
    specific severity level."""

    severity: RatificationSeverity
    policy: RatificationPolicy
    authorized_principal_ids: frozenset[str] = field(default_factory=frozenset)
    attester_principal_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RatificationPolicies:
    """Loaded ratification policy catalogue, keyed by severity."""

    by_severity: dict[RatificationSeverity, RatificationPolicyEntry]

    def get(self, severity: RatificationSeverity) -> RatificationPolicyEntry | None:
        return self.by_severity.get(severity)


@dataclass(frozen=True)
class Ratification:
    """A concrete ratification of a suggested label/profile/rule.
    Once APPLIED, the suggestion becomes durable. Unlike Override Grants,
    ratifications are persistent (not time-boxed)."""

    id: UUID
    target_kind: RatificationTargetKind
    target_id: str  # the identifier of the label/profile/rule being ratified
    severity: RatificationSeverity
    invoker_principal: str
    attester_principal: str | None
    policy_at_ratification: RatificationPolicyEntry
    state: RatificationState
    created_at: datetime
    applied_at: datetime | None = None
    refused_at: datetime | None = None
    audit_id: UUID = field(default_factory=uuid4)

    def is_applied(self) -> bool:
        return self.state is RatificationState.APPLIED

    def is_pending(self) -> bool:
        return self.state is RatificationState.PENDING_ATTESTATION


@dataclass(frozen=True)
class RatificationRefusal:
    """Structured refusal of a ratification request or attestation.
    Carries the audit-ready reason."""

    reason: RatificationRefusalReason
    severity: RatificationSeverity | None = None
    invoker: str | None = None
    detail: str = ""


# --- FSM ------------------------------------------------------------


def _is_ai_principal(principal: str) -> bool:
    """Check if the principal looks like an AI agent (not a human).
    Per FR-014: AI MUST NEVER be authorized to ratify."""
    return principal.lower().startswith("ai-")


def request_ratification(
    *,
    policies: RatificationPolicies,
    target_kind: RatificationTargetKind,
    target_id: str,
    severity: RatificationSeverity,
    invoker: str,
    now: datetime | None = None,
) -> Ratification | RatificationRefusal:
    """Initial ratification request. Returns either a Ratification
    (PENDING_ATTESTATION for dual-control; immediately APPLIED for
    single-authorized) or a structured refusal.

    AI principals are ALWAYS refused (FR-014 last-line requirement).
    """
    eff_now = now or datetime.now(UTC)

    # FR-014: AI MUST NEVER be authorized to ratify
    if _is_ai_principal(invoker):
        return RatificationRefusal(
            reason=RatificationRefusalReason.AI_PRINCIPAL_REFUSED,
            severity=severity,
            invoker=invoker,
            detail="AI principals cannot ratify suggestions",
        )

    entry = policies.get(severity)
    if entry is None:
        return RatificationRefusal(
            reason=RatificationRefusalReason.POLICY_DISALLOWED,
            severity=severity,
            invoker=invoker,
            detail="no operator policy declared for this severity",
        )

    if invoker not in entry.authorized_principal_ids:
        return RatificationRefusal(
            reason=RatificationRefusalReason.UNAUTHORIZED_INVOKER,
            severity=severity,
            invoker=invoker,
        )

    # Determine initial state based on policy
    initial_state = (
        RatificationState.PENDING_ATTESTATION
        if entry.policy is RatificationPolicy.DUAL_CONTROL
        else RatificationState.APPLIED
    )

    return Ratification(
        id=uuid4(),
        target_kind=target_kind,
        target_id=target_id,
        severity=severity,
        invoker_principal=invoker,
        attester_principal=None,
        policy_at_ratification=entry,
        state=initial_state,
        created_at=eff_now,
        applied_at=eff_now if initial_state is RatificationState.APPLIED else None,
    )


def attest_ratification(
    ratification: Ratification,
    *,
    attester: str,
    confirmed: bool,
    now: datetime | None = None,
) -> Ratification | RatificationRefusal:
    """Dual-control attestation step. The attester must be in the
    policy's `attester_principal_ids` AND must differ from the
    invoker (FR-036). If `confirmed` is False, return ATTESTATION_REFUSED."""
    eff_now = now or datetime.now(UTC)

    # FR-014: AI MUST NEVER be authorized to ratify
    if _is_ai_principal(attester):
        return RatificationRefusal(
            reason=RatificationRefusalReason.AI_PRINCIPAL_REFUSED,
            severity=ratification.severity,
            invoker=attester,
            detail="AI principals cannot attest ratifications",
        )

    if ratification.state is not RatificationState.PENDING_ATTESTATION:
        return RatificationRefusal(
            reason=RatificationRefusalReason.ATTESTATION_REFUSED,
            severity=ratification.severity,
            detail=f"ratification state is {ratification.state.value}, not pending_attestation",
        )

    entry = ratification.policy_at_ratification
    if attester == ratification.invoker_principal:
        return RatificationRefusal(
            reason=RatificationRefusalReason.ATTESTER_SAME_AS_INVOKER,
            severity=ratification.severity,
        )

    if attester not in entry.attester_principal_ids:
        return RatificationRefusal(
            reason=RatificationRefusalReason.ATTESTER_UNAUTHORIZED,
            severity=ratification.severity,
        )

    if not confirmed:
        return RatificationRefusal(
            reason=RatificationRefusalReason.ATTESTATION_REFUSED,
            severity=ratification.severity,
        )

    from dataclasses import replace

    return replace(
        ratification,
        state=RatificationState.APPLIED,
        attester_principal=attester,
        applied_at=eff_now,
    )


# --- YAML loader ----------------------------------------------------


def load(path: Path) -> RatificationPolicies:
    """Load configs/ratification_policy.yaml. Fail-closed on missing/
    unparseable. Empty `policies:` permitted — every ratification request
    refuses with POLICY_DISALLOWED."""
    if not path.is_file():
        raise RatificationError(f"ratification_policy config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RatificationError(f"unparseable: {path} — {e}") from e
    if data is None:
        return RatificationPolicies(by_severity={})
    raw = data.get("policies") or []
    if not isinstance(raw, list):
        raise RatificationError(f"'policies' must be a list: {path}")
    by_severity: dict[RatificationSeverity, RatificationPolicyEntry] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RatificationError(f"policies[{i}] is not an object")
        try:
            severity = RatificationSeverity(str(item["severity"]))
            policy = RatificationPolicy(str(item["policy"]))
        except (KeyError, ValueError) as e:
            raise RatificationError(f"policies[{i}]: {e}") from e
        if severity in by_severity:
            raise RatificationError(f"policies[{i}] duplicate severity: {severity.value}")
        entry = RatificationPolicyEntry(
            severity=severity,
            policy=policy,
            authorized_principal_ids=frozenset(
                str(p) for p in item.get("authorized_principal_ids", [])
            ),
            attester_principal_ids=frozenset(
                str(p) for p in item.get("attester_principal_ids", [])
            ),
        )
        by_severity[severity] = entry
    return RatificationPolicies(by_severity=by_severity)
