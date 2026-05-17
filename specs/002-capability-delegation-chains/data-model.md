# Phase 1 Data Model: Capability Delegation Chains

Additive extensions to existing entities — no new persistence tables,
no `SCHEMA_VERSION` bump (D7).

## Capability (extended)

Existing fields unchanged (`kind`, `pattern`, `expiry`, `origin`,
`audit_id`, `max_amount`, `allows_destructive`, `revoked_by`,
`expires_at`, `rate_limit`). Added:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `parent_audit_id` | `UUID \| None` | `None` | `audit_id` of the capability this was delegated from. `None` ⇒ root (not delegated). |
| `depth` | `int` | `0` | Position in the chain; root = 0, each hop +1. |

Serialization: both go into the existing capability JSON; missing on
read ⇒ `None` / `0` (default-tolerant `from_dict`, 001 precedent).

**Invariants**
- A delegated capability (`parent_audit_id is not None`) satisfies, vs.
  its parent: same `kind`; `pattern` provably ⊆ parent (D4);
  `max_amount ≤ parent` (None treated as parent's bound, never widens);
  `expires_at ≤ parent` (None inherits parent's, never extends);
  `rate_limit` no looser (count ≤, window ≥); `allows_destructive`
  only if parent's; `depth == parent.depth + 1`.
- These hold **by construction** — the engine builds the child, never
  trusts a supplied one (Constitution II).

## DelegationRequest (new, transient — not persisted)

Caller's *desired narrowing*; every field optional, only narrows:

| Field | Type | Meaning |
|---|---|---|
| `kind` | `CapabilityKind` | Which parent capability to delegate from. |
| `pattern` | `str \| None` | Desired narrower target (must be provable subset). |
| `max_amount` | `int \| None` | Desired lower cap. |
| `ttl` / `expires_at` | duration / datetime \| None | Desired earlier deadline. |
| `rate_limit` | `RateLimit \| None` | Desired tighter limit. |

No `allows_destructive`-widening, no `audit_id`, no `depth` — those are
engine-owned. A model-supplied full `Capability` is ignored (FR-012).

## DelegationRefusal (new, transient)

`reason: str` — deterministic, machine-readable, names the violated
dimension or condition: `kind-not-held`, `pattern-not-subset`,
`amount-widened`, `expiry-extended`, `rate-loosened`,
`destructive-widened`, `parent-dead`, `depth-exceeded`,
`cycle`, `self-delegation`.

## Session (extended)

| Field | Type | Default | Meaning |
|---|---|---|---|
| `revoked_audit_ids` | `frozenset[UUID]` | `frozenset()` | Capability `audit_id`s explicitly revoked in/for this session's authority graph. Serialized; missing ⇒ empty. |

`parent: UUID | None` already exists and is the spawn edge reused for
the parent→child session relationship (no new field needed).

## Provenance Edge (derived, not stored separately)

The edge *is* `child.parent_audit_id → parent.audit_id` plus the
session spawn edge (`child_session.parent`). Cascade traversal walks
`parent_audit_id` upward (≤ `depth` hops).

## Disqualification (decision-time property, not stored)

At `decide()`, a capability `C` is **inert** iff any of:
- `C` itself expired / rate-exhausted / `audit_id ∈ revoked_audit_ids`
  (existing v0.7 checks + new revoked-set), OR
- walking `parent_audit_id` upward, **any ancestor** is inert by the
  same test (the cascade — D1).

New `decide()` denial reason: **`capability-cascaded`** (distinct from
`capability-expired` / `rate-limit-exceeded` / `…-revoked-by-prior-use`
so audit attributes *why* precisely — Security & Architecture
Constraint on distinguishing reasons).

**Pending-approval linkage (no new field).** An `ApprovalRequest`
already carries `capability_requested: Capability | None` (the
authorizing capability, which after this feature carries
`parent_audit_id`/`depth`). FR-008 cascade-invalidation is therefore
`inert(approval.capability_requested)` evaluated by the *same*
provenance walk — no new approval→capability link, schema change, or
foundational task is required. An approval whose `capability_requested`
is `inert` can no longer be approved into an ALLOW.

## Audit Records (extended `EventType`)

| Event | Emitted when | Payload |
|---|---|---|
| `delegation.granted` | engine derives + records a delegated capability | parent_audit_id, child audit_id, child session, derived dims, depth |
| `delegation.refused` | request refused | request, deterministic reason |
| `capability.cascade_revoked` | a cascade disqualifies descendants (revoke/expire/rate-exhaust of an ancestor observed at decide) | originating audit_id, trigger, affected descendant audit_ids + sessions |

## State transitions

A delegated capability has no mutable lifecycle of its own — it is
**live** while every ancestor is live, **inert** the instant any
ancestor becomes expired / rate-exhausted / revoked (evaluated at the
next decision; no write). Explicit `revoke(audit_id)` is the only new
operator transition; expiry/rate are the existing time/use-driven
transitions, inherited transitively.
