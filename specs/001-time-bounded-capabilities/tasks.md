---
description: "Task list: Time-Bounded Capabilities"
---

# Tasks: Time-Bounded Capabilities

**Input**: Design documents from `specs/001-time-bounded-capabilities/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED. Constitution Principle III (Test-First,
NON-NEGOTIABLE) and the spec's acceptance scenarios + quickstart make
tests mandatory. Every implementation task is preceded by a failing
test that encodes its invariant.

**Organization**: By user story (US1 P1 → US2 P2 → US3 P3). US1 is the
independently shippable MVP.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different file, no incomplete dependency)
- Single project layout: `src/capabledeputy/`, `tests/` at repo root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish a known-green baseline before TDD.

- [ ] T001 Record green baseline on branch `001-time-bounded-capabilities`: run `uv run pytest -q` and `uv run ruff check src/ tests/`; confirm 610 passed / clean before any change

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `expires_at` attribute + its persistence. Every user
story depends on the field existing and round-tripping.

**⚠️ CRITICAL**: No user-story work begins until Phase 2 is complete.

- [ ] T002 [P] Write failing tests in tests/test_policy_capabilities.py: (a) `Capability` accepts optional `expires_at` (tz-aware UTC `datetime`, default `None`); (b) half-open validity helper — valid when `expires_at is None or now < expires_at`, expired when `now >= expires_at`; (c) `to_dict`/`from_dict` round-trip preserves `expires_at`; (d) a dict WITHOUT `expires_at` deserializes to `expires_at=None` (backward-tolerant)
- [ ] T003 Add optional `expires_at: datetime | None = None` field to the `Capability` dataclass in src/capabledeputy/policy/capabilities.py (UTC, `None` ⇒ never expires — today's behavior unchanged)
- [ ] T004 Extend `Capability.to_dict` / `Capability.from_dict` in src/capabledeputy/policy/capabilities.py to (de)serialize `expires_at` as ISO-8601 UTC; absent key ⇒ `None`. Do NOT bump `SCHEMA_VERSION` in src/capabledeputy/session/store.py (value lives in the capability JSON blob, same pattern as `allows_destructive`/`revoked_by`)
- [ ] T005 Run `uv run pytest tests/test_policy_capabilities.py -q` — T002 tests pass; run full suite — still green (no regression from the additive field)

**Checkpoint**: `expires_at` exists, persists, and is backward-tolerant. User stories can begin.

---

## Phase 3: User Story 1 - Self-expiring grant (Priority: P1) 🎯 MVP

**Goal**: A capability with a past deadline is deterministically inert
at the policy chokepoint; the denial is attributed to expiry.

**Independent Test**: Grant a cap with a short deadline, use it before
(normal decision), use it after (deny `rule=capability-expired`),
confirm a non-expired sibling still satisfies.

### Tests for User Story 1 (write first, MUST fail) ⚠️

- [ ] T006 [P] [US1] Write failing tests in tests/test_policy_engine.py: (a) `decide()` accepts an injected `now` and uses one value per decision; (b) a scope-matching capability with `now >= expires_at` is treated as absent; (c) half-open boundary — at exactly `now == expires_at` the cap is expired; (d) `now < expires_at` yields a decision byte-identical to the same cap with `expires_at=None` (C1/C2); (e) a non-expired sibling still satisfies the action when another matching cap is expired (C5); (f) when only-matching caps are all expired the denial is `rule="capability-expired"`, distinct from the generic no-capability denial (C4); (g) expiry composes with one-shot and `revoked_by` — any single disqualifier makes the cap unusable (C7)
- [ ] T007 [P] [US1] Write failing e2e test tests/test_time_bounded_e2e.py: grant a short-TTL `QUEUE_PURCHASE` cap → dispatch matching action before deadline (normal outcome) → after deadline (deny, `rule=capability-expired`) → SC-006 invariant: identical behavior with `App(enable_policy_preview=False)` and assert no LLM client is on the expiry path

### Implementation for User Story 1

- [ ] T008 [US1] Add `now: datetime | None = None` parameter to `decide()` in src/capabledeputy/policy/engine.py; resolve `None` → `datetime.now(UTC)` once at entry so the whole decision uses a single clock value
- [ ] T009 [US1] In `decide()` src/capabledeputy/policy/engine.py, when evaluating a scope-matching capability, skip it if `cap.expires_at is not None and now >= cap.expires_at` (half-open) — an expired match is treated as if the capability were absent (FR-002, C3)
- [ ] T010 [US1] Add reason constant `capability-expired` and attribution in src/capabledeputy/policy/engine.py: if every scope-matching capability was disqualified solely by expiry and none satisfied the action, return `deny` with `rule="capability-expired"`; otherwise the existing generic no-capability denial is unchanged (FR-003, C4) — mirror the existing `capability-revoked-by-prior-use` precedent
- [ ] T011 [US1] Thread the decision clock from src/capabledeputy/tools/client.py: pass the same `now` into `decide()` that the rest of the dispatch uses; remove any inline `datetime.now()` from the comparison path (deterministic, testable; FR-004)
- [ ] T012 [US1] Run `uv run pytest tests/test_policy_engine.py tests/test_time_bounded_e2e.py -q` — T006/T007 pass; run full suite + `uv run ruff check src/ tests/` — green

**Checkpoint**: MVP — capabilities self-expire deterministically, LLM-isolated, audited. Shippable on its own.

---

## Phase 4: User Story 2 - Duration helper (Priority: P2)

**Goal**: Operators express a bound as "for N", resolved to an
absolute deadline at grant time.

**Independent Test**: Create a cap "for N seconds", confirm its
deadline ≈ now+N; create one with ttl ≤ 0, confirm it is already
expired at first use.

### Tests for User Story 2 (write first, MUST fail) ⚠️

- [ ] T013 [P] [US2] Write failing tests in tests/test_policy_capabilities.py: `Capability.expiring_in(kind, pattern, ttl, now=…)` sets `expires_at == now + ttl`; a non-positive `ttl` yields `expires_at <= now` (already expired by the half-open rule, FR-007)

### Implementation for User Story 2

- [ ] T014 [US2] Add `Capability.expiring_in(...)` classmethod in src/capabledeputy/policy/capabilities.py: `expires_at = (now or datetime.now(UTC)) + ttl`; all other attributes pass through; absolute deadline is the unit of truth (duration is sugar)
- [ ] T015 [US2] Add `--ttl <seconds>` to `/grant` in src/capabledeputy/cli/chat.py (`_handle_grant`): when present, set the granted capability dict's `expires_at` to `now + ttl`; show "(expires in Ns)" in the confirmation line
- [ ] T016 [P] [US2] Add `--ttl` to `_GRANT_FLAGS` in src/capabledeputy/cli/completer.py so `/grant … --t<TAB>` completes it
- [ ] T017 [US2] Run `uv run pytest tests/test_policy_capabilities.py -q` — T013 passes; full suite + ruff green

**Checkpoint**: US1 + US2 both independently functional.

---

## Phase 5: User Story 3 - Operator visibility (Priority: P3)

**Goal**: Operator surfaces distinguish a time-bounded capability and
show remaining/expired state.

**Independent Test**: Grant a time-bounded cap; `/status`, `/caps`,
and the bottom toolbar show it as time-bounded with remaining time;
a non-expiring cap shows unchanged.

### Tests for User Story 3 (write first, MUST fail) ⚠️

- [ ] T018 [P] [US3] Write failing tests in tests/test_repl_ui_helpers.py: a capability dict with a future `expires_at` renders with a remaining-time annotation; with a past `expires_at` renders "expired"; a capability without `expires_at` renders exactly as today (no annotation)

### Implementation for User Story 3

- [ ] T019 [US3] Annotate time-bounded capabilities in `_handle_status` (covers `/status` and `/caps`) in src/capabledeputy/cli/chat.py: append remaining seconds or `expired`
- [ ] T020 [US3] Annotate time-bounded capabilities in `_make_bottom_toolbar` in src/capabledeputy/cli/chat.py so the live status band shows a bound/expired marker (reads the cache's `capability_set`, already available)
- [ ] T021 [US3] Run `uv run pytest tests/test_repl_ui_helpers.py -q` — T018 passes; full suite + ruff green

**Checkpoint**: All three user stories independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T022 [P] Write restart-persistence test in tests/test_session_store.py: persist a time-bounded capability, reload via a fresh `SessionStore`, confirm `expires_at` is unchanged and a post-deadline use still denies with `rule=capability-expired` (FR-008, SC-004)
- [ ] T023 [P] Add demo walkthrough docs/demos/19-time-bounded-capabilities.md: `/grant … --ttl` → use before → expire → `capability-expired`, plus the SC-006 `--no-policy-preview` invariant; update docs/demos/README.md table
- [ ] T024 Execute quickstart.md steps 1–7 (live daemon) or confirm each step's automated equivalent passes; tick the spec checklist
- [ ] T025 Final sweep: `uv run pytest -q` (expect 610 + new tests, all green) and `uv run ruff check src/ tests/` clean; update spec.md/plan.md Status to "Implemented"

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: none — start immediately
- **Foundational (P2)**: after Setup — BLOCKS all user stories (the `expires_at` field + serialization)
- **US1 (P3 phase)**: after Foundational — the MVP; no dependency on US2/US3
- **US2 (P4)**: after Foundational — independent of US1 (adds a constructor + CLI flag over the same field)
- **US3 (P5)**: after Foundational — independent (read-only display of the same field)
- **Polish (P6)**: after the desired stories complete

### User Story Independence

- US1, US2, US3 each depend only on Foundational, not on each other. US1 alone is a coherent shippable increment (capabilities self-expire and enforce deterministically) even without the duration sugar (US2) or the display (US3).

### Within Each Story

- The failing test task precedes its implementation tasks (Principle III). Field/model before engine before CLI. Each story ends with a green-suite + ruff gate.

### Parallel Opportunities

- T002 (foundational test) is `[P]` — single new test file section.
- Within US1: T006 and T007 are `[P]` (different test files) and must both fail before T008–T011.
- T016 (`[P]`, completer) parallel with T015 (chat.py) only if different files — they are; safe.
- T022 and T023 (`[P]`) are independent polish items (different files).
- US1/US2/US3 phases can be executed by different developers in parallel once Foundational is done.

## Parallel Example: User Story 1

```bash
# Write both failing test suites together (different files):
Task: "T006 failing engine tests in tests/test_policy_engine.py"
Task: "T007 failing e2e test in tests/test_time_bounded_e2e.py"
# Then implement T008→T011 sequentially (same file: engine.py), T011 in client.py.
```

## Implementation Strategy

### MVP First (US1 only)

1. Phase 1 Setup → 2. Phase 2 Foundational → 3. Phase 3 US1 →
4. STOP & VALIDATE: capabilities self-expire, deny is attributed,
   invariant holds with preview off → 5. Demo/ship.

### Incremental Delivery

Foundational → US1 (MVP, ship) → US2 (duration sugar, ship) →
US3 (operator visibility, ship) → Polish. Each story adds value
without breaking the prior.

## Notes

- `[P]` = different file, no incomplete dependency.
- Every implementation task is gated by a preceding failing test
  (Principle III, NON-NEGOTIABLE).
- Single decision clock injected into `decide()` — never inline
  `datetime.now()` in the comparison (Principle I determinism).
- No `SCHEMA_VERSION` bump (Constraint: backward-tolerant reads).
- Commit after each green checkpoint.
