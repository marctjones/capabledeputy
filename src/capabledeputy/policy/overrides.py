"""Override Policy / Authorization / Grant FSM (003 US6 / FR-032/036/038).

Override is the mechanism for crossing a *hard floor* — outcomes
that ordinary approval cannot reach (prohibited tier, admissibility-
exclusion, max-tier-clearance, integrity-floor). Distinct from
ordinary approval (FR-038):
  - The resulting capability carries `origin=OVERRIDE_GRANTED`.
  - The audit objects (`override.granted`, `override.attested`, etc.)
    are separate from approval events.
  - No model path may invoke or attest — the CLI/UI does
    (Principle I + V).

Three policies:
  - `disallowed` — refuses every override request, even from
    authorized invokers.
  - `single-authorized` — one named principal may invoke; friction
    confirmation required.
  - `dual-control` — invoker plus a distinct attester. Attester
    sees engine-authored verbatim facts, never model prose.

Every grant has a `friction_level` (low/medium/maximal) and a
non-null `expires_at`. Grants are session-bound, non-inheritable,
and one-shot (consumed_at recorded on use).

Per the contract in contracts/override.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import yaml

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
)


class OverrideError(RuntimeError):
    """Override config malformed or override-FSM violation. Fail-closed
    per Principle VI."""


class OverridePolicy(StrEnum):
    DISALLOWED = "disallowed"
    SINGLE_AUTHORIZED = "single-authorized"
    DUAL_CONTROL = "dual-control"


class HardFloor(StrEnum):
    PROHIBITED = "prohibited"
    ADMISSIBILITY_EXCLUSION = "admissibility-exclusion"
    MAX_TIER_CLEARANCE = "max-tier-clearance"
    INTEGRITY_FLOOR = "integrity-floor"


class FrictionLevel(StrEnum):
    """Required friction strength. MAXIMAL for `prohibited` and other
    gravest cells — typed acknowledgement of the specific
    irreversible effect by the invoker."""

    LOW = "low"
    MEDIUM = "medium"
    MAXIMAL = "maximal"


# Default friction by floor — operator may override in policy entries.
_DEFAULT_FRICTION: dict[HardFloor, FrictionLevel] = {
    HardFloor.PROHIBITED: FrictionLevel.MAXIMAL,
    HardFloor.ADMISSIBILITY_EXCLUSION: FrictionLevel.MAXIMAL,
    HardFloor.MAX_TIER_CLEARANCE: FrictionLevel.MEDIUM,
    HardFloor.INTEGRITY_FLOOR: FrictionLevel.MEDIUM,
}


class OverrideRefusalReason(StrEnum):
    POLICY_DISALLOWED = "policy_disallowed"
    UNAUTHORIZED_INVOKER = "unauthorized_invoker"
    FRICTION_NOT_MET = "friction_not_met"
    ATTESTER_SAME_AS_INVOKER = "attester_same_as_invoker"
    ATTESTER_UNAUTHORIZED = "attester_unauthorized"
    ATTESTATION_REFUSED = "attestation_refused"
    GRANT_EXPIRED = "grant_expired"
    GRANT_CONSUMED = "grant_consumed"
    ACTION_MISMATCH = "action_mismatch"
    UNKNOWN_GRANT = "unknown_grant"


@dataclass(frozen=True)
class OverridePolicyEntry:
    """One operator-declared entry: how the override mechanism behaves
    at a specific hard floor."""

    floor: HardFloor
    policy: OverridePolicy
    authorized_principal_ids: frozenset[str] = field(default_factory=frozenset)
    attester_principal_ids: frozenset[str] = field(default_factory=frozenset)
    expiry_seconds: int = 300  # 5 min default; operator scales by severity
    friction_level: FrictionLevel | None = None  # None ⇒ use _DEFAULT_FRICTION

    def effective_friction(self) -> FrictionLevel:
        return self.friction_level or _DEFAULT_FRICTION[self.floor]


@dataclass(frozen=True)
class OverridePolicies:
    """Loaded override policy catalogue, keyed by hard floor."""

    by_floor: dict[HardFloor, OverridePolicyEntry]

    def get(self, floor: HardFloor) -> OverridePolicyEntry | None:
        return self.by_floor.get(floor)


class GrantState(StrEnum):
    """OverrideGrant FSM state. The transitions are:
    PENDING_ATTESTATION → ACTIVE (dual-control attested) | EXPIRED
    ACTIVE → CONSUMED (used) | EXPIRED
    Terminal: REFUSED, EXPIRED, CONSUMED."""

    PENDING_ATTESTATION = "pending_attestation"
    ACTIVE = "active"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REFUSED = "refused"


@dataclass(frozen=True)
class OverrideGrant:
    """A concrete override authorization for a specific session +
    action + target. One-shot (consumed_at marks use). Non-inheritable
    across fork/delegate (the session_id pin makes that structural)."""

    id: UUID
    session_id: UUID
    action_kind: CapabilityKind
    target: str
    target_category_tier: tuple[str, str]
    hard_floor_crossed: HardFloor
    invoker_principal: str
    attester_principal: str | None
    policy_at_grant: OverridePolicyEntry
    friction_level: FrictionLevel
    state: GrantState
    expires_at: datetime
    consumed_at: datetime | None = None
    audit_id: UUID = field(default_factory=uuid4)

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def is_for(self, *, action_kind: CapabilityKind, target: str) -> bool:
        return self.action_kind == action_kind and self.target == target


@dataclass(frozen=True)
class OverrideRefusal:
    """Structured refusal of an override request or use. Carries the
    audit-ready reason."""

    reason: OverrideRefusalReason
    floor: HardFloor | None = None
    invoker: str | None = None
    detail: str = ""


# --- FSM ------------------------------------------------------------


def request_override(
    *,
    policies: OverridePolicies,
    session_id: UUID,
    action_kind: CapabilityKind,
    target: str,
    target_category_tier: tuple[str, str],
    floor: HardFloor,
    invoker: str,
    friction_confirmed: bool,
    now: datetime | None = None,
) -> OverrideGrant | OverrideRefusal:
    """Initial override request. Returns either a Grant (ACTIVE for
    single-authorized; PENDING_ATTESTATION for dual-control) or a
    structured refusal.

    Friction confirmation is the typed acknowledgement that the
    invoker made — the CLI/UI layer collected it from a human and
    passes the boolean here. The engine itself NEVER generates the
    acknowledgement text; that's operator/UI work.
    """
    eff_now = now or datetime.now(UTC)
    entry = policies.get(floor)
    if entry is None:
        return OverrideRefusal(
            reason=OverrideRefusalReason.POLICY_DISALLOWED,
            floor=floor,
            invoker=invoker,
            detail="no operator policy declared for this floor",
        )
    if entry.policy is OverridePolicy.DISALLOWED:
        return OverrideRefusal(
            reason=OverrideRefusalReason.POLICY_DISALLOWED,
            floor=floor,
            invoker=invoker,
        )
    if invoker not in entry.authorized_principal_ids:
        return OverrideRefusal(
            reason=OverrideRefusalReason.UNAUTHORIZED_INVOKER,
            floor=floor,
            invoker=invoker,
        )
    if not friction_confirmed:
        return OverrideRefusal(
            reason=OverrideRefusalReason.FRICTION_NOT_MET,
            floor=floor,
            invoker=invoker,
        )

    initial_state = (
        GrantState.PENDING_ATTESTATION
        if entry.policy is OverridePolicy.DUAL_CONTROL
        else GrantState.ACTIVE
    )
    return OverrideGrant(
        id=uuid4(),
        session_id=session_id,
        action_kind=action_kind,
        target=target,
        target_category_tier=target_category_tier,
        hard_floor_crossed=floor,
        invoker_principal=invoker,
        attester_principal=None,
        policy_at_grant=entry,
        friction_level=entry.effective_friction(),
        state=initial_state,
        expires_at=eff_now + timedelta(seconds=entry.expiry_seconds),
    )


def attest_override(
    grant: OverrideGrant,
    *,
    attester: str,
    confirmed: bool,
    now: datetime | None = None,
) -> OverrideGrant | OverrideRefusal:
    """Dual-control attestation step. The attester must be in the
    policy's `attester_principal_ids` AND must differ from the
    invoker (FR-036). If `confirmed` is False, return ATTESTATION_REFUSED."""
    eff_now = now or datetime.now(UTC)
    if grant.is_expired(eff_now):
        return OverrideRefusal(
            reason=OverrideRefusalReason.GRANT_EXPIRED,
            floor=grant.hard_floor_crossed,
        )
    if grant.state is not GrantState.PENDING_ATTESTATION:
        return OverrideRefusal(
            reason=OverrideRefusalReason.ATTESTATION_REFUSED,
            floor=grant.hard_floor_crossed,
            detail=f"grant state is {grant.state.value}, not pending_attestation",
        )
    entry = grant.policy_at_grant
    if attester == grant.invoker_principal:
        return OverrideRefusal(
            reason=OverrideRefusalReason.ATTESTER_SAME_AS_INVOKER,
            floor=grant.hard_floor_crossed,
        )
    if attester not in entry.attester_principal_ids:
        return OverrideRefusal(
            reason=OverrideRefusalReason.ATTESTER_UNAUTHORIZED,
            floor=grant.hard_floor_crossed,
        )
    if not confirmed:
        return OverrideRefusal(
            reason=OverrideRefusalReason.ATTESTATION_REFUSED,
            floor=grant.hard_floor_crossed,
        )
    from dataclasses import replace

    return replace(
        grant,
        state=GrantState.ACTIVE,
        attester_principal=attester,
    )


def use_override(
    grant: OverrideGrant,
    *,
    action_kind: CapabilityKind,
    target: str,
    now: datetime | None = None,
) -> Capability | OverrideRefusal:
    """Consume an active grant to mint a capability with
    `origin=OVERRIDE_GRANTED`. Refused if the grant is expired,
    consumed, or the action/target doesn't match the grant."""
    eff_now = now or datetime.now(UTC)
    if grant.state is GrantState.EXPIRED or grant.is_expired(eff_now):
        return OverrideRefusal(
            reason=OverrideRefusalReason.GRANT_EXPIRED,
            floor=grant.hard_floor_crossed,
        )
    if grant.state is GrantState.CONSUMED:
        return OverrideRefusal(
            reason=OverrideRefusalReason.GRANT_CONSUMED,
            floor=grant.hard_floor_crossed,
        )
    if grant.state is not GrantState.ACTIVE:
        return OverrideRefusal(
            reason=OverrideRefusalReason.ATTESTATION_REFUSED,
            floor=grant.hard_floor_crossed,
            detail=f"grant state is {grant.state.value}, not active",
        )
    if not grant.is_for(action_kind=action_kind, target=target):
        return OverrideRefusal(
            reason=OverrideRefusalReason.ACTION_MISMATCH,
            floor=grant.hard_floor_crossed,
        )
    return Capability(
        kind=action_kind,
        pattern=target,
        expiry=CapabilityExpiry.ONE_SHOT,
        origin=CapabilityOrigin.OVERRIDE_GRANTED,
        expires_at=grant.expires_at,
        override_grant_id=grant.id,
    )


# --- YAML loader ----------------------------------------------------


class OverrideGrantStore:
    """In-memory grant store. Holds grants by id and indexes by
    (session_id, action_kind, target) so the engine.decide() chokepoint
    can short-circuit a matching active grant into ALLOW. Persistence
    (override_grants table) lands in a follow-up; this in-memory layer
    is enough for the demo path and tests.
    """

    def __init__(self) -> None:
        self._by_id: dict[UUID, OverrideGrant] = {}

    def add(self, grant: OverrideGrant) -> None:
        self._by_id[grant.id] = grant

    def get(self, grant_id: UUID) -> OverrideGrant | None:
        return self._by_id.get(grant_id)

    def update(self, grant: OverrideGrant) -> None:
        if grant.id in self._by_id:
            self._by_id[grant.id] = grant

    def list_all(self) -> list[OverrideGrant]:
        return list(self._by_id.values())

    def find_active(
        self,
        *,
        session_id: UUID,
        action_kind: CapabilityKind,
        target: str,
        now: datetime,
    ) -> OverrideGrant | None:
        """Return an ACTIVE, not-expired, not-consumed grant matching
        (session_id, action_kind, target). Multiple matches would be
        a bug; we return the first by id-order for determinism.
        """
        candidates = sorted(
            (
                g
                for g in self._by_id.values()
                if g.session_id == session_id
                and g.action_kind == action_kind
                and g.target == target
                and g.state is GrantState.ACTIVE
                and not g.is_expired(now)
                and g.consumed_at is None
            ),
            key=lambda g: str(g.id),
        )
        return candidates[0] if candidates else None


def load(path: Path) -> OverridePolicies:
    """Load configs/override_policy.yaml. Fail-closed on missing/
    unparseable. Empty `policies:` permitted — every override request
    refuses with POLICY_DISALLOWED."""
    if not path.is_file():
        raise OverrideError(f"override_policy config missing: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise OverrideError(f"unparseable: {path} — {e}") from e
    if data is None:
        return OverridePolicies(by_floor={})
    raw = data.get("policies") or []
    if not isinstance(raw, list):
        raise OverrideError(f"'policies' must be a list: {path}")
    by_floor: dict[HardFloor, OverridePolicyEntry] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise OverrideError(f"policies[{i}] is not an object")
        try:
            floor = HardFloor(str(item["tier_or_floor"]))
            policy = OverridePolicy(str(item["policy"]))
        except (KeyError, ValueError) as e:
            raise OverrideError(f"policies[{i}]: {e}") from e
        if floor in by_floor:
            raise OverrideError(f"policies[{i}] duplicate floor: {floor.value}")
        entry = OverridePolicyEntry(
            floor=floor,
            policy=policy,
            authorized_principal_ids=frozenset(
                str(p) for p in item.get("authorized_principal_ids", [])
            ),
            attester_principal_ids=frozenset(
                str(p) for p in item.get("attester_principal_ids", [])
            ),
            expiry_seconds=int(item.get("expiry_seconds", 300)),
            friction_level=(
                FrictionLevel(str(item["friction_level"])) if "friction_level" in item else None
            ),
        )
        by_floor[floor] = entry
    return OverridePolicies(by_floor=by_floor)
