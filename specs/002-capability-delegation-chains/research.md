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

## D7 — No schema bump

**Decision**: `SCHEMA_VERSION` stays 4. `parent_audit_id`, `depth`,
and the session revoked-set are additive JSON handled by the existing
default-tolerant `from_dict`. Old rows load as non-delegated with an
empty revoked-set.

**Rationale**: Exact precedent set by 001 (expiry attribute). Constitution
"backward-tolerant on read unless migration explicitly justified" — no
migration is justified here.

**Output**: all design unknowns resolved; no open clarifications.
