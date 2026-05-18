# Phase 0 Research: Capability Delegation Chains

The spec carries no `[NEEDS CLARIFICATION]` markers (open choices were
resolved as documented fail-closed assumptions). This records the
load-bearing design decisions with rationale and rejected alternatives.

## D1 — Cascade evaluation: compute-at-decide, not eager mutation

**Decision**: A delegated capability is inert iff *any* ancestor on
its single-parent provenance chain is currently disqualified
(expired / rate-exhausted / explicitly revoked). This is evaluated by
an O(depth) provenance walk **inside the existing `decide()`**, at
decision time — no eager sweep over the session graph, no background
job, no mutation of descendant records when an ancestor dies.

**Rationale**: Constitution I (deterministic pure function) and VII
(small owned TCB). The v0.7 expiry/rate constraints already work this
way — disqualification is *computed* at `decide()` against the clock /
use-log, not eagerly written. "At the same logical instant" (FR-007)
is satisfied because the very next decision in any live session sees
the dead ancestor. No new state machine; cascade is a property, not an
event-processing pipeline.

**Alternatives rejected**:
- *Eager cascade sweep* (walk the graph on revoke, mutate every
  descendant): non-deterministic ordering, mutates shared state across
  sessions, races with in-flight decisions, and adds a state machine
  the constitution’s "one chokepoint / pure function" model forbids.
- *Materialized descendant index*: premature; depth is bounded (D5),
  per-session cap sets are tiny — the walk is cheaper than maintaining
  an index and far easier to audit.

## D2 — Provenance representation: single-parent audit_id link

**Decision**: `Capability` gains `parent_audit_id: UUID | None` and
`depth: int` (root = 0). Provenance is a **tree** (each delegated
capability has exactly one parent), keyed by the existing stable
`Capability.audit_id`. Serialized into the existing capability JSON;
absent → not delegated (`parent_audit_id=None, depth=0`).

**Rationale**: `audit_id` already exists and is stable across
serialization, so it is the natural provenance key — no new identity
concept. Single-parent (tree, not DAG) makes cascade a unique upward
walk and the audit unambiguous (spec Assumption: single-parent
provenance; Constitution VII secure-by-reduction).

**Alternatives rejected**: DAG of authority (multiple parents) —
multiplies cascade and audit complexity for a use case
(diamond provenance) the spec explicitly scopes out.

## D3 — Explicit revocation primitive

**Decision**: Add a per-session **revoked audit_id set** (default
empty, serialized; `graph.revoke(audit_id)` adds to it). `decide()`
treats a capability as disqualified if its own or any ancestor's
`audit_id` is in the revoking session's revoked-set (or the ancestor
is expired/rate-exhausted). Revocation is a control-plane operation
(operator/RPC), never model-reachable (Principle IV).

**Rationale**: Today revocation only exists implicitly via
`revoked_by` prior-use, expiry, rate. Cascade needs a first-class
"this capability is dead now" signal. A small explicit set consulted
in the pure `decide()` keeps it deterministic and additive; no DDL
(default-tolerant JSON).

**Alternatives rejected**: tombstone records / capability lifecycle
table — heavier persistence, migration, and a second source of truth
versus a set checked in the existing pure function.

## D4 — Conservative, decidable pattern-subset test

**Decision**: `pattern_is_subset(child, parent)` returns True only for
**provable** containment: exact equality; or `parent` ends in a single
trailing `*`/`**` and `child` has that same prefix and is strictly
more specific (additional literal path segments, no new wildcard
broadening). Anything not provably ⊆ → **refuse** (FR-004).

**Rationale**: General glob⊆glob is undecidable; Constitution VI
mandates fail-closed. A small decidable approximation that errs toward
refusal is correct-by-construction; over-permissive matching would be
a silent authority leak.

**Alternatives rejected**: full glob-language containment (undecidable
/ unbounded), regex translation (subtle over-acceptance) — both risk
granting broader authority than the parent.

## D5 — Bounded depth via existing config pattern

**Decision**: Max delegation depth is a configurable integer, default
**3**, read from `CAPDEP_MAX_DELEGATION_DEPTH` (env), consistent with
the existing `CAPDEP_*` daemon-config convention. Checked at delegation
time; refusal reason names the depth limit and is independent of the
capability's other dimensions (FR-006). Reconfiguring affects only new
delegations (FR-006 / spec US3-3).

**Rationale**: Matches how the daemon is already configured; no new
config mechanism. Default 3 is conservative and bounds cascade-walk
cost (D1).

## D6 — Delegation request path (LLM-isolated)

**Decision**: A new `session.delegate(parent_sid, child_sid,
request)` daemon RPC + `graph.delegate(...)`. The request carries only
*desired narrowing* (optional pattern/amount/expiry/rate). The engine
calls `derive_delegated_capability(parent_cap, request)` which returns
either the engine-constructed attenuated `Capability` or a
`DelegationRefusal(reason)`. The model may *trigger* this when
spawning a child but never authors or approves the grant; any
model-supplied capability object is ignored (FR-012, Principle I/IV).

**Rationale**: Mirrors the existing spawn/grant control-plane shape;
keeps derivation a pure function with the model strictly a requester.

**Alternatives rejected**: letting the model pass a full capability to
be "validated" — invites trust-the-model-then-check; construction-by-
clamping makes broadening unrepresentable (Constitution II).

## D7 — Schema bump 4→5 for the session field only (CORRECTED at impl)

**Decision (revised during implementation, T007/T009).** The
*capability* provenance fields (`parent_audit_id`, `depth`,
`origin=DELEGATED`) ride the `capability_set` **JSON blob** column, so
they need **no DDL** — handled by the default-tolerant
`Capability.from_dict` (001 precedent holds for these). However the
session-level **`revoked_audit_ids`** is a *top-level Session field*,
and `SessionStore` is **columnar** (one SQL column per top-level
field), not a single blob — so it requires a new column and a
migration. `SCHEMA_VERSION` is therefore bumped **4 → 5** with an
idempotent additive `ALTER TABLE ... ADD COLUMN revoked_audit_ids TEXT
NOT NULL DEFAULT '[]'` that converges any v1–v4 db to v5 in one pass
(same pattern as the v3→v4 `cap_uses` migration).

**Rationale**: The original "no bump" decision was wrong about the
*session* field — it assumed whole-Session JSON persistence; the store
is per-column. The migration is **explicitly justified and
backward-tolerant** (old rows default to an empty revoked-set), which
Constitution "Schema/state evolution" expressly permits. This
correction is recorded per Principle VIII (document the deviation when
implementation contradicts the plan).

## D8 — Pooled rate accounting via use-log fan-out (FR-015)

**Decision**: On a *granted* dispatch of a delegated capability `C`,
`record_cap_use` appends the timestamp to the use log of `C.audit_id`
**and** of every ancestor `audit_id` (walk `parent_audit_id` upward,
O(depth)), each written into the session that holds that capability.
`is_rate_exceeded` is unchanged — at `decide()`, a capability is
rate-disqualified if its own window OR (via the `inert()` provenance
walk) any ancestor's window has reached its limit. Because the fan-out
already deposited descendant uses into the ancestor's log, the
ancestor's window naturally reflects pooled usage. US2-4 ("a child
cannot circumvent the parent's rate ceiling") is therefore true **by
construction**, not by a separate guard.

**Rationale**: Keeps the rate check a pure read of an existing
per-capability log (v0.7 `cap_uses`), so cascade stays compute-at-
decide and deterministic (Constitution I, II). Writes are O(depth) on
the granted path only; reads unchanged. No new store, no new column —
the v0.7 `cap_uses` map is reused (a non-delegated cap is the
degenerate single-node chain).

**Ended ancestor (fail-closed).** If an ancestor session is no longer
live, its capability is absent from the live graph: the descendant is
already `inert` by the cascade rule (you cannot inherit authority whose
session is gone — consistent with FR-013 "no delegation from dead
authority"). Therefore the fan-out writes only to *live* ancestor
sessions, and a missing/ended ancestor short-circuits the descendant to
inert at the next `decide()`. No persistence of an ended ancestor's
window is required, and "ancestor absent" is never read as "ancestor
not rate-limited" (that would be fail-open). Fail-closed,
Constitution VI.

**Alternatives rejected**:
- *Read-aggregate (downward)*: ancestor rate check sums uses across its
  whole subtree at decision time — needs a child→descendant index or a
  full graph scan per decision; heavier, and inverts the established
  upward-only provenance walk.
- *Separate chain use-log store keyed by root*: a second source of
  truth for usage; migration + reconciliation risk versus reusing the
  existing serialized `cap_uses`.

## D9 — Inherit-restrictive non-enumerated fields (FR-016)

**Decision**: `derive_delegated_capability` also fixes the fields the
six-dimension clamp does not cover: `revoked_by` = parent's set ∪ any
request additions (never a subset — a request MAY add prior-use kill
conditions, MUST NOT remove); `expiry` lifetime clamped on the total
order `one_shot < session < persistent`, defaulting to `one_shot`
(never longer-lived than parent); `origin` set by the engine to a new
`CapabilityOrigin.DELEGATED`. No non-enumerated field may yield a
less-restrictive child.

**Rationale**: Constitution VI (fail-closed) and II (by construction):
a permissive default on an unenumerated field (e.g. empty `revoked_by`,
`persistent` lifetime) would silently *widen* authority — exactly the
hole the attenuation guarantee must not have. `DELEGATED` origin keeps
the audit trail able to distinguish delegated grants (Security &
Architecture Constraint on distinguishing reasons).

**Alternatives rejected**:
- *Attenuate only the six dimensions, reset others to engine defaults*:
  empty `revoked_by` / `session` lifetime can be broader than the
  parent → violates "never widen" (Constitution VI). Rejected.
- *Copy parent's other fields verbatim, no lifetime tightening, no
  DELEGATED marker*: safe but not maximally fail-closed and degrades
  audit attribution. Rejected in favor of clamp + marker.

**Output**: all design unknowns resolved (D1–D9); no open
clarifications. Post-clarify FR-015/FR-016 incorporated.
