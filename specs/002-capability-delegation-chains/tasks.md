---

description: "Task list — Capability Delegation Chains (002), post-clarify"
---

# Tasks: Capability Delegation Chains

**Input**: Design documents from `/specs/002-capability-delegation-chains/`
**Prerequisites**: plan.md, spec.md (incl. Clarifications 2026-05-17 → FR-015/FR-016), research.md (D1–D9), data-model.md, contracts/delegation.md (C1–C4), quickstart.md

**Tests**: REQUIRED (not optional) — Constitution III (Test-First,
Invariants as Tests, NON-NEGOTIABLE) and the invariant Success Criteria
SC-001..SC-007 mandate them.

**Organization**: by user story (US1 P1 / US2 P2 / US3 P3); each an
independently testable increment. Single-project layout per plan.md.
Capability provenance rides the `capability_set` JSON blob (no DDL);
the session-level `revoked_audit_ids` is columnar → `SCHEMA_VERSION`
4→5 idempotent additive migration (research D7, corrected at impl).
`cap_uses` is reused for pooled accounting.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different file, no incomplete dependency)

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 [P] Add audit event types `DELEGATION_GRANTED`, `DELEGATION_REFUSED`, `CAPABILITY_CASCADE_REVOKED` to the `EventType` enum in src/capabledeputy/audit/events.py
- [X] T002 [P] Add `DelegationRequest` + `DelegationRefusal` frozen dataclasses (transient; reason set incl. `kind-not-held`, `pattern-not-subset`, `amount-widened`, `expiry-extended`, `rate-loosened`, `destructive-widened`, `revoked-by-narrowed`, `lifetime-extended`, `parent-dead`, `depth-exceeded`, `cycle`, `self-delegation`) in src/capabledeputy/policy/capabilities.py
- [X] T003 [P] Add `CAPDEP_MAX_DELEGATION_DEPTH` env read (default 3) alongside existing `CAPDEP_*` reads in src/capabledeputy/daemon/lifecycle.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: data-model extensions ALL stories depend on. No DDL.

- [X] T004 Add `parent_audit_id: UUID | None = None` and `depth: int = 0` to `Capability` in src/capabledeputy/policy/capabilities.py
- [X] T005 Add `DELEGATED` value to the `CapabilityOrigin` enum (string-serialized) in src/capabledeputy/policy/capabilities.py
- [X] T006 Extend `Capability.to_dict`/`from_dict` for `parent_audit_id`/`depth`/`origin=DELEGATED` with default-tolerant read in src/capabledeputy/policy/capabilities.py
- [X] T007 Add `revoked_audit_ids: frozenset[UUID] = frozenset()` to the session model + default-tolerant `to_dict`/`from_dict` in src/capabledeputy/session/model.py
- [X] T008 [P] Implement pure `pattern_is_subset(child, parent) -> bool` (conservative/decidable per research D4; not-provable ⇒ False) in src/capabledeputy/policy/capabilities.py
- [X] T009 [P] Serialization round-trip tests for T006/T007 incl. old-row default tolerance in tests/test_policy_capabilities.py and tests/test_session_store.py
- [X] T010 [P] `pattern_is_subset` tests — proven-subset accepted, undecidable/broadening rejected (fail-closed) in tests/test_policy_capabilities.py

**Checkpoint**: data model + helpers exist and round-trip.

---

## Phase 3: US1 — Delegate an attenuated capability (Priority: P1) — MVP

**Goal**: engine-derived attenuated capability; every broadening
refused across ALL fields (six dims + FR-016 non-enumerated);
LLM-isolated.

- [X] T011 [US1] Implement pure `derive_delegated_capability(parent, request, *, depth_limit) -> Capability | DelegationRefusal`: ordered preconditions (kind-not-held, parent-dead, depth-exceeded, cycle/self) then per-dimension clamp-or-refuse for the six dims per contracts/delegation.md C1, fresh `audit_id`, `depth=parent.depth+1` in src/capabledeputy/policy/capabilities.py
- [X] T012 [US1] Extend `derive_delegated_capability` with FR-016 non-enumerated clamps: `revoked_by = parent.revoked_by ∪ request.add` (refuse `revoked-by-narrowed` if request removes any); `expiry` lifetime clamped on `one_shot<session<persistent` default `one_shot` (refuse `lifetime-extended`); `origin = CapabilityOrigin.DELEGATED` in src/capabledeputy/policy/capabilities.py
- [X] T013 [US1] Implement `SessionGraph.delegate(parent_sid, child_sid, request)`: resolve parent's live cap of `request.kind`, call derivation, on grant register the derived cap into the child + record provenance, emit `delegation.granted`/`delegation.refused` in src/capabledeputy/session/graph.py
- [X] T014 [US1] Add `session.delegate` daemon RPC (control-plane; accepts only the narrowing request; ignores any model-supplied capability) in src/capabledeputy/daemon/session_handlers.py
- [X] T015 [US1] Add `capdep session delegate <parent> <child> --kind --pattern --max-amount --ttl --rate` CLI wired to the RPC in src/capabledeputy/cli/main.py
- [X] T016 [P] [US1] Exhaustive per-dimension attenuation matrix test (equal/narrower/wider × the six dims ⇒ clamp or named refusal) — SC-001 — in tests/test_policy_capabilities.py
- [X] T017 [P] [US1] FR-016 tests: derived `revoked_by ⊇ parent`; `expiry` ≤ parent, default `one_shot`; `origin == DELEGATED`; requests narrowing `revoked_by` or extending lifetime refused — in tests/test_policy_capabilities.py
- [X] T018 [P] [US1] Tests: kind-not-held, parent-dead, self/cycle refusals; provenance recorded; audit pair emitted — in tests/test_session_graph.py
- [X] T019 [P] [US1] LLM-isolation invariant test: model-supplied widened `Capability` ignored; only engine-derived cap in effect — SC-006 — in tests/test_delegation_e2e.py

**Checkpoint**: US1 independently demonstrable; MVP shippable
(monotonic attenuation across all fields + LLM-isolation proven).

---

## Phase 4: US2 — Cascade revocation across the live graph (Priority: P2)

**Goal**: revoke/expire/rate-exhaust of an ancestor makes every
descendant inert next decision; pooled rate (FR-015) so a child cannot
out-spend an ancestor; pending approvals invalidated; audited.

- [ ] T020 [US2] Implement the cascade guard inside `decide()`: `inert(C)` = self expired/rate-exhausted/`audit_id ∈ revoked_audit_ids` OR any ancestor inert (O(depth) provenance walk, research D1); deny with distinct reason `capability-cascaded` attributed to the originating ancestor in src/capabledeputy/policy/engine.py
- [ ] T021 [US2] Implement pooled `SessionGraph.record_cap_use` (FR-015/D8/C4): on a granted dispatch of `C`, append the timestamp under `C.audit_id` AND each ancestor `audit_id` (walk `parent_audit_id` upward, O(depth)), each into the session holding that capability; idempotent per dispatch in src/capabledeputy/session/graph.py
- [ ] T022 [US2] Wire the pooled fan-out at the granted-dispatch site so delegated tool calls record against the chain (extend the existing rate-limited-cap recording) in src/capabledeputy/tools/client.py
- [ ] T023 [US2] Add `SessionGraph.revoke(session_id, audit_id)` (adds to `revoked_audit_ids`) + `capability.revoke` daemon RPC + `capdep capability revoke` CLI (operator/control-plane only) in src/capabledeputy/session/graph.py, src/capabledeputy/daemon/session_handlers.py, src/capabledeputy/cli/main.py
- [ ] T024 [US2] Invalidate any pending approval where `inert(approval.capability_requested)` (reuse existing `ApprovalRequest.capability_requested`; no new linkage) and emit `capability.cascade_revoked` (originating audit_id, trigger, affected descendant audit_ids + sessions) in src/capabledeputy/approval/queue.py
- [ ] T025 [P] [US2] Tests: revoke/expire/rate-exhaust ancestor ⇒ child & grandchild denied next decision with `capability-cascaded`; reason distinct from expired/rate/prior-use — SC-002 — in tests/test_policy_engine.py
- [ ] T026 [P] [US2] FR-015 pooled-rate test: ancestor cap N uses/window, ancestor makes 0 calls, child makes N granted calls ⇒ child's (N+1)th DENIED though child's own window not full; sibling unaffected — US2-4 — in tests/test_policy_engine.py
- [ ] T027 [P] [US2] Test: pending approval authorized by a cascaded descendant can no longer be approved into ALLOW; one `capability.cascade_revoked` record — SC-003/SC-005 — in tests/test_approval_chokepoint_registration.py
- [ ] T028 [P] [US2] Test: a call already past the chokepoint before revoke is NOT unwound (FR-009) in tests/test_delegation_e2e.py

**Checkpoint**: containment closed — a child cannot outlive, out-spend,
or escape the prior-use kill-set of its ancestor.

---

## Phase 5: US3 — Bounded delegation depth (Priority: P3)

- [X] T029 [US3] Enforce `parent.depth + 1 ≤ depth_limit` in the `derive_delegated_capability` precondition order (before clamps), limit threaded from T003, refusal `depth-exceeded` independent of other dimensions in src/capabledeputy/policy/capabilities.py and src/capabledeputy/session/graph.py
- [X] T030 [P] [US3] Tests: chain of exactly N attenuating hops succeed; N+1 ⇒ `depth-exceeded` even when a valid attenuation; reconfiguring the limit governs only new delegations, existing deeper chains stay valid until independently revoked/expired — SC-004 — in tests/test_session_graph.py

---

## Phase 6: Polish & Cross-Cutting

- [ ] T031 [P] End-to-end quickstart test (all steps incl. 6a pooled-rate, 6b inherit-restrictive, no-retro-unwind, LLM-isolation) in tests/test_delegation_e2e.py
- [ ] T032 [P] Determinism test: repeat US1+US2 flows; assert byte-identical decisions and identical audit content — SC-007 — in tests/test_delegation_e2e.py
- [ ] T033 [P] Update ROADMAP.md (v0.8 delegation row + commit) and set `specs/002-capability-delegation-chains/spec.md` Status: Implemented
- [ ] T034 Full gate green: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright` (0), `uv run pytest` (all pass) — Constitution III done-criteria
- [ ] T035 [P] Update `docs/security-models.md`: move the "Capability delegation chains" row from *spec'd* to *implemented* and verify the documented deviations (single-parent tree; cascade computed at decide()) match the built code — Constitution VIII obligation (model-faithfulness map MUST stay truthful)

---

## Dependencies & Story Completion Order

- **Setup (T001–T003)** → **Foundational (T004–T010)** block everything.
- **US1 (T011–T019)** depends only on Foundational → the MVP.
- **US2 (T020–T028)** depends on US1 (needs provenance from T011/T013); T021/T022 pooled fan-out depends on T011 (provenance) + reuses v0.7 `cap_uses`.
- **US3 (T029–T030)** depends on US1; independent of US2.
- **Polish (T031–T035)** last.

```
Setup → Foundational → US1 (MVP) ─┬─→ US2 ─┐
                                  └─→ US3 ──┴─→ Polish
```

## Parallel Execution Examples

- Setup: T001–T003 [P].
- Foundational: T008–T010 [P] after T004–T007.
- US1 tests: T016, T017, T018, T019 [P] once T011–T015 exist.
- US2 tests: T025, T026, T027, T028 [P] once T020–T024 exist.

## Implementation Strategy

- **MVP = Phases 1–3 (through US1)**: monotonic attenuation across the
  six dims **and** the FR-016 non-enumerated fields; every broadening
  refused; LLM-isolation proven. Independently valuable.
- Then US2 (containment incl. FR-015 pooled rate), then US3
  (hardening), then Polish.
- Each phase leaves suite green + linter clean + pyright 0
  (Constitution III; incremental-reviewable Workflow gate).
- No `SCHEMA_VERSION` bump; additive default-tolerant fields; pooled
  accounting reuses the v0.7 `cap_uses` map (research D7/D8).

## Requirement → Task coverage (for /speckit-analyze)

FR-001 T013–T015 · FR-002 T011/T016 · FR-003 T011/T016 · FR-004
T008/T010/T011 · FR-005 T011/T030 · FR-006 T003/T029/T030 · FR-007
T020/T025 · FR-008 T024/T027 · FR-009 T028 · FR-010 T004/T006/T013/T018
· FR-011 T001/T013/T024 · FR-012 T014/T019 · FR-013 T011/T018 · FR-014
T011/T018 · **FR-015 T021/T022/T026** · **FR-016 T005/T012/T017** ·
SC-001 T016 · SC-002 T020/T025/T026 · SC-003 T024/T027 · SC-004
T029/T030 · SC-005 T001/T027 · SC-006 T019 · SC-007 T032.
