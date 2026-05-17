# Contract: Delegation Derivation & Cascade

Two pure contracts plus one control-plane RPC. All derivation/cascade
logic is deterministic and LLM-isolated (Constitution I).

## C1 — `derive_delegated_capability(parent, request, *, depth_limit) -> Capability | DelegationRefusal`

Pure function. Constructs the child capability by **clamping**, never
by trusting `request`.

**Preconditions checked, in order (first failure wins, deterministic):**

1. Parent session holds a live capability of `request.kind` →
   else `DelegationRefusal("kind-not-held")`.
2. Parent capability is not itself inert (expired / rate-exhausted /
   revoked / cascaded) → else `DelegationRefusal("parent-dead")`.
3. `parent.depth + 1 ≤ depth_limit` → else
   `DelegationRefusal("depth-exceeded")`.
4. Not a cycle / self-delegation (child session ≠ parent session and
   not an ancestor) → else `DelegationRefusal("cycle"|"self-delegation")`.

**Derivation (each dimension clamped; widening request → refusal, not
silent clamp, so the caller learns):**

| Dimension | Rule | Refusal reason if widened |
|---|---|---|
| kind | identical to parent | (covered by 1) |
| pattern | `request.pattern` if `pattern_is_subset(req, parent)` proven; absent ⇒ inherit parent | `pattern-not-subset` |
| max_amount | `min(request, parent)`; request > parent | `amount-widened` |
| expires_at | `min(request, parent)`; request later than parent | `expiry-extended` |
| rate_limit | tighter only (count ≤ parent, window ≥ parent) | `rate-loosened` |
| allows_destructive | child True only if parent True | `destructive-widened` |
| parent_audit_id | set to `parent.audit_id` (engine-owned) | — |
| depth | `parent.depth + 1` (engine-owned) | — |

Output capability gets a fresh `audit_id`. **Determinism**: identical
`(parent, request, depth_limit)` ⇒ identical capability bytes and
identical refusal reason (SC-007).

## C2 — Cascade guard inside `decide()`

Augments the existing decision. A matched capability `C` is treated as
**non-matching** (action denied with reason `capability-cascaded`,
attributed to the originating ancestor) iff `inert(C)`:

```
inert(C) =
  C.expired(now) or C.rate_exhausted(now, uses)
  or C.audit_id in session.revoked_audit_ids
  or (C.parent_audit_id is not None and inert(parent_of(C)))
```

- O(depth), depth ≤ configured max.
- Composes with all v0.7 constraints; no constraint overrides another
  (Security & Architecture Constraint).
- A pending approval is invalidated iff
  `inert(approval.capability_requested)` — the existing
  `ApprovalRequest.capability_requested` field is the authorizing
  capability (no new linkage/field). Such an approval can no longer be
  approved into an ALLOW (FR-008); emits `capability.cascade_revoked`.
- Already-dispatched calls are **not** unwound (FR-009).

## C3 — `session.delegate` RPC (control-plane, operator/agent-trigger)

`session.delegate(parent_session_id, child_session_id, request)` →
`{granted: true, capability: {...}}` or
`{granted: false, reason: "<deterministic>"}`.

- Registers the derived capability into the child session; records
  provenance; emits `delegation.granted` / `delegation.refused`.
- The model may *trigger* this when spawning a child but supplies only
  the narrowing `request`; it never authors, widens, or approves the
  grant. Any model-supplied full capability is ignored (FR-012).
- `capability.revoke(session_id, audit_id)` is the paired operator RPC
  that adds to `revoked_audit_ids` (drives C2 cascade).

## Invariant tests (Constitution III)

- Per-dimension attenuation matrix: equal / each-narrower / each-wider
  → exactly the clamped cap or the right refusal (SC-001).
- Chain: N attenuating hops succeed; N+1 → `depth-exceeded` (SC-004).
- Cascade: revoke / expire / rate-exhaust ancestor ⇒ every descendant
  denied next decision, pending approvals invalidated, audited
  (SC-002/003/005).
- LLM-isolation: a model-supplied widened capability is ignored;
  engine-derived cap is the only one in effect (SC-006).
- Determinism: repeated runs ⇒ identical decisions + audit (SC-007).
