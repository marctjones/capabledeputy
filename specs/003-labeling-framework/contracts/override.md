# Contract: Override Policy / Authorization / Grant (003)

Distinct mechanism from ordinary approval (FR-038). Crosses a hard floor (`prohibited`, admissibility-exclusion, max-tier-clearance, integrity-floor); operator-configured policy governs whether and by whom (FR-036).

## Types

```python
class OverridePolicy(StrEnum):
    DISALLOWED        = "disallowed"
    SINGLE_AUTHORIZED = "single-authorized"
    DUAL_CONTROL      = "dual-control"

class HardFloor(StrEnum):
    PROHIBITED            = "prohibited"
    ADMISSIBILITY_EXCL    = "admissibility-exclusion"
    MAX_TIER_CLEARANCE    = "max-tier-clearance"
    INTEGRITY_FLOOR       = "integrity-floor"

class FrictionLevel(StrEnum):
    LOW     = "low"
    MEDIUM  = "medium"
    MAXIMAL = "maximal"   # required for `prohibited` and other gravest cells

@dataclass(frozen=True)
class OverridePolicyEntry:
    floor: HardFloor
    policy: OverridePolicy
    authorized_principal_ids: frozenset[str]
    attester_principal_ids: frozenset[str]   # required if DUAL_CONTROL
    expiry_seconds: int                       # friction-scaled

@dataclass(frozen=True)
class OverrideGrant:
    id: UUID
    session_id: UUID
    action_kind: str
    target: str
    target_category_tier: tuple[str, str]
    hard_floor_crossed: HardFloor
    invoker_principal: str
    attester_principal: str | None             # required if DUAL_CONTROL
    override_policy_at_grant: OverridePolicyEntry
    friction_level: FrictionLevel
    audit_id: UUID
    expires_at: datetime
    consumed_at: datetime | None
```

## Lifecycle (FSM)

```
[Decision returns OverrideRequired{floor, policy}]
   policy == DISALLOWED:
       → REFUSED (no grant created; audit `override.refused: policy=disallowed`)
       → action is left for the human to perform manually outside the system
   policy == SINGLE_AUTHORIZED:
       request_override(invoker, action, target, floor)
         invoker ∈ authorized_principal_ids?
           no  → REFUSED (audit `override.refused: unauthorized`)
           yes → confirm_friction(invoker, friction_level)   # maximal for PROHIBITED: typed acknowledgement of the specific irreversible effect
             confirmed?
               no  → REFUSED (audit `override.refused: friction_not_met`)
               yes → GRANT issued; capability minted with origin=override_granted, expires_at = now + expiry_seconds
                     audit `override.granted` with non-suppressible long-retention flag
   policy == DUAL_CONTROL:
       request_override(invoker, ...) → grant pending attestation
       attest(attester, grant_id)
         attester ∈ attester_principal_ids AND attester != invoker?
           no  → REFUSED (audit `override.attestation_refused`)
           yes → attester sees engine-authored verbatim effect+risk facts (NOT model prose)
                 attester confirms?
                   no  → REFUSED (audit `override.attestation_refused`)
                   yes → GRANT issued (audit `override.attested` + `override.granted`)
       expiry before attestation → EXPIRED (audit `override.expired`)
   use_grant(grant_id, action):
       valid (not expired, not consumed, action matches)?
         yes → execute; capability marked consumed_at; cascade-revoke not applicable (grants are non-inheritable and one-shot)
         no  → REFUSED (audit `override.use_refused`)
```

## Invariants (Principle V, VI, FR-031, FR-038)

1. **Distinct from approval.** `OverrideRequired` is a distinct outcome (not `require-approval`); the resulting capability carries `origin=override_granted` (not `user_approved`); approval-store and override-grant-store are separate (FR-038).
2. **AI-isolated.** No model output may invoke or attest. The CLI/UI invokes; the dispatcher serves; the planner is structurally excluded (Principle I, FR-031).
3. **Non-inheritable.** A child session via `fork`/`delegate` does NOT inherit an Override Grant's capability; the cap is session-bound (extends 002 attenuation semantics).
4. **Auto-expiry.** Every grant has a non-null `expires_at`; expiry is checked at every `decide()` call (matches the existing capability expiry path).
5. **Loudness.** Maximal-friction grants set `non_suppressible=true` on their audit event; the event survives normal log rotation (long-retention).
6. **Engine-authored review for `dual-control`.** The attester sees the verbatim effect + risk facts produced by `decide()`, not any LLM-generated framing (Principle V, trust-model §5).

## CI invariant tests required

- `test_disallowed_policy_refuses_authorized_invoker`: `disallowed` rejects even an authorized human.
- `test_dual_control_requires_distinct_attester`: invoker == attester → refused.
- `test_unauthorized_principal_refused_and_audited`.
- `test_override_grant_origin_distinct`: grant-derived capability has `origin == override_granted`, never `user_approved`.
- `test_override_audit_non_suppressible_at_maximal`: maximal-friction grants produce non-suppressible audit entries.
- `test_override_not_invocable_by_ai`: no path from a model output reaches `request_override`/`attest`.
- `test_override_grant_non_inheritable`: forking from a session with an outstanding grant does NOT copy the grant.
