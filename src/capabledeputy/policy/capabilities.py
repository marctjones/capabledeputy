"""Capabilities held by sessions (DESIGN.md §7.3).

Capabilities are unforgeable tokens granting specific scoped actions.
The runtime — never the LLM — holds and dispatches them. Each
capability records its origin, expiry, and audit_id so every check
is traceable.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Self
from uuid import UUID, uuid4


class CapabilityKind(StrEnum):
    READ_FS = "READ_FS"
    WRITE_FS = "WRITE_FS"
    SEND_EMAIL = "SEND_EMAIL"
    WEB_FETCH = "WEB_FETCH"
    CALENDAR_READ = "CALENDAR_READ"
    CALENDAR_WRITE = "CALENDAR_WRITE"
    QUEUE_PURCHASE = "QUEUE_PURCHASE"

    # Granular destructive-op kinds (DESIGN.md §7.5 — Clark-Wilson + CRUD
    # decomposition). New tools that distinguish create / modify / delete
    # use these explicitly. Legacy WRITE_FS / CALENDAR_WRITE capabilities
    # remain valid: their matches() implementation accepts the granular
    # kinds as a backward-compat union.
    CREATE_FS = "CREATE_FS"
    MODIFY_FS = "MODIFY_FS"
    DELETE_FS = "DELETE_FS"
    CREATE_CAL = "CREATE_CAL"
    MODIFY_CAL = "MODIFY_CAL"
    DELETE_CAL = "DELETE_CAL"


# Action kinds the policy engine treats as "destructive" — modifying or
# deleting existing state. New tools opt into stricter gating by setting
# their capability_kind to one of these; the policy engine then requires
# either a `allows_destructive=True` capability or an explicit human
# approval gate before the action can fire.
DESTRUCTIVE_KINDS: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.MODIFY_FS,
        CapabilityKind.DELETE_FS,
        CapabilityKind.MODIFY_CAL,
        CapabilityKind.DELETE_CAL,
    },
)


# Backward-compat: a legacy capability of `WRITE_FS` / `CALENDAR_WRITE`
# matches actions whose kind is the granular create/modify/delete
# variant. New capabilities should be granular.
_WRITE_UNION_MATCHES: dict[CapabilityKind, frozenset[CapabilityKind]] = {
    CapabilityKind.WRITE_FS: frozenset(
        {CapabilityKind.CREATE_FS, CapabilityKind.MODIFY_FS, CapabilityKind.DELETE_FS},
    ),
    CapabilityKind.CALENDAR_WRITE: frozenset(
        {CapabilityKind.CREATE_CAL, CapabilityKind.MODIFY_CAL, CapabilityKind.DELETE_CAL},
    ),
}


class CapabilityExpiry(StrEnum):
    ONE_SHOT = "one_shot"
    SESSION = "session"
    PERSISTENT = "persistent"


class CapabilityOrigin(StrEnum):
    SYSTEM_DEFAULT = "system_default"
    USER_APPROVED = "user_approved"
    PATTERN_RULE = "pattern_rule"


@dataclass(frozen=True)
class RateLimit:
    """A sliding-window use cap: at most `max_uses` dispatches of the
    owning capability within any trailing `window_seconds`. Evaluated
    deterministically at the policy chokepoint against the session's
    recorded use timestamps — never by the LLM."""

    max_uses: int
    window_seconds: int

    def to_dict(self) -> dict[str, int]:
        return {"max_uses": self.max_uses, "window_seconds": self.window_seconds}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RateLimit:
        return cls(
            max_uses=int(d["max_uses"]),
            window_seconds=int(d["window_seconds"]),
        )


@dataclass(frozen=True)
class Capability:
    kind: CapabilityKind
    pattern: str
    expiry: CapabilityExpiry = CapabilityExpiry.SESSION
    origin: CapabilityOrigin = CapabilityOrigin.SYSTEM_DEFAULT
    audit_id: UUID = field(default_factory=uuid4)
    max_amount: int | None = None
    # When True, this capability authorises modify/delete operations
    # (the granular MODIFY_* / DELETE_* kinds) without needing a per-
    # action approval. Default OFF: destructive operations are gated by
    # default, matching the Clark-Wilson principle that modifications
    # must be deliberate, audited transactions.
    allows_destructive: bool = False
    # If any of these CapabilityKinds has already been dispatched in the
    # session, this capability is treated as revoked: the policy engine
    # returns DENY with rule "capability-revoked-by-prior-use". This is
    # the tool-identity counterpart to the label-based conflict rules —
    # use it when the prior-use signal is the tool itself rather than an
    # information-flow label (e.g. "after web.fetch, no memory.write").
    revoked_by: frozenset[CapabilityKind] = field(default_factory=frozenset)
    # Optional absolute expiry deadline (timezone-aware UTC). None ⇒
    # never expires (today's behavior). Evaluated deterministically at
    # the policy decision point against an injected clock — never by
    # the LLM. Half-open: valid while `now < expires_at`, expired at
    # `now >= expires_at`. Independent of the `expiry` lifetime enum
    # above (one-shot/session/persistent) — a session capability may
    # also carry an absolute `expires_at`.
    expires_at: datetime | None = None
    # Optional sliding-window use limit. None ⇒ unlimited (today's
    # behavior). Counted per-session-per-capability (keyed by
    # audit_id) at the policy chokepoint; independent of and composed
    # with expiry / revocation — any single disqualifier makes the
    # capability unusable.
    rate_limit: RateLimit | None = None

    def is_expired(self, now: datetime) -> bool:
        """True iff this capability carries a deadline that has been
        reached. Half-open window: expired when `now >= expires_at`."""
        return self.expires_at is not None and now >= self.expires_at

    def is_rate_exceeded(
        self,
        now: datetime,
        use_timestamps: tuple[datetime, ...],
    ) -> bool:
        """True iff using this capability now would exceed its rate
        limit: at least `max_uses` prior uses fall within the trailing
        `window_seconds` (a use counts while `now - ts < window`)."""
        if self.rate_limit is None:
            return False
        window = timedelta(seconds=self.rate_limit.window_seconds)
        in_window = sum(1 for ts in use_timestamps if now - ts < window)
        return in_window >= self.rate_limit.max_uses

    @classmethod
    def expiring_in(
        cls,
        kind: CapabilityKind,
        pattern: str,
        ttl: timedelta,
        *,
        now: datetime | None = None,
        **rest: Any,
    ) -> Self:
        """Construct a capability whose absolute deadline is `ttl`
        after `now` (default: current UTC time). A non-positive `ttl`
        yields `expires_at <= now`, so the capability is already
        expired at first use (half-open rule). The absolute deadline
        is the unit of truth; the duration is sugar resolved here."""
        base = now if now is not None else datetime.now(UTC)
        return cls(kind=kind, pattern=pattern, expires_at=base + ttl, **rest)

    def matches(
        self,
        kind: CapabilityKind,
        target: str,
        amount: int | None = None,
    ) -> bool:
        if self.kind != kind:
            # Backward-compat: WRITE_FS / CALENDAR_WRITE capabilities
            # match the granular create/modify/delete variants.
            covered = _WRITE_UNION_MATCHES.get(self.kind, frozenset())
            if kind not in covered:
                return False
        if not fnmatch.fnmatchcase(target, self.pattern):
            return False
        if self.max_amount is None:
            return True
        return amount is not None and amount <= self.max_amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "pattern": self.pattern,
            "expiry": self.expiry.value,
            "origin": self.origin.value,
            "audit_id": str(self.audit_id),
            "max_amount": self.max_amount,
            "allows_destructive": self.allows_destructive,
            "revoked_by": sorted(k.value for k in self.revoked_by),
            "expires_at": (
                self.expires_at.isoformat() if self.expires_at is not None else None
            ),
            "rate_limit": (
                self.rate_limit.to_dict() if self.rate_limit is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            kind=CapabilityKind(d["kind"]),
            pattern=d["pattern"],
            expiry=CapabilityExpiry(d["expiry"]),
            origin=CapabilityOrigin(d["origin"]),
            audit_id=UUID(d["audit_id"]),
            max_amount=d.get("max_amount"),
            allows_destructive=bool(d.get("allows_destructive", False)),
            revoked_by=frozenset(
                CapabilityKind(k) for k in d.get("revoked_by", ())
            ),
            expires_at=(
                datetime.fromisoformat(d["expires_at"])
                if d.get("expires_at")
                else None
            ),
            rate_limit=(
                RateLimit.from_dict(d["rate_limit"])
                if d.get("rate_limit")
                else None
            ),
        )
