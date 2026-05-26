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
from typing import Any
from uuid import UUID, uuid4

import yaml

from capabledeputy.policy.capabilities import (
    Capability,
    CapabilityExpiry,
    CapabilityKind,
    CapabilityOrigin,
    kind_name,
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


# Issue 003 / Q2 (spec.md §Clarifications 2026-05-25, FR-032):
# Default Override Grant expiry is 15 minutes (900s); absolute spec-enforced
# cap is 60 minutes (3600s). No operator configuration, even misconfigured,
# may produce an Override Grant whose expiry exceeds 3600s — the validator
# in OverridePolicyEntry.__post_init__ refuses such entries at load time.
OVERRIDE_EXPIRY_DEFAULT_SECONDS = 900
OVERRIDE_EXPIRY_MAX_SECONDS = 3600


class OverridePolicyValidationError(ValueError):
    """Raised when an OverridePolicyEntry violates the spec's hard caps
    (e.g., expiry_seconds > OVERRIDE_EXPIRY_MAX_SECONDS per FR-032)."""


@dataclass(frozen=True)
class OverridePolicyEntry:
    """One operator-declared entry: how the override mechanism behaves
    at a specific hard floor."""

    floor: HardFloor
    policy: OverridePolicy
    authorized_principal_ids: frozenset[str] = field(default_factory=frozenset)
    attester_principal_ids: frozenset[str] = field(default_factory=frozenset)
    expiry_seconds: int = OVERRIDE_EXPIRY_DEFAULT_SECONDS  # 15 min — Q2 / FR-032
    friction_level: FrictionLevel | None = None  # None ⇒ use _DEFAULT_FRICTION

    def __post_init__(self) -> None:
        # FR-032 / Q2 (2026-05-25): the spec enforces a 3600s absolute cap.
        # An expiry above that is refused at policy authoring / load time,
        # so a misconfiguration can't yield an all-day bypass.
        if self.expiry_seconds <= 0:
            raise OverridePolicyValidationError(
                f"OverridePolicyEntry expiry_seconds must be positive "
                f"(got {self.expiry_seconds})",
            )
        if self.expiry_seconds > OVERRIDE_EXPIRY_MAX_SECONDS:
            raise OverridePolicyValidationError(
                f"OverridePolicyEntry expiry_seconds={self.expiry_seconds} "
                f"exceeds the FR-032 hard cap of "
                f"{OVERRIDE_EXPIRY_MAX_SECONDS}s "
                f"({OVERRIDE_EXPIRY_MAX_SECONDS // 60} min). Operators "
                f"cannot grant longer bypasses than this by configuration.",
            )

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
    action_kind: CapabilityKind | str
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

    def is_for(self, *, action_kind: CapabilityKind | str, target: str) -> bool:
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
    action_kind: CapabilityKind | str,
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
    action_kind: CapabilityKind | str,
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
    can short-circuit a matching active grant into ALLOW.

    Optional persistence: when constructed with `db_path`, grants are
    serialized to the `override_grants` SQLite table on every add/
    update. The store eagerly loads the table on init() so a daemon
    restart preserves all ACTIVE grants. Use the bare in-memory
    constructor for tests and demos that don't need persistence.
    """

    def __init__(self, db_path: Any = None) -> None:
        self._by_id: dict[UUID, OverrideGrant] = {}
        self._db_path = db_path
        if db_path is not None:
            self._load_from_db()

    def _connect(self) -> Any:
        import sqlite3

        conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_from_db(self) -> None:
        """Eager load every persisted grant into memory. Called from
        __init__ when db_path is set. Idempotent.

        Defensive: if the table or `state` column doesn't exist yet
        (daemon constructed the store before app.startup() ran the
        schema migration), treat as 'no grants to load' and continue.
        The first add() will create the table via the schema's
        IF NOT EXISTS + the v6 ALTER fixup."""
        import json
        import sqlite3
        from contextlib import closing

        with closing(self._connect()) as conn:
            try:
                cursor = conn.execute(
                    "SELECT id, session_id, action_kind, target, "
                    "target_category_tier, hard_floor_crossed, invoker_principal, "
                    "attester_principal, override_policy_at_grant, friction_level, "
                    "audit_id, expires_at, consumed_at, state FROM override_grants",
                )
            except sqlite3.OperationalError:
                # Table or column not yet present — nothing to load.
                return
            for row in cursor.fetchall():
                entry_raw = json.loads(row["override_policy_at_grant"])
                entry = OverridePolicyEntry(
                    floor=HardFloor(entry_raw["floor"]),
                    policy=OverridePolicy(entry_raw["policy"]),
                    authorized_principal_ids=frozenset(
                        entry_raw.get("authorized_principal_ids", []),
                    ),
                    attester_principal_ids=frozenset(
                        entry_raw.get("attester_principal_ids", []),
                    ),
                    expiry_seconds=int(entry_raw.get("expiry_seconds", OVERRIDE_EXPIRY_DEFAULT_SECONDS)),
                    friction_level=(
                        FrictionLevel(entry_raw["friction_level"])
                        if entry_raw.get("friction_level")
                        else None
                    ),
                )
                tier_raw = json.loads(row["target_category_tier"])
                grant = OverrideGrant(
                    id=UUID(row["id"]),
                    session_id=UUID(row["session_id"]),
                    action_kind=CapabilityKind(row["action_kind"]),
                    target=row["target"],
                    target_category_tier=(tier_raw[0], tier_raw[1]),
                    hard_floor_crossed=HardFloor(row["hard_floor_crossed"]),
                    invoker_principal=row["invoker_principal"],
                    attester_principal=row["attester_principal"],
                    policy_at_grant=entry,
                    friction_level=FrictionLevel(row["friction_level"]),
                    state=GrantState(row["state"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]),
                    consumed_at=(
                        datetime.fromisoformat(row["consumed_at"]) if row["consumed_at"] else None
                    ),
                    audit_id=UUID(row["audit_id"]),
                )
                self._by_id[grant.id] = grant

    def _persist(self, grant: OverrideGrant) -> None:
        """UPSERT a grant into the override_grants table. No-op when
        no db_path was wired."""
        if self._db_path is None:
            return
        import json
        from contextlib import closing

        entry_payload = {
            "floor": grant.policy_at_grant.floor.value,
            "policy": grant.policy_at_grant.policy.value,
            "authorized_principal_ids": sorted(
                grant.policy_at_grant.authorized_principal_ids,
            ),
            "attester_principal_ids": sorted(
                grant.policy_at_grant.attester_principal_ids,
            ),
            "expiry_seconds": grant.policy_at_grant.expiry_seconds,
            "friction_level": (
                grant.policy_at_grant.friction_level.value
                if grant.policy_at_grant.friction_level is not None
                else None
            ),
        }
        tier_payload = list(grant.target_category_tier)
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO override_grants ("
                "id, session_id, action_kind, target, target_category_tier, "
                "hard_floor_crossed, invoker_principal, attester_principal, "
                "override_policy_at_grant, friction_level, audit_id, "
                "expires_at, consumed_at, state"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "attester_principal = excluded.attester_principal, "
                "consumed_at = excluded.consumed_at, "
                "state = excluded.state",
                (
                    str(grant.id),
                    str(grant.session_id),
                    kind_name(grant.action_kind),
                    grant.target,
                    json.dumps(tier_payload),
                    grant.hard_floor_crossed.value,
                    grant.invoker_principal,
                    grant.attester_principal,
                    json.dumps(entry_payload),
                    grant.friction_level.value,
                    str(grant.audit_id),
                    grant.expires_at.isoformat(),
                    grant.consumed_at.isoformat() if grant.consumed_at else None,
                    grant.state.value,
                ),
            )

    def add(self, grant: OverrideGrant) -> None:
        self._by_id[grant.id] = grant
        self._persist(grant)

    def get(self, grant_id: UUID) -> OverrideGrant | None:
        return self._by_id.get(grant_id)

    def update(self, grant: OverrideGrant) -> None:
        if grant.id in self._by_id:
            self._by_id[grant.id] = grant
            self._persist(grant)

    def list_all(self) -> list[OverrideGrant]:
        return list(self._by_id.values())

    def find_active(
        self,
        *,
        session_id: UUID,
        action_kind: CapabilityKind | str,
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
            expiry_seconds=int(item.get("expiry_seconds", OVERRIDE_EXPIRY_DEFAULT_SECONDS)),
            friction_level=(
                FrictionLevel(str(item["friction_level"])) if "friction_level" in item else None
            ),
        )
        by_floor[floor] = entry
    return OverridePolicies(by_floor=by_floor)
