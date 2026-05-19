# Quickstart: Labeling Framework (003) — Canonical Scenarios

**Status**: Phase 1 output. Each scenario doubles as an integration-test sketch for the implementation phase. Scenarios assume the operator has configured the bindings/policies referenced; all configuration is human-declared (FR-012).

---

## Scenario 1 — HR-folder → SharePoint deny (FR-043, FR-048, US2)

**Setup.** `configs/source_bindings.yaml`:
```yaml
- name: HR-folder
  scope: file:///home/marc/HR/**
  category: hr-private
  default_tier: restricted
  reversibility: { degree: irreversible, agent: none }   # data leaving HR is irreversible
  risk_ids: [PI-001, NIST-AI-MAP-1, GDPR-ART-5]
- name: TeamSharePoint
  scope: https://acme.sharepoint.com/sites/team/**
  category: team-internal
  default_tier: regulated
  risk_ids: [PI-010]
```

`configs/rules.yaml`:
```yaml
- deny:
    source: HR-folder
    destination: TeamSharePoint
    effect: COMMUNICATE
  reason: HR data must never reach team SharePoint
  risk_id: PI-001
```

**Expected flow.** An agent reads `~/HR/contracts/me.pdf`; ingest applies binding `HR-folder` → category `hr-private` tier `restricted` (per FR-043). The agent later attempts to write the content to `https://acme.sharepoint.com/sites/team/HR-stuff/me.pdf`. The adapter surfaces canonical destination id `https://acme.sharepoint.com/sites/team/**` (FR-048). The decision rule matches `(source=HR-folder, destination=TeamSharePoint, effect=COMMUNICATE)` → **deny**, audited with the rule's `risk_id`. **No prompt to a human**; this is a deterministic deny, not an approval.

**Test asserts**:
1. ingest-time labels on the data carry `(category=hr-private, tier=restricted)`.
2. write attempt resolves `(source_binding=HR-folder, destination_binding=TeamSharePoint)` deterministically.
3. outcome is `deny` (not `require-approval`).
4. audit event records both binding names + the rule's `risk_id`.

---

## Scenario 2 — Versioned-write reversibility (FR-037, FR-044, US6, SC-013/015)

**Setup.** A git-backed local repo is bound as `version-preserving`:
```yaml
- name: my-code
  scope: file:///home/marc/code/**
  category: proprietary_work
  default_tier: regulated
  reversibility: { degree: reversible, agent: system }
  write_discipline: version-preserving
  risk_ids: [PI-020]
```

`configs/envelopes.yaml` for `(category=proprietary_work, effect=MUTATE_LOCAL, decision_context=*, reversibility=reversible/system)`:
```yaml
- cell: { category: proprietary_work, effect: MUTATE_LOCAL,
          decision_context: any, reversibility: "reversible/system" }
  strictest: auto
  loosest: auto
```

**Expected flow.** Agent commits new code to the repo. The runtime invokes the (spec-004) `VersionedWritePort` which returns `{prior_version_handle, post_state_hash, attestation}`. The runtime *verifies* prior-version retention by reading the handle and checking the pre-write hash matches; verification succeeds → effective reversibility = `reversible/system` → optimistic execution (FR-034) → no prompt. A subsequent in-place destructive overwrite to a path with `write_discipline: in-place` (or no discipline declared) resolves to `irreversible` → gates.

**Test asserts**:
1. verification call returns `system-reversible` only when prior-version handle resolves correctly.
2. agent commit runs optimistically (no `require-approval` event).
3. attempted unverifiable overwrite resolves `irreversible` and produces `require-approval`.
4. rollback restores prior version end-to-end.

---

## Scenario 3 — `restricted`-tier session via Reference Handle (Pattern ③) (FR-047, US5)

**Setup.** A session is spawned with `purpose_handle: tax-prep`, which admits category `financial` (tier `restricted` in the operator's profile). The tool `bank.send_transactions_to_accountant` declares `accepts_handles=true` and `effect_class=COMMUNICATE`.

**Expected flow.** The agent fetches financial transactions; the runtime returns a `ReferenceHandle` (UUID), not the values. The planner orchestrates without seeing the data: "send `<handle:#42>` to my accountant." The dispatch resolves the handle to labels `(category=financial, tier=restricted)`, evaluates the rule `(financial → accountant-relationship-group, effect=COMMUNICATE)` → allow (within envelope) → runtime **substitutes the bound value at the bind site** and emits audit event `pattern3.handle_bind {handle_id=#42, dest_canonical_id=mailto:accountant@..., audit_id=...}`. The planner's `history` never contains the raw transactions.

**Test asserts**:
1. handles are issued for every `restricted`-fetch; planner context inspection shows zero raw values.
2. dispatch substitutes only after the gate passes; on deny, no substitution and no leak.
3. `pattern3.handle_bind` audit event records destination canonical id per insertion.
4. spawning a `restricted` session without any `accepts_handles=true` tool and no SandboxActuator available → spawn refused (FR-047 floor).

---

## Scenario 4 — Override Grant with `dual-control` policy (FR-032, FR-036, FR-038, US6)

**Setup.** `configs/override_policy.yaml`:
```yaml
- tier_or_floor: prohibited
  policy: dual-control
  authorized_principal_ids: [marc, partner]
  attester_principal_ids: [marc, partner, legal]
  expiry_seconds: 300   # 5-minute window (shortest)
- tier_or_floor: admissibility-exclusion
  policy: single-authorized
  authorized_principal_ids: [marc]
  expiry_seconds: 3600
```

**Expected flow.** An action resolves to tier `prohibited` (e.g., an explicit, knowingly-illegal request the operator wants the system to *help* with under their own override). `decide()` returns `OverrideRequired{floor=prohibited, policy=dual-control}`. The CLI prompts `marc` for the typed acknowledgement (maximal friction); `marc` invokes the override. The system then awaits a *distinct* second-human attestation from `partner` or `legal`. Once attested (within 5 min), the runtime issues a one-shot capability with `origin=override_granted` and `override_grant_id=...`, valid for one call, expires immediately on use. The audit event records `{invoker=marc, attester=partner, hard_floor_crossed=prohibited, friction_level=maximal}`. An attestation by `marc` himself is refused; an unauthorized principal's attempt is refused; expiry without attestation auto-aborts the grant.

**Test asserts**:
1. `OverrideRequired` is a distinct outcome (not `require-approval`).
2. `dual-control` enforces invoker ≠ attester and authorization for both.
3. `disallowed` policy refuses even an authorized invoker (operator-chosen terminal).
4. `single-authorized` admissibility-exclusion override at lower friction works for `marc` but not for an unauthorized principal.
5. issued capability has `origin=override_granted` and exactly one use.

---

## Smoke test (post-implementation)

Once US1 is built, the existing approval-demo (`capdep approval submit/list/show/approve`) MUST still pass end-to-end. The new `capdep audit storage-shape` CLI MUST return success on a freshly-migrated v6 database.
