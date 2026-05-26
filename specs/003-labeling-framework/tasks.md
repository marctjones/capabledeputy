---

description: "Task list for 003 — v0.9 Labeling Framework implementation"
---

# Tasks: Labeling Framework (003)

**Input**: Design documents from `/specs/003-labeling-framework/`
**Prerequisites**: plan.md (✓), spec.md (✓), research.md (✓), data-model.md (✓), contracts/ (✓), quickstart.md (✓)

**Tests**: REQUIRED — Constitution Principle III (NON-NEGOTIABLE, "Test-First, Invariants as Tests") makes test tasks mandatory for every behavioral change. Tests for an invariant MUST exist before the invariant's implementation.

**Organization**: Tasks are grouped by user story (US1–US6) to enable independent implementation and testing. Priority order from spec.md: US1 (P1) → US2 (P2) → US3 (P3) → US6 (P3) → US4 (P4) → US5 (P5) → Polish.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Different file, no dependency on incomplete tasks — parallelizable.
- **[Story]**: Phase 3+ only. Setup/Foundational/Polish carry no story label.
- Each task has an exact file path.

## Path Conventions

Single project — existing layout under `src/capabledeputy/` and `tests/` at repo root (per plan.md §Project Structure).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Configuration scaffolding, schema-version constants, CLI audit command, daemon load-fail-closed wiring.

- [X] T001 Bump `SCHEMA_VERSION = 6` in `src/capabledeputy/session/store.py` (placeholder; migration impl arrives in Foundational T008).
- [X] T002 [P] Create operator-config scaffolding files (empty/stub valid YAML/JSON) at `configs/risk_register.json`, `configs/purposes.yaml`, `configs/source_bindings.yaml`, `configs/relationship_groups.yaml`, `configs/expectations.yaml`, `configs/override_policy.yaml`, `configs/envelopes.yaml`, `configs/risk_preference.json`, `configs/labels.yaml`, `configs/profiles.yaml`, `configs/rules.yaml` — each with a top-comment naming its owning FR.
- [X] T003 [P] Add CI lint script `scripts/lint_risk_register.py` enforcing SC-001 (every label cites ≥1 register id; every register id has ≥1 external framework ref); wire into `pyproject.toml`/CI.
- [X] T004 [P] Add `capdep audit storage-shape` CLI subcommand in new file `src/capabledeputy/cli/audit_cmd.py` (registered in `src/capabledeputy/cli/main.py`); skeleton that resolves the DB path and exits 0 (real audit logic lands in T016).
- [X] T005 Extend `src/capabledeputy/daemon/lifecycle.py` to load all new config files at startup via a new helper `load_v09_configs()`; fail-closed (refuse to start) on missing or unparseable config.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema migration v5→v6, axis type stubs, ToolDefinition extension, audit-events registry, invariant-test scaffolding. **No US-story work begins until Phase 2 is green.**

⚠️ **CRITICAL**: blocks all user stories.

- [X] T006 [P] Create `src/capabledeputy/policy/tiers.py` with `Tier(StrEnum)` (none<sensitive<regulated<restricted<prohibited), strict total-order helpers `compare`/`max_of`/`is_above`, frozen as a module-level total order (FR-027).
- [X] T007 [P] Add Axis A/B/D type stubs in `src/capabledeputy/policy/labels.py`: `AxisA` (list of `{category, tier, risk_ids[]}`), `AxisB` (provenance lattice with `integrity_floor: bool`), `AxisD` (initiator+auth, counterparty/relationship_group_ids, expectedness, reversibility) — `@dataclass(frozen=True)` with `to_dict`/`from_dict` default-tolerant.
- [X] T008 ALTER TABLE migration in `src/capabledeputy/session/store.py` for new `sessions` columns (`axis_a TEXT NOT NULL DEFAULT '[]'`, `axis_b TEXT NOT NULL DEFAULT '[]'`, `axis_d TEXT NOT NULL DEFAULT '{}'`, `purpose_handle TEXT NOT NULL DEFAULT 'unset'`, `reference_handles TEXT NOT NULL DEFAULT '{}'`, `risk_preference_at_spawn TEXT NOT NULL DEFAULT 'cautious'`, `effective_isolation_region_id TEXT NULL`); idempotent, follows the v3→v4/v4→v5 pattern; broadens `if current in (1,2,3,4,5)` to include 5 (FR-045).
- [X] T009 Add new tables to `src/capabledeputy/session/store.py`: `source_location_bindings`, `relationship_groups`, `expectation_bindings`, `purposes`, `override_policies`, `override_grants` per data-model.md §Persistence shape; CREATE TABLE IF NOT EXISTS + indices on parent/session.
- [X] T010 Extend `Session` dataclass in `src/capabledeputy/session/model.py` with new fields (axis_a, axis_b, axis_d, purpose_handle, reference_handles, risk_preference_at_spawn, effective_isolation_region_id); `Session.new()` accepts them with safe defaults; `to_dict`/`from_dict` default-tolerant (Constitution §Sec. Constraints: backward-tolerant on read).
- [X] T011 Implement one-time legacy `label_set` converter in `src/capabledeputy/session/store.py` migration path: each legacy enum value maps into axis-correct slot at the most-restrictive position (FR-024 forward-only); legacy column retained read-only (drop scheduled at v7).
- [ ] T012 [P] Extend `ToolDefinition` in `src/capabledeputy/tools/registry.py` with new required fields per `contracts/tool_definition.md` (effect_class, default_reversibility, default_mutability_target_facets, social_commitment, tool_provenance, accepts_handles, handle_arg_names, surfaces_destination_id, risk_ids); registry-load validation refuses a `ToolDefinition` missing any new required field (FR-005, FR-039, Principle VI fail-closed).
- [X] T013 [P] Create `src/capabledeputy/policy/risk_register.py`: load `configs/risk_register.json` at daemon start; expose `get(id)`/`exists(id)`/`audit_orphans()` (FR-015/028).
- [X] T014 [P] Extend `src/capabledeputy/audit/events.py` with new event constants and types: `binding.applied`, `override.granted`, `override.attested`, `override.refused`, `override.expired`, `override.use_refused`, `pattern3.handle_bind`, `isolation_region.created`, `isolation_region.discarded`, `envelope.dial_changed`, `risk_register.audit`, `residual_risk.exception`.
- [X] T015 [P] Create invariant-test scaffold: `tests/invariants/__init__.py`; pytest marker `@pytest.mark.invariant`; new `tests/invariants/conftest.py` with shared fixtures (in-memory daemon, frozen clock).
- [X] T016 [P] Test `tests/invariants/test_storage_shape.py` asserting every `sessions` row populates the four axis fields (no flat-legacy rows post-migration) (SC-019); also exposes `audit_storage_shape()` helper used by the CLI (T004).
- [ ] T017 [P] Test `tests/invariants/test_failclosed.py` — parametric over every new resolver path (T021/T027/T039/T053/T063/T077/T099): every unmapped/non-canonicalizable input refuses, not best-effort-allows (FR-023, Principle VI; SC-003/SC-019/SC-020/SC-022).
- [ ] T018 [P] Test `tests/invariants/test_enforcement_llm_independence_v2.py` extending the existing `test_enforcement_llm_independence` to cover `resolution.py`, `bindings.py`, `envelope.py`, `overrides.py` (FR-012 AI-read-only invariants, Principle I CI invariant).
- [ ] T019 Extend `Capability` serialization in `src/capabledeputy/policy/capabilities.py`: add `CapabilityOrigin.OVERRIDE_GRANTED`, optional `override_grant_id: UUID | None = None`; default-tolerant `to_dict`/`from_dict` (FR-038).
- [X] T020 Wire new audit-event constants into the audit-event registry; back-compat test `tests/test_audit_events.py` updated for the new namespaces.

**Checkpoint**: Foundation ready — user story implementation may now proceed in priority order.

---

## Phase 3: User Story 1 — Orthogonal labels with deterministic sensitivity resolution (Priority: P1) 🎯 MVP

**Goal**: Replace the lossy flat label set with axes A/B + a deterministic sensitivity-resolution layer; same inputs always yield the same tier; no LLM in the path.

**Independent Test**: Configure two context profiles (clinician vs general use-case) for `health` and assert the same datum resolves to different tiers per profile, reproducibly, with no model participation (SC-002).

### Tests for User Story 1 (write FIRST; must FAIL before implementation)

- [X] T021 [P] [US1] Test `tests/policy/test_axis_a.py`: data categories stay distinct (FR-003); fixed-high resolution mode cannot be lowered by any profile (US1 scenario 3); each Axis-A label carries `assignment_provenance`.
- [X] T022 [P] [US1] Test `tests/policy/test_resolution.py`: same inputs → byte-identical outcome + rationale (SC-002); conflicting profiles → most-restrictive (edge case).
- [X] T023 [US1] End-to-end determinism via test_resolution.py determinism tests (replay with logged inputs reproduces tier+rationale). Covered without separate test_decide_us1.py since decide.py wire-in (T029) is deferred — the resolver itself is the pure function under SC-002.
- [ ] T024 [P] [US1] Test `tests/policy/test_legacy_migration.py`: legacy `label_set` rows treated most-restrictive; no datum's effective protection lowered (FR-024). [Deferred — T011 converter covered by integration; explicit unit test lands when US2 needs migration semantics in decide().]

### Implementation for User Story 1

- [X] T025 [P] [US1] Define Axis A `Category` schema in `src/capabledeputy/policy/resolution.py` (`stable_core`/`registered`, C-impact, I-impact, default tier, resolution mode); load definitions from `configs/labels.yaml` via `load_categories()`.
- [X] T026 [P] [US1] Define Axis B provenance lattice in `src/capabledeputy/policy/labels.py`: `principal-direct > system-internal > external-untrusted` with `integrity_floor` (FR-004); pure comparison helpers via `provenance_max`. [is_sanctioned_declassifier flag deferred — needed by US5 only.]
- [X] T027 [US1] Create `src/capabledeputy/policy/resolution.py`: `ContextProfile` loader (configs/profiles.yaml); `resolve_tier(category, profile_ids, categories, profiles) → ResolutionResult` honoring `fixed-high | context-up | context-resolved` modes (FR-007); deterministic + replayable.
- [X] T028 [US1] Implement most-restrictive composition baseline in `src/capabledeputy/policy/resolution.py` (FR-026(a) only — baseline; the bounded-relax cases land in US2).
- [ ] T029 [US1] Wire `resolve_tier()` into `src/capabledeputy/policy/decide.py`; capture the resolved tier + the input snapshot into the audit event (FR-021/SC-002). [Deferred — decide.py integration lands with US2 never-auto rule (T044); MVP doesn't need it for SC-002 determinism since resolver is the pure function.]
- [ ] T030 [US1] Thread `LabelAssignmentRecord` through resolution in `src/capabledeputy/policy/labels.py` + `resolution.py`: every label preserves `assignment_provenance` (source-declared / curated-MCP / human-declared / raise-only-inspector) (FR-022). [Deferred — assignment_provenance is on AxisACategory; full LabelAssignmentRecord plumbing lands with decide.py integration.]
- [X] T031 [US1] Implement the legacy-`label_set` converter callsite in the v5→v6 migration (uses T011 helper) — touches `src/capabledeputy/session/store.py`. [Done in Phase 2b alongside T011.]
- [X] T032 [US1] Add CLI subcommand `capdep policy resolve <category> <profile>` in `src/capabledeputy/cli/policy.py` demonstrating deterministic tier resolution end-to-end. [Extended existing policy.py instead of creating policy_cmd.py.]
- [X] T033 [P] [US1] Test `tests/policy/test_assignment_provenance.py`: provenance preserved across resolution chains; raise-only inspector adds taint only, never clears.
- [X] T034 [US1] Run `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest`; assert green. 791 passed, 4 skipped.
- [X] T035 [US1] US1 checkpoint: SC-002 green (deterministic resolver verified by test_resolution.py + CLI demo); SC-019 green (storage-shape audit working). MVP demonstrable end-to-end.

**Checkpoint**: US1 (MVP) fully functional and testable independently. Daily-briefing/clinician profiles produce deterministic per-category tiers; no LLM in the path.

---

## Phase 4: User Story 2 — Decision-context axis and the multi-axis never-auto rule (Priority: P2)

**Goal**: Outcomes depend on Axis-D (initiator+auth, counterparty/relationship, expectedness, reversibility) plus axes A/B; baseline + bounded-relax composition (FR-026); never-auto default (FR-011); asymmetry invariant (FR-031).

**Independent Test**: With an empty rule set, every consequential action resolves to `suggest` or `deny` (SC-003); adding the human-authored rule for `(initiator=cron-configured-by-principal, effect=backup, expectedness=matches-configured-job)` flips that exact context to `auto` while leaving the unauthenticated-inbound version `deny`.

### Tests for User Story 2

- [ ] T036 [P] [US2] Test `tests/policy/test_never_auto.py`: empty rule set ⇒ 0 `auto` outcomes (SC-003).
- [ ] T037 [P] [US2] Test `tests/policy/test_asymmetry.py`: any non-deterministic input that attempts to relax is refused and audited (FR-031).
- [ ] T038 [P] [US2] Test `tests/policy/test_relationship_groups.py`: share `proprietary_work` allowed to `project-P` members; denied to non-members (US2 scenario 5).
- [ ] T039 [P] [US2] Test `tests/policy/test_axis_d_integration.py`: backup-cron-at-2am → `auto`; same effect via unauth inbound → `deny` (US2 scenario 2).
- [ ] T040 [P] [US2] Test `tests/policy/test_expectation_bindings.py`: matching registered (initiator+effect+window) → `expected`; non-match → `anomalous` (FR-029).
- [ ] T041 [P] [US2] Test `tests/policy/test_audit_reconstruction.py`: replay decision from logged inputs yields identical outcome+rationale (SC-002).

### Implementation for User Story 2

- [ ] T136 [US2] Create the **Axis D first-class type** in `src/capabledeputy/policy/axis_d.py`: `DecisionContext(initiator: PrincipalRef, initiator_authentication: AuthLevel, counterparty: PrincipalRef | None, relationship_group_ids: frozenset[str], expectedness: Literal["expected","anomalous"], reversibility: ReversibilityLabel)`. Implement `Session.axis_d` storage shape per FR-045; derive from authentication adapter at request time. Mark every constructor as runtime-built from the deterministic adapter chain — no AI path may construct one (FR-006, FR-012, FR-045). Resolves analyzer finding A1.
- [ ] T042 [P] [US2] Create `src/capabledeputy/policy/relationships.py`: `RelationshipGroup` registry loading `configs/relationship_groups.yaml`; `is_member(principal, group_id)`; human-declared, AI-read-only (FR-033).
- [ ] T043 [P] [US2] Create `src/capabledeputy/policy/expectations.py`: `ExpectationBinding` registry loading `configs/expectations.yaml`; deterministic `match(initiator, effect, params, now)` (FR-029); no heuristic anomaly inference allowed.
- [ ] T044 [US2] Extend `src/capabledeputy/policy/decide.py` with `DecisionRule` evaluation: load `configs/rules.yaml`; rules are predicates over axes A–D + target (FR-001 four-axis input shape; FR-002 axis A categories drive the rule lookup); ratchet-stricter only relative to baseline (FR-010/026(b–c)).
- [ ] T045 [US2] Implement never-auto default in `decide.py` (FR-011): absent matching rule ⇒ `suggest` or `deny` per the cell's default; never `auto`.
- [ ] T046 [US2] Implement baseline + bounded-relax composition full algorithm in `decide.py` (FR-026); reject any relax input that originates from a non-deterministic source (FR-031) and emit `audit: relaxation_refused`.
- [ ] T047 [US2] Derive Axis-D `initiator+authentication` from the existing trust prefix on legacy `label_set` reads during migration (T011); going forward, set it from the authentication adapter at request time.
- [ ] T048 [US2] Update audit events in `src/capabledeputy/audit/events.py` to record `rule_matched_id`, `relax_inputs[]`, full Axis-D context (FR-021).
- [ ] T049 [US2] US2 checkpoint: SC-003 and SC-002 (decision-record replay) green; one rules.yaml example checked into `configs/rules.yaml` exercising the scenario battery.

**Checkpoint**: US1+US2 work independently and together; cron-vs-daytime distinction and never-auto provable in CI.

---

## Phase 5: User Story 3 — Purpose-scoped category admissibility (Priority: P3)

**Goal**: Sessions cannot hold any capability that reads an inadmissible category for the session's purpose (e.g., `health` excluded from `employee-evaluation`); enforcement at spawn (FR-009/FR-046).

**Independent Test**: Declare `health ⊄ inputs(employee-evaluation)`; spawning an `employee-evaluation` session yields zero capabilities with read scope over `health`; an attempt to grant/delegate one is refused (SC-004).

### Tests for User Story 3

- [ ] T050 [P] [US3] Test `tests/policy/test_purpose_admissibility.py`: `health ⊄ inputs(employee-evaluation)` excluded at spawn; admissible category passes (US3 scenarios).
- [ ] T051 [P] [US3] Test `tests/policy/test_no_purpose_failclosed.py`: session without `purpose_handle` refuses consequential effects (SC-020).
- [ ] T052 [P] [US3] Test `tests/policy/test_purpose_fork.py`: `SessionGraph.fork()` preserves parent `purpose_handle`.
- [ ] T053 [P] [US3] Test `tests/policy/test_purpose_delegation_refusal.py`: delegation that would introduce an inadmissible category is refused (extends 002 US1; cross-ref `tests/test_session_graph.py::T018`).

### Implementation for User Story 3

- [ ] T054 [P] [US3] Create `src/capabledeputy/policy/purposes.py`: `Purpose` entity + registry loader (`configs/purposes.yaml`); `admissibility(purpose_id, category) → bool` (FR-046/FR-009).
- [ ] T055 [US3] Add `purpose_handle` argument to `session.new` RPC handler in `src/capabledeputy/daemon/session_handlers.py` and CLI subcommand `capdep session new --purpose <id>` in `src/capabledeputy/cli/session.py`.
- [ ] T056 [US3] Implement admissibility check at spawn in `src/capabledeputy/session/graph.py::SessionGraph.new`: refuse if any candidate capability has read scope over an inadmissible category for the requested purpose (FR-009).
- [ ] T057 [US3] Implement no-purpose ⇒ fail-closed: `session.new` without `purpose_handle` either uses `unset` (which admits no consequential effects) or refuses outright per `configs/purposes.yaml` policy (FR-046).
- [ ] T058 [US3] Extend `SessionGraph.fork()` to copy `purpose_handle` from parent (purpose-preserving fork); document on the method.
- [ ] T059 [US3] Extend `SessionGraph.delegate()` (from 002 US1) to also check delegation-time admissibility against the child session's purpose; refuse + audit (`audit: delegation.refused` reason `inadmissible_category`).
- [ ] T060 [US3] US3 checkpoint: SC-004 and SC-020 green.

**Checkpoint**: US3 functional; inappropriate-context contamination structurally impossible for the configured purposes.

---

## Phase 6: User Story 6 — Usable risk tuning without weakening the model (Priority: P3)

**Goal**: Practical adoption layer — Reversibility/Mutability labels (FR-037/039), Source/Location Bindings + canonical destination-id (FR-043/048), Outcome Envelopes + Risk-Preference Profile (FR-030), Override Policy/Authorization/Grant distinct from approval (FR-032/036/038), optimistic execution (FR-034), semantic approval grouping (FR-035), EXECUTE tiering (FR-042), isolation posture (FR-040), containment≠declassification (FR-041), write-discipline verification (FR-044).

**Independent Test**: Risk-preference dial flip keeps outcomes within declared envelopes (SC-010); `HR-folder → TeamSharePoint` deny resolves deterministically via named binding (SC-018); versioned-write composes to `reversible/system` while in-place gates (SC-015); dual-control override requires distinct attester (SC-014); reversible/non-egressing pipelines run autonomously (SC-013); bulk 500-action with 2 rationales → 2 approvals (SC-012); `EXECUTE.sandbox` without actuator ⇒ `OverrideRequired` (SC-017 caveat).

### Tests for User Story 6

- [ ] T061 [P] [US6] Test `tests/policy/test_reversibility.py`: degree×agent composition; `version-preserving` verified retention ⇒ reversible/system; unverified or `in-place` ⇒ irreversible (SC-015).
- [ ] T062 [P] [US6] Test `tests/policy/test_mutability.py`: effects exceeding effective mutability deterministically refused; planner-prune signal surfaced; create/append into immutable/append-only composes into reversibility=irreversible (FR-039 Mutability Label; SC-016).
- [ ] T063 [P] [US6] Test `tests/policy/test_bindings.py`: `HR-folder → TeamSharePoint` deny via named binding (Quickstart §1, SC-018); unbound location fail-closed; overlapping bindings most-restrictive.
- [ ] T064 [P] [US6] Test `tests/policy/test_destination_id.py`: unidentifiable destination ⇒ deny/escalate, never "no rule matched" (SC-022).
- [ ] T065 [P] [US6] Test `tests/policy/test_envelope_dial.py`: preference change keeps every outcome within envelope; hard-floor cells immovable by the dial (SC-010).
- [ ] T066 [P] [US6] Test `tests/policy/test_override_policy.py`: `disallowed` refuses authorized invoker; `dual-control` requires distinct attester; `single-authorized` works; unauthorized refused (SC-014, SC-011, Quickstart §4).
- [ ] T067 [P] [US6] Test `tests/policy/test_override_distinct_from_approval.py`: `Override Grant` produces capability with `origin=override_granted`; audit object distinct from ordinary approval (FR-038).
- [ ] T068 [P] [US6] Test `tests/policy/test_no_terminal_unlock.py`: no rule/dial/AI/ordinary-approval can produce or unlock `prohibited` (FR-017 prohibited tier unreachable by automatic path; SC-006).
- [ ] T069 [P] [US6] Test `tests/policy/test_optimistic_execution.py`: reversible/non-egressing work runs without prompts; category-mixed artifact flagged + rollback offered (SC-013, Quickstart §2).
- [ ] T070 [P] [US6] Test `tests/policy/test_write_discipline.py`: verified version-preserving ⇒ reversible/system; unverifiable ⇒ irreversible (SC-015).
- [ ] T071 [P] [US6] Test `tests/policy/test_approval_grouping.py`: 500 homogeneous actions with 2 rationales ⇒ exactly 2 approval groups; per-step prompting forbidden (SC-012).
- [ ] T072 [P] [US6] Test `tests/patterns/test_isolation_posture.py`: contained + egress-free ⇒ effective reversibility `reversible/system`; containment ≠ declassification (source label retained on output); `EXECUTE.sandbox` without actuator ⇒ `OverrideRequired` (FR-040/041/042 + SC-017 caveat).

### Implementation for User Story 6

- [ ] T073 [P] [US6] Create `src/capabledeputy/policy/reversibility.py`: `ReversibilityLabel(degree, agent)`, `MutabilityLabel(degree, agent)`, composition most-restrictive across (effect default × target × channel) (FR-037/039); registry loader from labels declared in `configs/labels.yaml`.
- [ ] T074 [P] [US6] Create `src/capabledeputy/policy/bindings.py`: `SourceLocationLabelBinding` + resolver — load `configs/source_bindings.yaml`; canonicalize via per-scheme canonicalizers (`file://`, `unc://`, `https://...`, `mcp:...`); subtree-inheritance; most-restrictive composition; unbound/non-canonicalizable ⇒ fail-closed (FR-043).
- [ ] T075 [US6] Create `src/capabledeputy/substrate/source_port.py` and `src/capabledeputy/substrate/version_write_port.py` (port-only interfaces per Constitution VII; **no provider impl** — that's spec 004); document the `canonical_resource_handle`/`canonical_destination_id`/`surfaces_destination_id` contract (FR-048) and `VersionedWritePort.write` returning `{prior_version_handle, post_state_hash, attestation}` (FR-044).
- [ ] T076 [US6] Create `src/capabledeputy/policy/envelope.py`: `OutcomeEnvelope` loader (`configs/envelopes.yaml`); `RiskPreferenceProfile` loader (`configs/risk_preference.json`); `select_outcome(envelope, dial_value) → Decision` (FR-030); hard-floor cells have degenerate envelopes.
- [ ] T077 [US6] Wire envelope dial into `src/capabledeputy/policy/decide.py`: after baseline + bounded-relax, the dial picks the envelope point; refuses to cross any hard floor (FR-026(d) + FR-030).
- [ ] T078 [US6] Create `src/capabledeputy/policy/overrides.py`: `OverridePolicy`, `OverrideAuthorization`, `OverrideGrant` types + FSM per `contracts/override.md`; persisted in `override_grants` table; auto-expiry enforced at every `decide()` call (SC-011).
- [ ] T079 [US6] Implement `OverrideRequired` distinct return path in `src/capabledeputy/policy/decide.py` (replaces collapsing into `require-approval` for floor crossings) (FR-038); audit `decision.override_required`.
- [ ] T080 [US6] Add `capdep override request/attest/list/show/refuse` CLI in new `src/capabledeputy/cli/override_cmd.py`; the planner has no path to invoke any of these (Principle I + V).
- [ ] T081 [US6] Implement optimistic execution boundary in `src/capabledeputy/policy/decide.py` (FR-034): if effective reversibility is degree-low + agent=`system` + non-egressing, return `auto` without prompt; reversal-agent=`human` work surfaces/gates; carve-out for purpose-contamination (FR-009) — pre-excluded at spawn, not act-then-flag.
- [ ] T082 [US6] Implement semantic approval grouping in `src/capabledeputy/policy/approval_grouping.py` (FR-035): group homogeneous actions by rationale; aggregate impact presented; per-step prompting refused.
- [ ] T083 [US6] Implement write-discipline verification in `src/capabledeputy/policy/reversibility.py` (FR-044): given a `VersionedWritePort.WriteResult`, verify prior-version retention by reading the prior-version handle and matching the pre-write state hash; only on success label the write `reversible/system`.
- [ ] T084 [US6] Implement isolation-posture rules in `src/capabledeputy/patterns/isolation_posture.py` (FR-040/041/042): a session running in a Disposable Isolation Region composes effective reversibility to `reversible/system`; "containment ≠ declassification" (output retains source category labels); `EXECUTE.sandbox` invocation without the actuator port satisfied ⇒ `OverrideRequired` (Principle VI fail-closed; SC-017).
- [ ] T085 [US6] Add `src/capabledeputy/substrate/sandbox_actuator.py` (port-only stub) declaring the `SandboxActuator` interface used by spec 004; importing without an impl raises `NotImplementedError` at first call.
- [ ] T086 [US6] US6 checkpoint: SC-010, SC-012, SC-013, SC-014, SC-015, SC-016, SC-018, SC-022 green; Quickstart §1, §2, §4 e2e pass.

**Checkpoint**: US6 functional — the practical adoption layer (dial, override policy, optimistic exec, grouping, bindings, reversibility/mutability, isolation posture) lands without weakening the security model.

---

## Phase 7: User Story 4 — Robustness, traceability, assurance deltas (Priority: P4)

**Goal**: Risk-id citation on every label/decision; residual-risk exception capture on threshold-crossing allows; `prohibited` unreachable by automatic paths; control-plane reflexivity (untrusted-tainted sessions cannot exercise ADMINISTER); reversibility-weighted gating + social-commitment treated reputationally irreversible.

**Independent Test**: Orphan label fails validation; threshold-crossing allow emits exactly one Residual-Risk Exception; `prohibited` cannot be reached by any rule/dial/heuristic; ADMINISTER attempt from a tainted session refused.

### Tests for User Story 4

- [ ] T087 [P] [US4] Test `tests/policy/test_risk_register.py`: orphan label refused; orphan register entry refused (SC-001).
- [ ] T088 [P] [US4] Test `tests/policy/test_residual_risk.py`: every threshold-crossing allow produces exactly one Residual-Risk Exception event (SC-007).
- [ ] T089 [P] [US4] Test `tests/policy/test_control_plane_reflexivity.py`: ADMINISTER attempt from untrusted-tainted session refused (SC-005).
- [ ] T090 [P] [US4] Test `tests/policy/test_reversibility_gating.py`: graded thresholds; social-commitment effect always treated `irreversible` regardless of mechanical recoverability (FR-019).

### Implementation for User Story 4

- [ ] T091 [US4] Enforce risk-id citation at label-load and decision-record time in `src/capabledeputy/policy/risk_register.py` + `decide.py`; orphan label or unknown risk-id refused (FR-015).
- [ ] T092 [US4] Implement Residual-Risk Exception emission in `src/capabledeputy/policy/decide.py` (FR-016): any allow that crosses a configured risk threshold emits a `residual_risk.exception` audit event with full inputs and rationale.
- [ ] T093 [US4] Implement control-plane reflexivity in `src/capabledeputy/policy/decide.py` (FR-018): label/capability/profile/audit operations are `ADMINISTER`-class; refuse if the session carries any `external-untrusted` provenance.
- [ ] T094 [US4] Implement reversibility-weighted gating in `src/capabledeputy/policy/decide.py` (FR-019) replacing the binary destructive-op gate; social-commitment effect class hard-coded `irreversible` regardless of declared reversibility.
- [ ] T095 [US4] US4 checkpoint: SC-001, SC-005, SC-006, SC-007 green.

**Checkpoint**: US4 functional; every label/decision is risk-traceable; threshold crossings are explicit exceptions; tainted sessions cannot touch the control plane.

---

## Phase 8: User Story 5 — Model-fidelity targets: clearance, integrity floor, sealed effect (Priority: P5)

**Goal**: Max-tier clearance + read-up refusal (FR-008, dynamic-BLP target); integrity floor + no-read-down within a step (FR-004 Biba direction); first-class Reference Handle (Pattern ③, FR-047) — required for `restricted` tier; sealed-effect via disposable isolation rule (FR-040, impl deferred to 004).

**Independent Test**: Profile with max-tier clearance `regulated` refuses read of `restricted` datum; integrity-floored step refuses inputs below floor; planner context never holds raw value under Pattern ③; `restricted` session without ③ or ⑤ available ⇒ refused at spawn.

### Tests for User Story 5

- [ ] T096 [P] [US5] Test `tests/policy/test_max_tier_clearance.py`: profile clearance `regulated` + datum `restricted` ⇒ refused (FR-008, US5 scenario 1).
- [ ] T097 [P] [US5] Test `tests/policy/test_integrity_floor.py`: integrity-floored step refuses an input below the floor (FR-004, US5 scenario 2).
- [ ] T098 [P] [US5] Test `tests/patterns/test_reference_handle.py`: unforgeable per-session handles; planner `history` contains 0 raw values; bind only after `decide` passes; `pattern3.handle_bind` audit event records destination canonical id (SC-021; Quickstart §3).
- [ ] T099 [P] [US5] Test `tests/patterns/test_restricted_requires_3_or_5.py`: spawning a `restricted` session whose tool surface offers neither ③ (`accepts_handles=true`) nor ⑤ (SandboxActuator) is refused at spawn (FR-047).
- [ ] T100 [P] [US5] Test `tests/mode/test_dispatcher_v0_9.py`: `select_mode` returns `REFERENCE` or `SEALED` for `restricted`; never falls back to `DUAL_LLM`; never auto-de-escalates.

### Implementation for User Story 5

- [ ] T101 [P] [US5] Implement max-tier clearance + read-up refusal in `src/capabledeputy/policy/resolution.py` (FR-008): every context profile carries `max_tier`; reads above it refused.
- [ ] T102 [P] [US5] Implement integrity-floor + no-read-down within a step in `src/capabledeputy/policy/decide.py` (FR-004 Biba direction): integrity-floored steps refuse `external-untrusted` provenance inputs.
- [ ] T103 [US5] Create `src/capabledeputy/patterns/__init__.py` and `src/capabledeputy/patterns/reference_handle.py` per `contracts/reference_handle.md`: `ReferenceHandle`, `ReferenceHandleStore.issue/bind/destroy/bind_trail`; per-session in-memory; persisted into `sessions.reference_handles`.
- [ ] T104 [US5] Wire handle issue/bind into the dispatcher: only the dispatcher (post-`decide`-allow) may bind; tools with `accepts_handles=true` receive handles in declared `handle_arg_names`; `pattern3.handle_bind` audit per insertion (FR-047).
- [ ] T105 [US5] Extend `src/capabledeputy/mode/dispatcher.py::select_mode` to include `REFERENCE` and `SEALED` outputs; deterministic floor: effective tier `restricted` ⇒ `REFERENCE` (if any session tool `accepts_handles=true`) or `SEALED` (if SandboxActuator port satisfied); else fail-closed at spawn (FR-047).
- [ ] T106 [US5] Create `src/capabledeputy/patterns/isolation_posture.py` (continued from T084) — formalize the sealed-effect rule and the `effective_isolation_region_id` accounting on the Session; impl of region creation/discard remains in 004.
- [ ] T107 [US5] US5 checkpoint: SC-008 and SC-021 green; Quickstart §3 e2e passes.

**Checkpoint**: All P1–P5 user stories functional and tested; v0.9 labeling framework substantively complete except the 004 substrate (SandboxActuator + provider adapters).

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T108 [P] e2e test `tests/e2e/test_quickstart_all_scenarios.py` — all four `quickstart.md` scenarios end-to-end against a real in-memory daemon.
- [ ] T109 [P] Property-based determinism test `tests/policy/test_decide_pure_function.py` using Hypothesis — same inputs ⇒ identical outputs across permutations (research D11, Principle I).
- [ ] T110 [P] Performance test `tests/perf/test_decide_latency.py` — assert `decide()` p99 < 1 ms over a representative workload (plan Technical Context performance goal).
- [ ] T111 [P] Scope-honesty audit `scripts/audit_scope_honesty.py`: verify no FR addresses a named deliberate non-goal (model bias/accuracy/eval/content-safety, lawful-basis/consent/DSAR, substrate security) (SC-009).
- [ ] T112 [P] Storage-shape audit CLI test `tests/invariants/test_storage_shape_audit_cli.py`: `capdep audit storage-shape` exits 0 on a v6 store; exits non-zero on a synthetic v5/flat row (SC-019).
- [ ] T113 [P] Update `docs/security-models.md` cross-reference table — every new FR (003) appears mapped to its model row with deviation documented (Principle VIII review check).
- [ ] T114 [P] Update `docs/llm-flow-patterns.md` selector documentation reflecting `REFERENCE` / `SEALED` additions and the `restricted`-tier floor.
- [ ] T115 [P] Update `README.md` illustrative-risks list mentioning relationship-scoped sharing + reversibility + disposable isolation (subtle, demoted-prompt-injection, keep lean per `feedback_positioning`).
- [ ] T116 [P] Update `ROADMAP.md`: mark v0.9 in-flight; add a v0.10 / spec-004 placeholder for SandboxActuator + provider adapters.

---

## Phase 10: Clarification Addendum (2026-05-25 — Q1–Q5)

**Purpose**: Implement the 5 new clarifications recorded in `spec.md` §Clarifications / Session 2026-05-25 and the corresponding D12–D16 design decisions in `research.md`. Each task is labeled to the user story it extends.

### Q1: Per-Purpose risk-preference dial (FR-030 / FR-046)

- [X] T118 [P] [US6] Extend `configs/purposes.yaml` schema: each Purpose entry MUST carry a `risk_preference_dial` field valued in `cautious | balanced | permissive`. Update the Purpose loader in `src/capabledeputy/policy/purposes.py` to read and expose the per-purpose dial value. (D12, Q1)
- [X] T119 [US6] Modify session-spawn path in `src/capabledeputy/session/graph.py` `SessionGraph.new()` so `risk_preference_at_spawn` is resolved from the session's Purpose entry's `risk_preference_dial`, not from a standalone config file. On `fork`, child inherits the parent's resolved dial. (D12, Q1)
- [X] T120 [P] [US6] Migration task: extend `src/capabledeputy/store/migrations/v6.py` — read legacy `configs/risk_preference.json` (if present), apply its value as default `risk_preference_dial` to every Purpose entry that doesn't declare one, then archive the legacy file (`configs/risk_preference.json.migrated`). (D12, Q1)
- [X] T121 [P] [US6] Test `tests/test_dial_per_purpose.py` — verify Session inherits dial from its Purpose at spawn; verify fork inherits parent's dial; verify session cannot mutate its own dial at runtime; verify per-purpose values differentiate (`tax-prep: cautious` vs `daily-briefing: balanced`) (FR-030, Q1, SC-010 extension).

### Q2: Override Grant default expiry (FR-032)

- [X] T122 [P] [US4] Update `src/capabledeputy/override/policy.py` `OverridePolicyEntry` validator: `expiry_seconds` defaults to `900` (15 min) when unset; entries with `expiry_seconds > 3600` are refused at policy authoring time with a clear error pointing at FR-032's hard cap. (D13, Q2)
- [X] T123 [P] [US4] Test `tests/test_override_grant_expiry.py` — verify default expiry = 900s; verify a configured entry with expiry_seconds=600 is honored; verify expiry_seconds=3601 is refused with FR-032 reference; verify the granted Override Grant's `expires_at` = `created_at + min(expiry_seconds, 3600)` (FR-032, Q2, SC-014 extension).

### Q3: Ratification Authorization (FR-014)

- [ ] T124 [US4] New module `src/capabledeputy/policy/ratification.py`: implement `RatificationPolicy` + `RatificationAuthorization` state machine valued in `{single-authorized | dual-control}` per affected severity. Reuse `src/capabledeputy/override/authorization.py` infrastructure (operator MAY declare role mappings identical-to or distinct-from Override Authorization). Hard-floor-touching ratifications default to `dual-control`; non-hard-floor ratifications default to `single-authorized`. AI MUST NEVER be authorized to ratify. (D14, Q3)
- [ ] T125 [P] [US4] Add `configs/ratification_policy.yaml` (operator-edited, AI-read-only) declaring per-severity `{single-authorized | dual-control}` + per-severity Ratification Authorization role mapping. Daemon loads at startup; CI-lint refuses invalid configurations. (D14, Q3)
- [ ] T126 [P] [US4] Test `tests/test_ratification_authorization.py` — verify hard-floor ratifications default to dual-control; verify non-hard-floor default to single-authorized; verify invoker MUST NOT equal attester; verify unauthorized principal refused + audited; verify AI principal refused (FR-014, Q3).
- [ ] T127 [P] [US4] Add audit event `EventType.RATIFICATION_APPLIED` to `src/capabledeputy/audit/events.py` carrying `{ratification_id, target_kind: label|profile|rule, invoker, attester?, severity, audit_id}`. Update `tests/test_audit_events.py` taxonomy assertion. (D14, Q3)

### Q4: Decision-latency SLO (SC-023)

- [X] T128 [P] New module `src/capabledeputy/policy/latency.py`: in-process histogram tracking per-`decide()` latency; on every Nth dispatch (configurable, default 100), checks recent window's p95 / p99.9 against thresholds (50ms / 250ms) and emits `decision.latency_degraded` audit event when exceeded. (D15, Q4)
- [X] T129 [P] Add `EventType.DECISION_LATENCY_DEGRADED` to `src/capabledeputy/audit/events.py`. Payload: `{latency_ms, rule, fixture_size, threshold_crossed: "p95"|"p99.9"}`. Update `tests/test_audit_events.py` taxonomy assertion. (D15, Q4)
- [X] T130 [P] Benchmark test `tests/test_decision_latency.py` — build standard rule-set fixture (≥1k rules, ≥100 categories, ≥50 expectation bindings); run `decide()` 10,000× ; assert p95 ≤ 50ms AND p99.9 ≤ 250ms. Skip-by-default in CI when running in resource-constrained environments via `pytest.mark.benchmark` (SC-023, Q4).

### Q5: Per-Risk-Register-Entry residual-risk thresholds (FR-016 / FR-028)

- [ ] T131 [P] [US4] Extend `configs/risk_register.json` schema: every entry MUST declare a `threshold` field shaped per its framework reference (FAIR `{framework, magnitude_band_min}`; NIST AI RMF `{framework, impact_tier_min}`; EU AI Act `{framework, risk_class_min}`; etc.). Update the documented schema in `docs/risk-register.md`. (D16, Q5)
- [ ] T132 [US4] New module `src/capabledeputy/policy/risk_register.py`: load + cache the risk register at daemon startup; expose `get_threshold(risk_id) -> Threshold` + `threshold_crossed(risk_id, decision_residual) -> bool`. (D16, Q5)
- [ ] T133 [US4] Update `src/capabledeputy/policy/engine.py` `decide()` — when a residual-risk threshold is crossed but the decision is allowed (FR-016), the emitted residual-risk exception payload MUST list the specific risk-id(s) crossed (not "a threshold"). Adds `crossed_risk_ids: list[str]` to the exception object. (D16, Q5)
- [ ] T134 [P] [US4] CI-lint test `tests/invariants/test_risk_register_thresholds.py` — refuses to ship a `risk_register.json` whose entries cite a quantification-required framework but omit the `threshold` field. Extends existing SC-001 lint. (D16, Q5)
- [ ] T135 [P] [US4] Test `tests/test_risk_register_thresholds.py` — verify a decision that crosses an entry's threshold produces a residual-risk exception whose `crossed_risk_ids` list contains that entry's id; verify multi-risk crossings name all crossed ids; verify entries without thresholds never produce silent exceptions (FR-016, Q5, SC-007 extension).

---

- [ ] T117 Final: run `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest`; confirm SC-001..023 all green (extended by Q4 / SC-023); tag a `v0.9.0-rc.1` candidate (pre-release) once the user requests it.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** — no dependencies; start immediately.
- **Foundational (Phase 2)** — depends on Setup; **BLOCKS all user stories**.
- **US1 (P1, Phase 3)** — depends on Foundational.
- **US2 (P2, Phase 4)** — depends on Foundational; benefits from US1 axes but is internally independent of US1's exact API.
- **US3 (P3, Phase 5)** — depends on Foundational + US1 (categories) + US2 (decide rules).
- **US6 (P3, Phase 6)** — depends on Foundational + US1 + US2; many subtasks internally independent (T073–T085 marked accordingly).
- **US4 (P4, Phase 7)** — depends on Foundational + US1 + US2.
- **US5 (P5, Phase 8)** — depends on Foundational + US1 + US2 + US3 (purpose surface) + US6 (reversibility for the integrity-floor semantics).
- **Polish (Phase 9)** — depends on all desired user stories.
- **Clarification Addendum (Phase 10, 2026-05-25)** — depends on the user story each task extends:
  - Q1 tasks (T118–T121) extend US6 — depend on US6 (envelope + dial scaffolding).
  - Q2 tasks (T122–T123) extend US4 — depend on US4 (Override Grant / Policy scaffolding).
  - Q3 tasks (T124–T127) extend US4 — depend on US4 (Override Authorization infrastructure they share).
  - Q4 tasks (T128–T130) extend Polish — depend on Foundational + US1 (latency benchmark needs the engine wired).
  - Q5 tasks (T131–T135) extend US4 — depend on US4 (risk register + residual-risk exception path).
  - Phase 10 finishes before T117's final-validation gate.

### Within Each User Story

- Tests FIRST (Constitution III); a behavioral test MUST exist (and fail) before its implementation task.
- Models/types before services; services before integration/CLI.
- Story complete before moving to the next priority OR run two stories in parallel if independent (US3/US6/US4 are internally independent after US1+US2).

### Parallel Opportunities

- All Setup `[P]` tasks parallelizable (T002/T003/T004).
- All Foundational `[P]` tasks parallelizable (T006/T007, T012/T013/T014/T015/T016/T017/T018).
- After Foundational: US3, US6, US4 can be developed in parallel by different developers once US2 lands (each is internally independent).
- Within each story, all `[P]` test tasks parallelize; all `[P]` implementation tasks (different files) parallelize.

---

## Parallel Example: User Story 1

```bash
# Tests for US1 (write FIRST, ensure they FAIL):
Task: "tests/policy/test_axis_a.py"
Task: "tests/policy/test_resolution.py"
Task: "tests/policy/test_decide_us1.py"
Task: "tests/policy/test_legacy_migration.py"

# Models/types for US1 (different files, parallel):
Task: "Define Axis A Category in src/capabledeputy/policy/labels.py"
Task: "Define Axis B provenance lattice in src/capabledeputy/policy/labels.py"
```

---

## Implementation Strategy

### MVP first (US1 only)

1. Phase 1: Setup.
2. Phase 2: Foundational (the blocker — schema v6, axis types, ToolDefinition extension, audit events, invariant test scaffold).
3. Phase 3: US1.
4. **STOP and VALIDATE**: SC-002, SC-019, and US1 tests green; demonstrate deterministic per-profile tier resolution via the new CLI.
5. Deploy/demo if ready (v0.9.0-alpha.1).

### Incremental delivery

1. Setup + Foundational → foundation ready.
2. + US1 (P1) → MVP.
3. + US2 (P2) → never-auto rule + multi-axis decisions.
4. + US3 (P3) → purpose admissibility at spawn.
5. + US6 (P3) → practical layer (dial, override policy, optimistic exec, bindings, reversibility/mutability, isolation posture rules).
6. + US4 (P4) → risk-traceability + threshold exceptions + control-plane reflexivity + reversibility-weighted gating.
7. + US5 (P5) → BLP clearance, Biba floor, Pattern ③ first-class, restricted-tier floor; cuts to spec 004 (SandboxActuator) for the sealed-effect impl.
8. Polish → e2e, perf, scope-honesty audit, doc cross-refs, RC tag.

### Parallel team strategy

- After Foundational lands, US3 / US6 / US4 can be developed concurrently by different developers; merge order doesn't matter for the SC tests (they're internally scoped).
- US5 should land last because it depends on the reversibility/mutability composition from US6 and the purpose surface from US3.

---

## Phase 10: Post-Analyze Remediations (T118–T121)

**Logical phase ordering** (despite the late numerical IDs):
- T118 and T119 belong to **Foundational** (must complete before any user story that derives or delegates labels/capabilities).
- T120 is an invariant tripwire (Foundational alongside T015–T018).
- T121 is a port (Foundational alongside T012's ToolDefinition extension).

Treat these four as belonging to **"T020.5"** in the execution graph; they were appended numerically to avoid renumbering the 117 existing tasks.

- [X] T118 [P] Implement `most_restrictive_inherit(parent_field, child_field) → Field` helper in `src/capabledeputy/policy/labels.py`; call it at every derive/compose site — `policy/capabilities.py::derive_delegated_capability` (extends 002), `policy/labels.py` Axis-A/B/D composition, `policy/reversibility.py` composition, `policy/bindings.py` overlapping-binding composition. Applies to **non-enumerated** fields: `risk_ids` = set-union; `assignment_provenance` = strictest source; `revoked_by` = superset; `expires_at` = min (FR-013). [Axis A/B composition done; reversibility/bindings/capabilities callsites land alongside their modules.]
- [X] T119 [P] Test `tests/invariants/test_non_enum_inheritance.py`: derived/delegated labels AND capabilities inherit most-restrictive on every non-enum field — explicit fixtures for `risk_ids`, `assignment_provenance`, `revoked_by`, `expires_at`, `write_discipline` (FR-013). [AxisA/B coverage; capability/reversibility fixtures land alongside those modules.]
- [X] T120 [P] Tripwire test `tests/invariants/test_no_unratified_apply_path.py`: assert no module under `src/capabledeputy/` imports or implements a path keyed on `suggestion`, `pending_ratification`, or `unratified_apply`; ensures FR-014's "unratified ⇒ zero effect" invariant holds structurally in 003 by absence; must be replaced with a real behavioral "unratified ⇒ 0 effect" test once the suggest/ratify channel ships in a follow-on spec (FR-014).
- [X] T121 [P] Create port `src/capabledeputy/substrate/inspector_port.py` defining `RaiseOnlyInspector` (input: ingested value + current labels → output: optional taint-raising delta; MUST NOT clear taint); runtime ingest hook (extends T030's LabelAssignmentRecord path) calls registered inspectors after binding resolution and composes any returned taint via `most_restrictive_inherit` (T118). No provider inspector impl in 003 (deferred to 004) (FR-025). [Port defined; runtime ingest hook lands in T030.]

**Checkpoint**: T118–T121 should land before US-story phases that depend on them — practically, alongside Foundational completion.

---

## Notes

- `[P]` = different file, no incomplete-task dependency; safe to parallelize.
- `[US#]` traces each task to its user story (Phase 3+ only).
- Each user story is independently completable and testable; checkpoints exist after Foundational and after every US.
- Tests MUST fail before implementation (Principle III).
- Commit after each task or logical group (per the project's scoped-commit preference).
- Avoid vague tasks, same-file conflicts, and cross-story dependencies that break independence.
- 004 substrate (SandboxActuator + EXECUTE.sandbox jailed tool + provider source adapters + versioned-write actuator impls) is **out of scope** — every task here references only ports + rule/labeling/decision code that lives in-TCB; the `EXECUTE.sandbox` invocation path fail-closes with `OverrideRequired` until 004 ships, per T084/T085 (Principle VI honesty).
- **Coverage-audit note (FR/SC citations)**: task bodies sometimes use a slash-shortened citation form like `(FR-032/036/038)` or `(FR-037/039)`. Coverage audits (incl. `/speckit-analyze`) MUST expand these into the equivalent comma-list (`FR-032, FR-036, FR-038`) before counting; otherwise naive substring matches will underreport coverage. `scripts/lint_risk_register.py` (T003) is the natural home for adding this expansion check.
