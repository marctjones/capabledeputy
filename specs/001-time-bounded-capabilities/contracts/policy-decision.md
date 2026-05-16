# Contract: Policy Decision with Expiry

This project's externally observable contract for this feature is the
**deterministic `decide()` verdict** and its serialized form in tool
outcomes / audit events. No network API; the contract is the decision
function's behavior and the audit record shape.

## decide() behavioral contract

Input: `(label_set, capability_set, action, used_kinds, now)` where
`now` is tz-aware UTC, defaulting to the real current time.

Guarantees:

1. **C1 — Non-expiring parity**: For a capability with
   `expires_at is None`, the decision and rule are byte-identical to
   today's behavior. (SC-002)
2. **C2 — Future deadline transparency**: For a capability with
   `now < expires_at`, the decision and rule are identical to the
   same capability with `expires_at is None`. (SC-002, FR-002 inverse)
3. **C3 — Expired is non-matching**: For a capability with
   `now >= expires_at`, the decision is computed as if that capability
   were absent from `capability_set`. (FR-002)
4. **C4 — Attribution**: If, after removing time-invalid capabilities,
   no capability satisfies the action **and** at least one removed
   capability would have matched on scope, the decision is
   `deny` with `rule = "capability-expired"`. If no capability would
   have matched even ignoring expiry, the existing generic
   no-capability denial is returned unchanged. (FR-003, SC-005)
5. **C5 — Sibling survival**: If a non-expired capability matches the
   action, the action is decided on that capability regardless of any
   expired siblings. (FR-005)
6. **C6 — Determinism / LLM isolation**: The verdict is a pure
   function of the inputs. No language-model output is consulted.
   With the policy-introspection tool absent entirely, every C1–C5
   guarantee is unchanged. (FR-004, SC-006)
7. **C7 — Composition**: Expiry is one of four independent
   disqualifiers (scope, one-shot-consumed, revoked-by-use, expired);
   a capability is usable iff none apply. No disqualifier overrides
   another. (FR-011)

## Audit record contract

Each expiry-affected decision emits the existing `POLICY_DECIDED`
audit event with:

- `decision`: `allow | deny | require_approval`
- `rule`: `"capability-expired"` for C4 denials (else unchanged)
- `reason`: human string naming the expired deadline
- enough detail to reconstruct, from the audit trail alone, that the
  action was denied because a matching capability's deadline had
  passed at decision time (SC-005)

## CLI display contract (FR-009)

`/status`, `/caps`, and the bottom toolbar render a time-bounded
capability as time-bounded with either remaining window or
`expired`; a non-expiring capability renders exactly as today (no
annotation). Display is read-only and never affects C1–C7.
