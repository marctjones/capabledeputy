---

description: "Task list — Capability Delegation Chains (002)"
---

# Tasks: Capability Delegation Chains

**Input**: Design documents from `/specs/002-capability-delegation-chains/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/delegation.md, quickstart.md

**Tests**: REQUIRED (not optional) for this feature — Constitution III
(Test-First, Invariants as Tests, NON-NEGOTIABLE) and the spec's
invariant Success Criteria SC-001..SC-007 mandate them.

**Organization**: by user story (US1 P1 / US2 P2 / US3 P3), each an
independently testable increment. Single-project layout per plan.md.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different file, no incomplete dependency)
- Paths are repository-root absolute-relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: small scaffolding every story needs. Existing repo — no
project init.

- [ ] T001 [P] Add audit event types `DELEGATION_GRANTED = "delegation.granted"`, `DELEGATION_REFUSED = "delegation.refused"`, `CAPABILITY_CASCADE_REVOKED = "capability.cascade_revoked"` to the `EventType` enum in src/capabledeputy/audit/events.py
- [ ] T002 [P] Add `DelegationRequest` and `DelegationRefusal` frozen dataclasses (transient, not persisted; reasons enumerated per data-model.md) in src/capabledeputy/policy/capabilities.py
- [ ] T003 [P] Add `CAPDEP_MAX_DELEGATION_DEPTH` env read (default 3) alongside the existing `CAPDEP_*` config reads in src/capabledeputy/daemon/lifecycle.py

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: data-model extensions that ALL stories depend on. MUST
complete before Phase 3. No `SCHEMA_VERSION` bump (default-tolerant
JSON, research D7).

- [ ] T004 Add `parent_audit_id: UUID | None = None` and `depth: int = 0` fields to `Capability` in src/capabledeputy/policy/capabilities.py
- [ ] T005 Extend `Capability.to_dict`/`from_dict` for `parent_audit_id`/`depth` with default-tolerant read (missing ⇒ None/0) in src/capabledeputy/policy/capabilities.py
- [ ] T006 Add `revoked_audit_ids: frozenset[UUID] = frozenset()` to the session model + default-tolerant `to_dict`/`from_dict` in src/capabledeputy/session/model.py
- [ ] T007 [P] Implement pure `pattern_is_subset(child: str, parent: str) -> bool` (conservative/decidable per research D4: exact-equal or strict literal-narrowing of a single trailing wildcard; else False) in src/capabledeputy/policy/capabilities.py
- [ ] T008 [P] Unit tests for T005/T006 serialization round-trip incl. old-row default tolerance in tests/test_policy_capabilities.py and tests/test_session_store.py
- [ ] T009 [P] Unit tests for `pattern_is_subset` — proven-subset accepted, undecidable/broadening rejected (fail-closed) in tests/test_policy_capabilities.py

**Checkpoint**: data model + helpers exist and round-trip; nothing
delegated yet.

---

## Phase 3: US1 — Delegate an attenuated capability (Priority: P1) — MVP

**Goal**: spawn child + engine-derived attenuated capability; every
broadening refused; LLM-isolated.
**Independent test**: equal / each-narrower / each-wider per dimension
⇒ clamped cap or correct deterministic refusal, no model involvement.

- [ ] T010 [US1] Implement pure `derive_delegated_capability(parent, request, *, depth_limit) -> Capability | DelegationRefusal` — ordered precondition checks (kind-not-held, parent-dead, depth-exceeded, cycle/self) then per-dimension clamp-or-refuse per contracts/delegation.md C1, fresh `audit_id`, `depth=parent.depth+1` — in src/capabledeputy/policy/capabilities.py
- [ ] T011 [US1] Implement `SessionGraph.delegate(parent_sid, child_sid, request) -> Capability | DelegationRefusal`: resolve parent's live cap of `request.kind`, call T010, on grant register the derived cap into the child session and record provenance, emit `delegation.granted`/`delegation.refused` in src/capabledeputy/session/graph.py
- [ ] T012 [US1] Add `session.delegate` daemon RPC (control-plane; accepts only the narrowing request; ignores any model-supplied capability) in src/capabledeputy/daemon/session_handlers.py
- [ ] T013 [US1] Add `capdep session delegate <parent> <child> --kind --pattern --max-amount --ttl --rate` CLI wired to the RPC in src/capabledeputy/cli/main.py
- [ ] T014 [P] [US1] Exhaustive per-dimension attenuation matrix test (equal/narrower/wider × kind/pattern/amount/expiry/rate/destructive ⇒ clamp or named refusal) — SC-001 — in tests/test_policy_capabilities.py
- [ ] T015 [P] [US1] Tests: kind-not-held, parent-dead, self/cycle refusals; provenance recorded; audit pair emitted — in tests/test_session_graph.py
- [ ] T016 [P] [US1] LLM-isolation invariant test: a model-supplied widened `Capability` in the request is ignored; only the engine-derived cap is in effect — SC-006 — in tests/test_delegation_e2e.py

**Checkpoint**: US1 independently demonstrable; MVP shippable.

---

## Phase 4: US2 — Cascade revocation across the live graph (Priority: P2)

**Goal**: revoke/expire/rate-exhaust of an ancestor makes every
transitive descendant inert at the next decision; pending approvals
invalidated; audited. No retro-unwind.

- [ ] T017 [US2] Implement the cascade guard inside `decide()`: `inert(C)` = self expired/rate-exhausted/`audit_id ∈ revoked_audit_ids` OR any ancestor inert (O(depth) provenance walk, research D1); deny with new distinct reason `capability-cascaded` attributed to the originating ancestor — in src/capabledeputy/policy/engine.py
- [ ] T018 [US2] Add `SessionGraph.revoke(session_id, audit_id)` (adds to `revoked_audit_ids`) + `capability.revoke` daemon RPC + `capdep capability revoke` CLI (operator/control-plane only) in src/capabledeputy/session/graph.py, src/capabledeputy/daemon/session_handlers.py, src/capabledeputy/cli/main.py
- [ ] T019 [US2] Invalidate any pending approval whose authorizing capability is `inert`, and emit `capability.cascade_revoked` (originating audit_id, trigger, affected descendant audit_ids + sessions) in src/capabledeputy/approval/queue.py
- [ ] T020 [P] [US2] Tests: revoke / expire / rate-exhaust an ancestor ⇒ child & grandchild denied next decision with `capability-cascaded`; reason distinct from expired/rate/prior-use — SC-002 — in tests/test_policy_engine.py
- [ ] T021 [P] [US2] Test: pending approval authorized by a cascaded descendant can no longer be approved into ALLOW; one `capability.cascade_revoked` audit record — SC-003/SC-005 — in tests/test_approval_chokepoint_registration.py
- [ ] T022 [P] [US2] Test: a call already past the chokepoint before revoke is NOT unwound (FR-009); sibling delegated separately is unaffected — in tests/test_delegation_e2e.py

**Checkpoint**: containment hole closed — a child cannot outlive or
out-spend its ancestor.

---

## Phase 5: US3 — Bounded delegation depth (Priority: P3)

**Goal**: configurable max chain depth; over-depth refused
deterministically, independent of other dimensions; reconfig affects
only new delegations.

- [ ] T023 [US3] Enforce `parent.depth + 1 ≤ depth_limit` in `derive_delegated_capability` precondition order (before dimension clamps) with refusal reason `depth-exceeded`, reading the configured limit threaded from T003 — in src/capabledeputy/policy/capabilities.py and src/capabledeputy/session/graph.py
- [ ] T024 [P] [US3] Tests: chain of exactly N attenuating hops all succeed; N+1 ⇒ `depth-exceeded` even when the request is a valid attenuation; reconfiguring the limit governs only new delegations, existing deeper chains stay valid until independently revoked/expired — SC-004 — in tests/test_session_graph.py

**Checkpoint**: runaway/pathological delegation trees impossible;
cascade-walk cost bounded.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T025 [P] End-to-end quickstart scenario test (the 9 steps in quickstart.md: attenuate → reject-broaden → chain → cascade revoke/expire/rate → no-retro-unwind → LLM-isolation → determinism) in tests/test_delegation_e2e.py
- [ ] T026 [P] Determinism test: repeat US1+US2 flows; assert byte-identical decisions and identical audit record content — SC-007 — in tests/test_delegation_e2e.py
- [ ] T027 [P] Update ROADMAP.md (v0.8 delegation row + commit) and mark `specs/002-capability-delegation-chains/spec.md` Status: Implemented
- [ ] T028 Full gate green: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright` (0), `uv run pytest` (all pass) — Constitution III done-criteria

---

## Dependencies & Story Completion Order

- **Setup (T001–T003)** → **Foundational (T004–T009)** block everything.
- **US1 (T010–T016)** depends only on Foundational → the MVP; ship-able alone.
- **US2 (T017–T022)** depends on US1 (needs provenance from T010/T011).
- **US3 (T023–T024)** depends on US1 (extends the precondition chain); independent of US2.
- **Polish (T025–T028)** last; T025/T026 need US1+US2(+US3).

```
Setup → Foundational → US1 (MVP) ─┬─→ US2 ─┐
                                  └─→ US3 ──┴─→ Polish
```

## Parallel Execution Examples

- Setup: T001, T002, T003 all [P] (distinct files).
- Foundational: T007, T008, T009 [P] after T004–T006 land.
- US1 tests: T014, T015, T016 [P] once T010–T013 exist.
- US2 tests: T020, T021, T022 [P] once T017–T019 exist.

## Implementation Strategy

- **MVP = Phases 1–3 (through US1)**: monotonic attenuation enforced +
  every broadening refused + LLM-isolation proven. Independently
  valuable and the irreducible core (spec US1 "Why this priority").
- Then US2 (containment), then US3 (hardening), then Polish.
- Each phase leaves suite green + linter clean + pyright 0 before the
  next (Constitution III; incremental-reviewable Workflow gate).
- No `SCHEMA_VERSION` bump; additive default-tolerant fields only.
