# Phase 1 Data Model: Time-Bounded Capabilities

## Entity: Capability (extended)

Existing unforgeable, session-held authorization. **One new optional
attribute**; all existing attributes unchanged.

| Attribute | Type | New? | Notes |
|-----------|------|------|-------|
| kind | CapabilityKind | no | what action it authorizes |
| pattern | str (glob) | no | scope of the target |
| expiry | CapabilityExpiry (one_shot/session/persistent) | no | lifetime category — **unchanged; orthogonal to the new field** |
| origin | CapabilityOrigin | no | provenance |
| audit_id | UUID | no | audit linkage |
| max_amount | int \| None | no | amount ceiling |
| allows_destructive | bool | no | destructive-op gate bypass |
| revoked_by | frozenset[CapabilityKind] | no | prior-use revocation |
| **expires_at** | **datetime \| None (tz-aware UTC)** | **yes** | absolute deadline; `None` ⇒ never expires (today's behavior) |

**Naming note**: the existing `expiry` enum is a *lifetime category*
(one-shot vs. session vs. persistent), not a wall-clock deadline.
`expires_at` is the new absolute-time field and is independent of it
(a `session` capability may also carry an `expires_at`).

### Validation / invariants

- `expires_at` is `None` or a timezone-aware UTC `datetime`.
- A capability is **time-valid** at decision time `now` iff
  `expires_at is None or now < expires_at` (half-open).
- Usability (unchanged composition, FR-011): scope-match **and**
  not-one-shot-consumed **and** not-revoked-by-prior-use **and**
  time-valid.

### Serialization (FR-008)

- `to_dict`: add `"expires_at": <iso8601-utc or null>`.
- `from_dict`: `expires_at = parse(d["expires_at"]) if present else None`
  — tolerant default, so capabilities persisted before this feature
  load as non-expiring. No `SCHEMA_VERSION` change.

### Construction helper (FR-006/FR-007)

- `Capability.expiring_in(kind, pattern, ttl: timedelta, *, now=…, **rest)`
  → sets `expires_at = now + ttl`. `ttl <= 0` ⇒ `expires_at <= now`
  ⇒ already expired by the half-open rule.

## Entity: PolicyDecision (extended attribution only)

No new fields. New **value** for the existing `rule` attribute:

- `rule = "capability-expired"` when the action would have matched a
  capability that is disqualified solely because it is not time-valid,
  and no other capability satisfies the action.

Distinct from the generic no-matching-capability denial so audits can
tell "expired" from "never granted" (FR-003 / SC-005).

## Entity: Decision Clock

Not persisted. The `now` value threaded into `decide()` for a single
decision. One decision uses exactly one `now`, so expiry, and any
other time-sensitive logic, agree within that decision.

## State transitions

A capability has no stored mutable expiry state — `expires_at` is set
once at creation and never changes. The *observed* validity is a pure
function of `(expires_at, now)`:

```
created ──(now < expires_at)──▶ TIME-VALID ──(now >= expires_at)──▶ EXPIRED (terminal, inert)
            expires_at = None ─▶ TIME-VALID (forever)
```

"EXPIRED" is inert, not poisonous: an expired capability is simply
skipped; a sibling non-expired capability that matches still
satisfies the action (FR-005, User Story 1 scenario 3).
