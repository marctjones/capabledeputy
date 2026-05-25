# Data Model: Labeling Framework (003)

**Status**: Phase 1 output. Persistence shape, entities, fields, relationships, validation rules, state transitions for the v0.9 labeling framework. The shape MUST satisfy FR-045 (axes A–D as separate structured fields, not a prefixed flat set) — Principle-VIII observable.

## Persistence shape

**Schema migration**: `SCHEMA_VERSION 5 → 6` (forward-only per FR-024). On first daemon launch after upgrade, a one-time converter reads each `sessions` row's legacy `label_set` and maps it into the new axis columns at the most-restrictive position; the legacy column is retained read-only for one schema cycle for audit, then dropped at v7. Existing columns (`history`, `declassification_log`, `used_kinds`, `cap_uses`, `revoked_audit_ids`) are preserved unchanged.

### Existing `sessions` table — additions (ALTER TABLE)

| Column | Type | NOT NULL? | Notes |
|---|---|---|---|
| `axis_a` | TEXT (JSON list of `{category, tier, risk_ids[]}`) | yes | Axis A data category + resolved tier per category present in this session (FR-002/007/045). |
| `axis_b` | TEXT (JSON list of provenance levels with monotone-lattice position + integrity-floor flag) | yes | Axis B provenance/integrity (FR-004/045). Replaces the trust prefix portion of legacy `label_set`. |
| `axis_d` | TEXT (JSON object: `initiator+auth`, `counterparty/relationship_group_ids[]`, `expectedness: expected\|anomalous`, `reversibility: {degree, agent}`) | yes | Axis D decision context (FR-006/029/045). |
| `purpose_handle` | TEXT (foreign-ref into purpose registry) | yes (default `unset` → fail-closed) | Structured purpose (FR-046). Distinct from free-text `intent`. |
| `reference_handles` | TEXT (JSON map `handle_id → {bound_resource_ref, axis_a, axis_b, materialized_at[]}`) | yes (`{}`) | Pattern ③ handles (FR-047). |
| `risk_preference_at_spawn` | TEXT | yes (default snapshot from `configs/risk_preference.json`) | Frozen dial value at spawn for replayability (SC-002). |
| `effective_isolation_region_id` | TEXT NULL | no | If this session runs inside a disposable region (FR-040), the region id; null = uncontained. |

Note: Axis C (effect class) is **not** on the Session. It lives on `Capability.kind` (already present) and on `ToolDefinition.effect_class` (new). The Session merely *holds capabilities* whose effect classes drive `decide()`.

### New tables (separate from `sessions`)

#### `source_location_bindings`
| Column | Type | Notes |
|---|---|---|
| `name` | TEXT PRIMARY KEY | Operator-given identifier referenced by rules (e.g., `HR-folder`). |
| `scope_pattern_canonical` | TEXT NOT NULL | Canonical URI form (e.g., `file:///home/marc/HR/**`). |
| `category` | TEXT NOT NULL | Axis A category. |
| `default_tier` | TEXT NOT NULL | Most-restrictive default for unbound subtree positions. |
| `reversibility` | TEXT NULL (JSON `{degree, agent}`) | Optional override (FR-037). |
| `mutability` | TEXT NULL (JSON `{degree, agent}`) | Optional override (FR-039). |
| `write_discipline` | TEXT NULL (`version-preserving` \| `in-place`) | Optional (FR-044). |
| `risk_ids` | TEXT NOT NULL (JSON list) | ≥1 internal risk-register id (FR-015). |
| `assignment_provenance` | TEXT NOT NULL | (FR-022). |
| `created_at`, `updated_at` | TEXT NOT NULL | |

#### `relationship_groups`
| Column | Type | Notes |
|---|---|---|
| `group_id` | TEXT PRIMARY KEY | E.g., `project-P`, `team-A`, `spouse`. |
| `member_principal_ids` | TEXT NOT NULL (JSON list) | Human-declared, AI-read-only (FR-033). |
| `created_at`, `updated_at` | TEXT NOT NULL | |

#### `expectation_bindings`
| Column | Type | Notes |
|---|---|---|
| `binding_id` | TEXT PRIMARY KEY | |
| `initiator` | TEXT NOT NULL | (FR-029). |
| `effect_kind` | TEXT NOT NULL | |
| `time_window` | TEXT NULL | |
| `param_constraints` | TEXT NULL (JSON) | |
| `risk_ids` | TEXT NOT NULL (JSON list) | |

#### `purposes`
| Column | Type | Notes |
|---|---|---|
| `purpose_id` | TEXT PRIMARY KEY | (FR-046). |
| `label` | TEXT NOT NULL | Human-readable. |
| `admissible_categories` | TEXT NULL (JSON list) | Whitelist. |
| `inadmissible_categories` | TEXT NULL (JSON list) | Blacklist (FR-009). |
| `recommended_pattern` | TEXT NULL | Hint, not authoritative. |

#### `override_policies`
| Column | Type | Notes |
|---|---|---|
| `tier_or_floor` | TEXT PRIMARY KEY | Cell key (e.g., `prohibited`, `admissibility-exclusion`, `max-tier-clearance`, `integrity-floor`). |
| `policy` | TEXT NOT NULL | `disallowed` \| `single-authorized` \| `dual-control`. |
| `authorized_principal_ids` | TEXT NOT NULL (JSON list) | (FR-036). |
| `attester_principal_ids` | TEXT NULL (JSON list) | Required if `dual-control`. |
| `expiry_seconds` | INTEGER NOT NULL | Friction-scaled to severity. **Default 900 (15 min) per Q2 / FR-032.** **Absolute spec-enforced cap: 3600 (60 min)**; registry validation refuses any entry with `expiry_seconds > 3600`. |

#### `override_grants`
| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PRIMARY KEY | UUID. |
| `session_id` | TEXT NOT NULL | |
| `action_kind` | TEXT NOT NULL | |
| `target` | TEXT NOT NULL | |
| `target_category_tier` | TEXT NOT NULL | |
| `hard_floor_crossed` | TEXT NOT NULL | Which floor: `prohibited` \| `admissibility-exclusion` \| `max-tier-clearance` \| `integrity-floor`. |
| `invoker_principal` | TEXT NOT NULL | |
| `attester_principal` | TEXT NULL | Required for `dual-control`. |
| `override_policy_at_grant` | TEXT NOT NULL | Snapshot. |
| `friction_level` | TEXT NOT NULL | `low` \| `medium` \| `maximal`. |
| `audit_id` | TEXT NOT NULL | |
| `expires_at` | TEXT NOT NULL | |
| `consumed_at` | TEXT NULL | |

Capabilities issued from an Override Grant set `Capability.origin = override_granted` and carry an `override_grant_id` field (ALTER on the capability serialization; back-compat-tolerant on read per Constitution §Sec. Constraints).

#### `outcome_envelopes`
Stored as **config file** (`configs/envelopes.yaml`) rather than a DB table; loaded into an in-memory map at daemon startup. Cell key = `(category, effect, decision_context_canonical, reversibility)`; value = `{strictest, loosest}`.

#### `risk_register`
Stored as **config file** (`configs/risk_register.json`); loaded at startup. Entry shape (post-Q5 / FR-016 / FR-028):

```json
{
  "id": "FAIR-2",
  "summary": "Financial loss exceeding $10,000",
  "framework_refs": ["FAIR/loss-magnitude/M3", "NIST-AI-RMF/Manage/2.3"],
  "threshold": {
    "framework": "FAIR",
    "magnitude_band_min": "M3"
  }
}
```

Each entry MUST declare a `threshold` field scoped to its framework reference. CI-lint enforces:
- SC-001 (every label cites ≥1 entry; every entry has ≥1 external ref)
- Q5 (`threshold` field present + framework-appropriate for any entry citing a framework that requires quantification)

#### `risk_preference` — moved to per-purpose (Q1, FR-030)

**No longer a standalone config file.** The dial value (`cautious | balanced | permissive`) is now declared **per Purpose** in `configs/purposes.yaml`:

```yaml
- purpose_id: inbox
  label: "Email triage and summarization"
  admissible_categories: [personal, work, email, public]
  risk_preference_dial: balanced     # NEW per Q1
  default_capabilities: [...]
  ...
- purpose_id: tax-prep
  label: "Annual tax preparation"
  admissible_categories: [financial, personal]
  risk_preference_dial: cautious     # different purposes carry different dials
  ...
```

The `sessions.risk_preference_at_spawn` column captures the dial value resolved from the session's purpose at spawn (and inherited on `fork`) for replayability (SC-002). A session cannot mutate its own dial at runtime — changes go through the FR-014 ratification path (Q3).

Migration: existing `configs/risk_preference.json` (if present) is read once during v5→v6 migration and its value applied as a default to every purpose without an explicit `risk_preference_dial`; the legacy file is then removed.

## Entities (logical, beyond what's already in spec.md §Key Entities)

The spec's Key Entities list is the authoritative semantic catalog. This data-model says *where* each lives:

| Entity | Lives in | Owner of writes |
|---|---|---|
| Data Category, Effect Class, Reversibility Label, Mutability Label (definitions) | `configs/labels.yaml` | Operator |
| Provenance Level | code (enum in `policy/labels.py`) | Maintainer |
| Decision Context (per session) | `sessions.axis_d` | Runtime (initiator+auth derived from authentication adapter; counterparty/expectedness/reversibility from rules + adapters) |
| Context Profile | `configs/profiles.yaml` | Operator |
| Admissibility Rule | `configs/purposes.yaml` (embedded in Purpose) | Operator |
| Purpose Handle (per session) | `sessions.purpose_handle` | Set at spawn (operator/CLI/human-ratified AI suggestion) |
| Source/Location Label Binding | `source_location_bindings` table (sourced from `configs/source_bindings.yaml`) | Operator |
| Risk Register Entry | `configs/risk_register.json` | Operator |
| Residual-Risk Exception | new event in audit log + a derived view (no separate table required) | Runtime |
| Label-Assignment Record | inlined as `assignment_provenance` on each label/binding | Runtime |
| Outcome Envelope | `configs/envelopes.yaml` | Operator |
| Risk-Preference Profile | **per-purpose** field in `configs/purposes.yaml` (+ snapshot in `sessions.risk_preference_at_spawn`) — Q1 | Owner |
| Override Policy | `override_policies` table (sourced from `configs/override_policy.yaml`) | Owner |
| Override Authorization | `override_policies.authorized_principal_ids` / `attester_principal_ids` | Owner |
| Ratification Authorization (Q3, FR-014) | `ratification_policies` table (sourced from `configs/ratification_policy.yaml`); reuses the same per-severity `{single-authorized | dual-control}` shape as Override Policy; operator MAY declare role-mapping identical-to or distinct-from Override Authorization. | Owner |
| Override Grant | `override_grants` table | Runtime (issued on human-attested grant) |
| Relationship Group | `relationship_groups` table (sourced from `configs/relationship_groups.yaml`) | Operator |
| Expectation Binding | `expectation_bindings` table (sourced from `configs/expectations.yaml`) | Operator |
| Reference Handle | `sessions.reference_handles` (per-session in-memory + persisted for replay) | Runtime |
| Disposable Isolation Region | `sessions.effective_isolation_region_id` + (when 004 lands) a region registry | Runtime + actuator |
| Human-Authored Decision Rule | `configs/rules.yaml` | Operator |

## State transitions

### Override Grant FSM (FR-032/036/038)
```
[requested by invoker]
   ├── policy=disallowed       → REFUSED (audited)
   ├── policy=single-authorized
   │      ├── invoker authorized       → GRANTED (audit) → consumed/expired → CLOSED
   │      └── invoker not authorized   → REFUSED (audited)
   └── policy=dual-control
          ├── awaiting attester
          │      ├── attester != invoker, authorized → ATTESTED → GRANTED → consumed/expired → CLOSED
          │      ├── attester == invoker             → REFUSED (audited)
          │      └── attester not authorized         → REFUSED (audited)
          └── expiry before attestation              → EXPIRED (audited)
```

### Reference Handle lifecycle (FR-047)
```
[runtime fetch returns labeled value]
   → handle_id created, recorded in sessions.reference_handles
   → planner receives handle_id (never the value)
   → planner invokes tool that accepts_handles=true with handle_id
   → decide() resolves handle → labels → outcome
   → on allow: runtime substitutes bound value into tool call AT the bind site;
               records audit `pattern3.handle_bind {handle_id, dest_canonical_id, audit_id}`
   → on deny: handle remains unbound; planner sees only the refusal reason
   → on session end: handles destroyed; full bind-trail retained in audit
```

### Purpose Handle (FR-046)
```
[session.new] → purpose_handle set OR session refused
[admissibility check at spawn]
   → forbidden categories produce 0 caps with read access
   → if any forbidden category would be unavoidably reachable → SPAWN REFUSED (fail-closed)
[fork] → child inherits parent.purpose_handle (purpose-preserving fork)
[explicit purpose change] → only via human-ratified rule (FR-014); creates a new session, not mutation
```

## Validation rules (drawn from FRs)

- Every persisted label in `axis_a`/`axis_b`/`axis_d` MUST cite ≥1 `risk_ids` (FR-015) — CI-lint + load-time check.
- Every Source/Location Binding MUST canonicalize on load; collisions with stable-core categories REFUSED (edge case).
- Every `ToolDefinition` MUST declare `effect_class`, `default_reversibility`, `default_mutability_target_facets`, `social_commitment`, `tool_provenance`, `accepts_handles`, `surfaces_destination_id`; missing fields → registry validation failure → daemon refuses to start (Principle VI).
- Every Override Grant MUST have non-null `audit_id` and `expires_at`; non-`single-authorized` grants MUST have non-null `attester_principal != invoker_principal` (FSM enforces).
- Every Reference Handle MUST have at least one `materialized_at[]` entry by the time it is destroyed if it was ever bound — else it's an unbound-but-issued handle (audit anomaly).
- Sessions whose `effective_isolation_region_id` is non-null MUST be marked as `EXECUTE.sandbox` and the resolver MUST compose their effects' reversibility to `reversible`/`system` per FR-040.
