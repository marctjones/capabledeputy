# Quickstart: Capability Delegation Chains

Integration scenario proving the feature end-to-end (becomes
`tests/test_delegation_e2e.py`). Deterministic, no real LLM.

## Scenario

1. **Root**: session A holds `Capability(kind=SEND_EMAIL,
   pattern="mail/*", max_amount=100, expires_at=T, rate=5/h)`.
2. **Attenuate (US1)**: A spawns child B and delegates
   `request(kind=SEND_EMAIL, pattern="mail/team/*", max_amount=40,
   ttl=T−1h, rate=2/h)`. Expect `granted`; B holds exactly the
   clamped capability with `parent_audit_id = A_cap.audit_id`,
   `depth = 1`. Audit: `delegation.granted`.
3. **Reject broadening (US1)**: from A, delegate
   `request(max_amount=250)` → `refused` reason `amount-widened`;
   delegate `pattern="mail/**"` → `pattern-not-subset`; delegate
   `allows_destructive` when A's is False → `destructive-widened`;
   delegate `kind=QUEUE_PURCHASE` (A lacks) → `kind-not-held`. No
   capability appears on any child.
4. **Chain (US1/US3)**: B delegates a further-narrowed cap to
   grandchild C (`depth = 2`). With `CAPDEP_MAX_DELEGATION_DEPTH=2`, a
   delegation from C is `refused` `depth-exceeded` even though the
   request is a valid attenuation.
5. **Cascade — revoke (US2)**: `capability.revoke(A, A_cap.audit_id)`.
   Next `decide()` in B and C on the descendant capability →
   **deny**, reason `capability-cascaded` attributing to A's cap. A
   pending approval in C authorized by the descendant can no longer be
   approved into ALLOW. Audit: `capability.cascade_revoked` with the
   affected descendant audit_ids + sessions.
6. **Cascade — expire/rate (US2)**: re-run with A's cap expiring / its
   rate exhausted instead of explicit revoke → identical descendant
   denial (the child cannot outlive or out-spend the ancestor).
6a. **Pooled rate (FR-015)**: A's cap allows 3 uses/hour; A makes 0
   calls, B (delegated, ≤3/h) makes 3 granted calls. Assert B's 4th
   call is **denied** because A's *pooled* window is now full —
   B never circumvents A's ceiling though B's own window allowed more.
6b. **Inherit-restrictive (FR-016)**: A's cap has `revoked_by={X}` and
   `expiry=session`. Delegate to B with no overrides → B's cap has
   `revoked_by ⊇ {X}`, `expiry=one_shot` (default, ≤ parent),
   `origin=DELEGATED`. A request to set B `revoked_by={}` or
   `expiry=persistent` is **refused** (`revoked-by-narrowed` /
   `lifetime-extended`).
7. **No retro-unwind (FR-009)**: a tool call already past the
   chokepoint before the revoke completes is not reversed; only
   subsequent decisions and pending approvals are affected.
8. **LLM-isolation (SC-006)**: the planner supplies a hand-crafted
   widened `Capability` in the request; assert the engine ignores it
   and B holds only the engine-derived attenuated cap.
9. **Determinism (SC-007)**: repeat steps 2–5; assert identical
   decisions and identical audit record content.

## Success = all of

- SC-001 every broadening refused; SC-002 every descendant inert on
  cascade; SC-003 pending approvals invalidated; SC-004 no over-depth
  chain; SC-005 one audit record per delegation/cascade; SC-006 no
  model-authored capability honored; SC-007 byte-identical on repeat.
- FR-015 pooled rate (step 6a) and FR-016 inherit-restrictive (6b)
  hold — covered under SC-001/SC-002/SC-007 by construction.
- Full suite + linter green; pyright clean (Constitution III).

## Try it (once implemented)

```
capdep daemon start
capdep session new --intent "root"          # A
capdep session new --parent A --intent "child"   # B
capdep session delegate A B --kind SEND_EMAIL --pattern "mail/team/*" --max-amount 40
capdep capability revoke A <A_cap_audit_id>
capdep trace B      # shows capability-cascaded denial
```
