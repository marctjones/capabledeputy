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

from capabledeputy.policy.labels import LabelState


class CapabilityKind(StrEnum):
    READ_FS = "READ_FS"
    WRITE_FS = "WRITE_FS"
    SEND_EMAIL = "SEND_EMAIL"
    WEB_FETCH = "WEB_FETCH"
    CALENDAR_READ = "CALENDAR_READ"
    CALENDAR_WRITE = "CALENDAR_WRITE"
    QUEUE_PURCHASE = "QUEUE_PURCHASE"
    SEND_MESSAGE = "SEND_MESSAGE"
    BROWSER_AUTOMATION = "BROWSER_AUTOMATION"
    MACOS_AUTOMATION = "MACOS_AUTOMATION"
    BROWSER_READ = "BROWSER_READ"
    BROWSER_NAVIGATE = "BROWSER_NAVIGATE"
    BROWSER_INTERACT = "BROWSER_INTERACT"
    BROWSER_SCRIPT = "BROWSER_SCRIPT"
    BROWSER_FILE = "BROWSER_FILE"
    MACOS_APP_CONTROL = "MACOS_APP_CONTROL"
    MACOS_CLIPBOARD_READ = "MACOS_CLIPBOARD_READ"
    MACOS_CLIPBOARD_WRITE = "MACOS_CLIPBOARD_WRITE"
    MACOS_NOTIFICATION = "MACOS_NOTIFICATION"
    APPLE_MAIL_READ = "APPLE_MAIL_READ"
    APPLE_MAIL_DRAFT = "APPLE_MAIL_DRAFT"
    KEYNOTE_READ = "KEYNOTE_READ"
    KEYNOTE_PRESENT = "KEYNOTE_PRESENT"
    PAGES_READ = "PAGES_READ"
    PAGES_EDIT = "PAGES_EDIT"
    PAGES_EXPORT = "PAGES_EXPORT"
    NUMBERS_READ = "NUMBERS_READ"
    NUMBERS_EDIT = "NUMBERS_EDIT"
    NUMBERS_EXPORT = "NUMBERS_EXPORT"

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

    # 004 U034/U035 — sandboxed execution. Pattern matches the region
    # spec_id (e.g. `EXECUTE_SANDBOX scratch` allows running in the
    # `scratch` region; `EXECUTE_SANDBOX *` allows any). The policy
    # engine separately gates the effect_class on whether an actuator
    # is wired (FR-042 fail-closed).
    EXECUTE_SANDBOX = "EXECUTE_SANDBOX"

    # Persistent devbox execution. Same spec_id keying as
    # EXECUTE_SANDBOX, but addresses a long-lived container that
    # outlives a single tool call: the LLM can `devbox.start` a
    # workspace, `devbox.exec` into it across many turns, and the
    # /work volume persists. Granted independently of EXECUTE_SANDBOX
    # so operators can allow disposable one-shots without granting
    # the longer-lived surface, or vice versa.
    EXECUTE_DEVBOX = "EXECUTE_DEVBOX"

    # Granular read kinds for data sources that are NOT the local
    # filesystem (Issue #33 partial — minimum-viable for "read my
    # email by default"). Previously every read-shaped tool was
    # mapped to READ_FS regardless of whether it actually read the
    # filesystem; that was a category confusion that prevented
    # operators from granting "read Gmail without granting
    # read-local-files." These kinds let operators distinguish.
    #
    # Backward-compat: a `READ_FS *` capability still matches GMAIL_READ
    # / IMAP_READ / DRIVE_READ actions (see _READ_UNION_MATCHES below).
    # Existing /grant READ_FS * grants for legacy reasons keep working;
    # new sessions get granular caps by default.
    GMAIL_READ = "GMAIL_READ"
    GMAIL_DRAFT = "GMAIL_DRAFT"
    IMAP_READ = "IMAP_READ"
    DRIVE_READ = "DRIVE_READ"
    CHAT_READ = "CHAT_READ"
    PEOPLE_READ = "PEOPLE_READ"


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
        CapabilityKind.MACOS_CLIPBOARD_WRITE,
        CapabilityKind.PAGES_EDIT,
        CapabilityKind.NUMBERS_EDIT,
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
    # Issue #33 partial — a legacy `READ_FS` capability still satisfies
    # the granular external-read kinds. Operators with existing
    # `/grant READ_FS *` grants (or the prior default `READ_FS *`)
    # keep working. New default grants use the granular kinds.
    CapabilityKind.READ_FS: frozenset(
        {
            CapabilityKind.GMAIL_READ,
            CapabilityKind.IMAP_READ,
            CapabilityKind.DRIVE_READ,
            CapabilityKind.CHAT_READ,
            CapabilityKind.PEOPLE_READ,
            CapabilityKind.APPLE_MAIL_READ,
            CapabilityKind.KEYNOTE_READ,
            CapabilityKind.PAGES_READ,
            CapabilityKind.NUMBERS_READ,
            CapabilityKind.BROWSER_READ,
            CapabilityKind.MACOS_CLIPBOARD_READ,
        },
    ),
    CapabilityKind.BROWSER_AUTOMATION: frozenset(
        {
            CapabilityKind.BROWSER_READ,
            CapabilityKind.BROWSER_NAVIGATE,
            CapabilityKind.BROWSER_INTERACT,
            CapabilityKind.BROWSER_SCRIPT,
            CapabilityKind.BROWSER_FILE,
        },
    ),
    CapabilityKind.MACOS_AUTOMATION: frozenset(
        {
            CapabilityKind.MACOS_APP_CONTROL,
            CapabilityKind.MACOS_CLIPBOARD_READ,
            CapabilityKind.MACOS_CLIPBOARD_WRITE,
            CapabilityKind.MACOS_NOTIFICATION,
            CapabilityKind.APPLE_MAIL_READ,
            CapabilityKind.APPLE_MAIL_DRAFT,
            CapabilityKind.KEYNOTE_READ,
            CapabilityKind.KEYNOTE_PRESENT,
            CapabilityKind.PAGES_READ,
            CapabilityKind.PAGES_EDIT,
            CapabilityKind.PAGES_EXPORT,
            CapabilityKind.NUMBERS_READ,
            CapabilityKind.NUMBERS_EDIT,
            CapabilityKind.NUMBERS_EXPORT,
        },
    ),
}


# ---------------------------------------------------------------------------
# Issue #35 — Extensibility for custom kinds from servers.d/*.yaml
# ---------------------------------------------------------------------------
#
# Built-in kinds are members of the `CapabilityKind` enum. Custom kinds
# (namespaced strings like `slack:dm.send`) live in a global registry
# populated at daemon startup. The helpers below let policy code treat
# either as a "kind" via a uniform interface:
#
#     resolve_kind(name) → CapabilityKind enum OR validated str
#     is_destructive_kind(kind) → bool
#     kind_add_labels(kind) → frozenset[Label] (custom kinds only;
#         built-in label propagation lives in the policy engine)
#
# This is process-global mutable state. The daemon calls
# `register_custom_kind_registry(reg)` once at startup. Tests that need
# isolation use `reset_custom_kind_registry()` in a fixture.

_CUSTOM_KIND_REGISTRY: Any = None  # type: ignore[assignment]


class UnknownKindError(ValueError):
    """A kind name was referenced that isn't a built-in enum member
    and isn't in the custom-kind registry. Caller should report this
    as a config error or a typo in a grant request."""


def register_custom_kind_registry(registry: Any) -> None:
    """Install the CustomKindRegistry (from upstream/server_yaml.py)
    so policy code can consult it. Called once at daemon startup
    after `load_servers_d()` has populated the registry."""
    global _CUSTOM_KIND_REGISTRY
    _CUSTOM_KIND_REGISTRY = registry


def reset_custom_kind_registry() -> None:
    """Clear the global registry. Used by tests; daemon callers
    should use `register_custom_kind_registry(new_one)` instead."""
    global _CUSTOM_KIND_REGISTRY
    _CUSTOM_KIND_REGISTRY = None


def kind_name(kind: CapabilityKind | str) -> str:
    """Return the bare name of a capability kind, whether it's a
    built-in enum member or a custom-kind string. Use this anywhere
    you need the string form for audit / display / error messages."""
    if isinstance(kind, CapabilityKind):
        return kind.value
    return str(kind)


def resolve_kind(name: str) -> CapabilityKind | str:
    """Resolve a kind name to either a built-in `CapabilityKind` enum
    member or a validated custom-kind string. Raises
    `UnknownKindError` if neither.

    Used at deserialization time (Capability.from_dict, /grant CLI,
    tool registration with custom kinds). The return value's str
    equality semantics work the same for both — `CapabilityKind`
    inherits from str, so `enum_member == "READ_FS"` is True.
    """
    # Built-in first — cheap, common path
    try:
        return CapabilityKind(name)
    except ValueError:
        pass
    # Custom — only valid if a registry is installed AND it knows this name
    if _CUSTOM_KIND_REGISTRY is not None and _CUSTOM_KIND_REGISTRY.get(name) is not None:
        return name
    # Unknown — caller decides how to surface
    raise UnknownKindError(
        f"Unknown capability kind {name!r}. Built-in kinds: "
        f"{sorted(k.value for k in CapabilityKind)}. "
        f"Custom kinds must be declared in servers.d/*.yaml.",
    )


def is_destructive_kind(kind: CapabilityKind | str) -> bool:
    """True if this kind is gated by the destructive-action rule.
    Combines the hardcoded DESTRUCTIVE_KINDS set (for built-ins)
    with the registry's per-kind destructive flag (for custom)."""
    # Built-in enum case
    if isinstance(kind, CapabilityKind):
        return kind in DESTRUCTIVE_KINDS
    # Custom string — consult registry
    if _CUSTOM_KIND_REGISTRY is not None:
        return _CUSTOM_KIND_REGISTRY.is_destructive(kind)
    return False


def kind_add_tags(kind: CapabilityKind | str) -> LabelState:
    """Tags declared by a custom kind's yaml `add_tags` field.
    Built-in kinds get their tag propagation from the policy
    engine's hardcoded conflict-invariant gate (this returns empty for them).

    Returns an empty LabelState if no tags are declared.
    """
    if isinstance(kind, CapabilityKind):
        return LabelState()
    if _CUSTOM_KIND_REGISTRY is None:
        return LabelState()
    decl = _CUSTOM_KIND_REGISTRY.get(kind)
    if decl is None:
        return LabelState()
    # New style: add_tags is LabelState directly
    if hasattr(decl, "add_tags"):
        return decl.add_tags
    return LabelState()


class CapabilityExpiry(StrEnum):
    ONE_SHOT = "one_shot"
    SESSION = "session"
    PERSISTENT = "persistent"


class CapabilityOrigin(StrEnum):
    SYSTEM_DEFAULT = "system_default"
    USER_APPROVED = "user_approved"
    PATTERN_RULE = "pattern_rule"
    # A capability derived from a parent via monotonic attenuation
    # (002 delegation chains). Engine-set; keeps the audit trail able
    # to distinguish delegated grants (Constitution VIII / data-model).
    DELEGATED = "delegated"
    # 003 US6 T019/T078 — capability minted by an Override Grant
    # (crossing a hard floor under operator policy). Carries the
    # `override_grant_id` linking to the OverrideGrant record. Always
    # distinct from `user_approved` — the audit object is separate
    # too (FR-038).
    OVERRIDE_GRANTED = "override_granted"


# Default maximum delegation chain depth (002 FR-006). Configurable via
# CAPDEP_MAX_DELEGATION_DEPTH (read in daemon/lifecycle.py); root = 0.
DEFAULT_MAX_DELEGATION_DEPTH = 3


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
    # Issue #35 / #37 — `kind` accepts CapabilityKind enum OR custom-kind
    # string registered via servers.d/*.yaml. `kind_name()` and the
    # matches() machinery both handle both cases.
    kind: CapabilityKind | str
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
    # 002 delegation provenance (additive, default-tolerant). A
    # delegated capability derives from exactly one parent capability;
    # `parent_audit_id` is that parent's audit_id, `depth` its position
    # in the chain (root = 0, each hop +1). None/0 ⇒ not delegated.
    parent_audit_id: UUID | None = None
    depth: int = 0
    # 003 US6 T019 — id of the OverrideGrant that minted this
    # capability (when `origin == OVERRIDE_GRANTED`). None for all
    # other origins. Default-tolerant on read so pre-Phase-6 stores
    # still parse.
    override_grant_id: UUID | None = None

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
        kind: CapabilityKind | str,
        target: str,
        amount: int | None = None,
    ) -> bool:
        if not self.covers_kind(kind):
            return False
        if not self._pattern_matches(target):
            return False
        if self.max_amount is None:
            return True
        return amount is not None and amount <= self.max_amount

    def covers_kind(self, kind: CapabilityKind | str) -> bool:
        """Kind-only counterpart to matches().

        Used by tool-surface filtering where final target args are not known
        yet. Dispatch still calls matches() with the concrete target.
        """
        if self.kind == kind:
            return True
        # Backward-compat: WRITE_FS / CALENDAR_WRITE / READ_FS capabilities
        # match granular variants. Custom kinds participate via string
        # equality only — they never trigger the union match.
        if not isinstance(self.kind, CapabilityKind) or not isinstance(kind, CapabilityKind):
            return False
        return kind in _WRITE_UNION_MATCHES.get(self.kind, frozenset())

    def _pattern_matches(self, target: str) -> bool:
        """Glob match with one usability fix: a pattern ending in
        `/*` also matches the bare parent directory entry, not just
        its contents. Without this, granting `READ_FS /foo/*` lets
        the agent read `/foo/a.txt` but DENIES `fs.list /foo` —
        which is the prerequisite for finding `a.txt` in the first
        place. Operators consistently hit this footgun; the
        semantic intent of `/foo/*` is "the foo subtree" including
        the entry that names it.

        Examples (pattern → target):
          `/foo/*` matches `/foo`       (special case: bare parent)
          `/foo/*` matches `/foo/`      (fnmatch: trailing slash)
          `/foo/*` matches `/foo/bar`   (fnmatch: child)
          `/foo/*` matches `/foo/bar/x` (fnmatch: deeper child)
          `*`      matches anything     (fnmatch: catch-all unchanged)
          `/foo`   matches `/foo`       (fnmatch: exact, unchanged)
        """
        if fnmatch.fnmatchcase(target, self.pattern):
            return True
        # Bare-parent escape hatch — only when the pattern literally
        # ends in `/*`. Conservative: doesn't fire for `/foo/*.txt`
        # or `/foo/*/bar`, where the wildcard isn't the suffix.
        if self.pattern.endswith("/*"):
            prefix = self.pattern[:-2]
            if target == prefix:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        # Issue #35 — self.kind can be a CapabilityKind enum or a
        # custom-kind string. Both should serialize to the bare name.
        kind_str = self.kind.value if isinstance(self.kind, CapabilityKind) else str(self.kind)
        return {
            "kind": kind_str,
            "pattern": self.pattern,
            "expiry": self.expiry.value,
            "origin": self.origin.value,
            "audit_id": str(self.audit_id),
            "max_amount": self.max_amount,
            "allows_destructive": self.allows_destructive,
            "revoked_by": sorted(k.value for k in self.revoked_by),
            "expires_at": (self.expires_at.isoformat() if self.expires_at is not None else None),
            "rate_limit": (self.rate_limit.to_dict() if self.rate_limit is not None else None),
            "parent_audit_id": (str(self.parent_audit_id) if self.parent_audit_id else None),
            "depth": self.depth,
            "override_grant_id": (
                str(self.override_grant_id) if self.override_grant_id is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        # Issue #35 — kind can be a built-in enum member OR a custom
        # namespaced string registered via servers.d/*.yaml.
        # `resolve_kind` returns either; both compare correctly to str.
        return cls(
            kind=resolve_kind(d["kind"]),  # type: ignore[arg-type]
            pattern=d["pattern"],
            expiry=CapabilityExpiry(d["expiry"]),
            origin=CapabilityOrigin(d["origin"]),
            audit_id=UUID(d["audit_id"]),
            max_amount=d.get("max_amount"),
            allows_destructive=bool(d.get("allows_destructive", False)),
            # Revoked-by stays enum-only for now — only built-ins can
            # transitively revoke. If custom-kind revocation rules
            # need this, extend the enum-coercion accordingly.
            revoked_by=frozenset(CapabilityKind(k) for k in d.get("revoked_by", ())),
            expires_at=(datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None),
            rate_limit=(RateLimit.from_dict(d["rate_limit"]) if d.get("rate_limit") else None),
            parent_audit_id=(UUID(d["parent_audit_id"]) if d.get("parent_audit_id") else None),
            depth=int(d.get("depth", 0)),
            override_grant_id=(
                UUID(d["override_grant_id"]) if d.get("override_grant_id") else None
            ),
        )


class DelegationRefusalReason(StrEnum):
    """Deterministic, machine-readable refusal reasons (002 data-model /
    contracts C1). One per violated dimension/condition."""

    KIND_NOT_HELD = "kind-not-held"
    PATTERN_NOT_SUBSET = "pattern-not-subset"
    AMOUNT_WIDENED = "amount-widened"
    EXPIRY_EXTENDED = "expiry-extended"
    RATE_LOOSENED = "rate-loosened"
    DESTRUCTIVE_WIDENED = "destructive-widened"
    REVOKED_BY_NARROWED = "revoked-by-narrowed"
    LIFETIME_EXTENDED = "lifetime-extended"
    PARENT_DEAD = "parent-dead"
    DEPTH_EXCEEDED = "depth-exceeded"
    CYCLE = "cycle"
    SELF_DELEGATION = "self-delegation"
    # 003 US3 T059 — delegation would introduce a data category that
    # the child session's purpose does not admit (FR-009 structural).
    INADMISSIBLE_CATEGORY = "inadmissible-category"


@dataclass(frozen=True)
class DelegationRequest:
    """Caller's *desired narrowing* (002 data-model). Transient — not
    persisted. Every field except `kind` is optional and may only
    narrow; the engine clamps and constructs the child capability. A
    model-supplied full Capability is ignored (FR-012)."""

    kind: CapabilityKind
    pattern: str | None = None
    max_amount: int | None = None
    expires_at: datetime | None = None
    rate_limit: RateLimit | None = None
    expiry: CapabilityExpiry | None = None
    # FR-016: a request MAY add prior-use kill conditions, never remove.
    add_revoked_by: frozenset[CapabilityKind] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DelegationRefusal:
    """Returned (instead of a Capability) when a delegation request
    cannot be satisfied. Deterministic for identical inputs (SC-007)."""

    reason: DelegationRefusalReason


_GLOB_CHARS = ("*", "?", "[", "]")


def _has_glob(s: str) -> bool:
    return any(c in s for c in _GLOB_CHARS)


def pattern_is_subset(child: str, parent: str) -> bool:
    """Conservative, decidable subset test (002 research D4, FR-004).

    Returns True ONLY when every target the `child` pattern matches is
    provably also matched by `parent`. General glob⊆glob containment is
    undecidable, so this is a deliberate under-approximation that errs
    toward False (fail-closed, Constitution VI) — an unprovable case is
    refused, never granted.

    Accepted:
      - exact equality (`child == parent`);
      - single-trailing-`*` parent (`pre + "*"`, `pre` glob-free) where
        `child` starts with `pre`, contains no `**`, and any glob in
        `child` is at most a single trailing `*`.
    Everything else (internal wildcards, `?`/`[]`, `**`, multiple `*`)
    → False.
    """
    if child == parent:
        return True
    if "**" in child:
        return False
    if not (parent.endswith("*") and not _has_glob(parent[:-1])):
        return False
    pre = parent[:-1]
    if not child.startswith(pre):
        return False
    body = child[:-1] if child.endswith("*") else child
    # `body` must be a concrete narrowing — no remaining glob chars, and
    # no `*` other than the one optional trailing star already stripped.
    return not _has_glob(body)


_EXPIRY_RANK: dict[CapabilityExpiry, int] = {
    CapabilityExpiry.ONE_SHOT: 0,
    CapabilityExpiry.SESSION: 1,
    CapabilityExpiry.PERSISTENT: 2,
}


def derive_delegated_capability(
    parent: Capability,
    request: DelegationRequest,
    *,
    depth_limit: int,
) -> Capability | DelegationRefusal:
    """Pure, deterministic derivation of an attenuated child capability
    (002 contracts C1; FR-002/003/004/005/006/016). The engine
    *constructs* the child by clamping every dimension to `parent`;
    any widening request is refused with a deterministic reason rather
    than silently clamped (the caller learns). No LLM input.

    Session-context preconditions (kind-not-held, parent-inert,
    cycle/self) are enforced by `SessionGraph.delegate`; this function
    owns the depth bound + the per-dimension + non-enumerated clamps.
    A fresh `audit_id` is minted (each delegated cap has its own
    identity); all other fields are a deterministic function of
    `(parent, request, depth_limit)`.
    """
    if request.kind != parent.kind:
        return DelegationRefusal(DelegationRefusalReason.KIND_NOT_HELD)
    if parent.depth + 1 > depth_limit:
        return DelegationRefusal(DelegationRefusalReason.DEPTH_EXCEEDED)

    if request.pattern is None:
        pattern = parent.pattern
    elif pattern_is_subset(request.pattern, parent.pattern):
        pattern = request.pattern
    else:
        return DelegationRefusal(DelegationRefusalReason.PATTERN_NOT_SUBSET)

    if request.max_amount is None:
        max_amount = parent.max_amount
    elif parent.max_amount is not None and request.max_amount > parent.max_amount:
        return DelegationRefusal(DelegationRefusalReason.AMOUNT_WIDENED)
    else:
        max_amount = request.max_amount

    if request.expires_at is None:
        expires_at = parent.expires_at
    elif parent.expires_at is not None and request.expires_at > parent.expires_at:
        return DelegationRefusal(DelegationRefusalReason.EXPIRY_EXTENDED)
    else:
        expires_at = request.expires_at

    if request.rate_limit is None:
        rate_limit = parent.rate_limit
    else:
        p, r = parent.rate_limit, request.rate_limit
        if p is not None and (r.max_uses > p.max_uses or r.window_seconds < p.window_seconds):
            return DelegationRefusal(DelegationRefusalReason.RATE_LOOSENED)
        rate_limit = r

    # FR-016: revoked_by is a superset of the parent's (request may add
    # kill-conditions, never remove — the request shape is add-only, so
    # narrowing is unrepresentable by construction).
    revoked_by = parent.revoked_by | request.add_revoked_by

    # FR-016: expiry lifetime clamped on one_shot<session<persistent,
    # default one_shot (most restrictive) when unspecified.
    requested_expiry = request.expiry if request.expiry is not None else CapabilityExpiry.ONE_SHOT
    if _EXPIRY_RANK[requested_expiry] > _EXPIRY_RANK[parent.expiry]:
        return DelegationRefusal(DelegationRefusalReason.LIFETIME_EXTENDED)

    return Capability(
        kind=parent.kind,
        pattern=pattern,
        expiry=requested_expiry,
        origin=CapabilityOrigin.DELEGATED,
        audit_id=uuid4(),
        max_amount=max_amount,
        # Destructive authority is inherited, never widened: the request
        # cannot carry allows_destructive (engine-owned).
        allows_destructive=parent.allows_destructive,
        revoked_by=revoked_by,
        expires_at=expires_at,
        rate_limit=rate_limit,
        parent_audit_id=parent.audit_id,
        depth=parent.depth + 1,
    )
