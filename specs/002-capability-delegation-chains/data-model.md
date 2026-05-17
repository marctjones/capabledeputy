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
- **Non-enumerated fields (FR-016, D9)**: `revoked_by` ⊇ parent's
  (request may add kill-conditions, never remove); `expiry` lifetime
  clamped on `one_shot < session < persistent`, default `one_shot`;
  `origin == CapabilityOrigin.DELEGATED`. No non-enumerated field may
  be less restrictive than the parent.
- These hold **by construction** — the engine builds the child, never
  trusts a supplied one (Constitution II).

`CapabilityOrigin` gains a new value `DELEGATED` (alongside
`SYSTEM_DEFAULT`/`USER_APPROVED`/`PATTERN_RULE`); string-serialized,
default-tolerant on read.

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
  same test (the cascade — D1) — including an ancestor whose rate
  window is full of *pooled* uses (FR-015 / D8: descendant uses were
  fanned into the ancestor's log, so the existing `is_rate_exceeded`
  read already reflects them).

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

## Pooled Rate Accounting (FR-015, D8)

Reuses the v0.7 per-session `cap_uses: dict[audit_id → (timestamps…)]`
— no new structure. On a **granted** dispatch of capability `C`,
`record_cap_use` appends the timestamp under `C.audit_id` **and** under
every ancestor `audit_id` (walk `parent_audit_id` upward, O(depth)),
each into the session holding that capability. `is_rate_exceeded` is
unchanged; because descendant uses were fanned into the ancestor's log,
an ancestor's window reflects pooled usage and the `inert()` walk
disqualifies the subtree once any ancestor window is full. A
non-delegated capability is the degenerate single-node chain (identical
to today's behavior). No DDL — `cap_uses` already serialized in v0.7.

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
