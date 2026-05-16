---
description: "Task list: Time-Bounded Capabilities"
---

# Tasks: Time-Bounded Capabilities

**Input**: Design documents from `specs/001-time-bounded-capabilities/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: REQUIRED. Constitution Principle III (Test-First,
NON-NEGOTIABLE) — every implementation task is preceded by a failing
test that encodes its invariant. No behavioral change (engine, CLI,
toolbar, audit) ships without a preceding red test.

**Organization**: By user story (US1 P1 → US2 P2 → US3 P3). US1 is the
independently shippable MVP.

**Remediation applied** (from `/speckit-analyze`): D1 — added T016
gating `/grant --ttl` before its impl; D2 — T021 explicitly covers the
toolbar render path; E1 — added T008 asserting the persisted audit
event (FR-010/SC-005); F1 — restart-persistence test relocated from
Polish into US1 (T009); C1 — added T024 covering the remaining
capability-inspection surfaces so FR-009 is fully satisfied.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different file, no incomplete dependency)
- Single project layout: `src/capabledeputy/`, `tests/` at repo root

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Record green baseline on branch `001-time-bounded-capabilities`: run `uv run pytest -q` and `uv run ruff check src/ tests/`; confirm 610 passed / clean before any change

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The `expires_at` attribute + its persistence. Every user story depends on the field existing and round-tripping.

**⚠️ CRITICAL**: No user-story work begins until Phase 2 is complete.

- [X] T002 [P] Write failing tests in tests/test_policy_capabilities.py: (a) `Capability` accepts optional `expires_at` (tz-aware UTC `datetime`, default `None`); (b) half-open validity — valid when `expires_at is None or now < expires_at`, expired when `now >= expires_at`; (c) `to_dict`/`from_dict` round-trip preserves `expires_at`; (d) a dict WITHOUT `expires_at` deserializes to `None` (backward-tolerant)
- [X] T003 Add optional `expires_at: datetime | None = None` to the `Capability` dataclass in src/capabledeputy/policy/capabilities.py (UTC; `None` ⇒ never expires — unchanged behavior)
- [X] T004 Extend `Capability.to_dict`/`from_dict` in src/capabledeputy/policy/capabilities.py to (de)serialize `expires_at` as ISO-8601 UTC; absent ⇒ `None`. Do NOT bump `SCHEMA_VERSION` in src/capabledeputy/session/store.py (lives in the capability JSON blob, same pattern as `allows_destructive`/`revoked_by`)
- [X] T005 Run `uv run pytest tests/test_policy_capabilities.py -q` — T002 passes; full suite green (no regression from the additive field)

**Checkpoint**: `expires_at` exists, persists, backward-tolerant.

---

## Phase 3: User Story 1 - Self-expiring grant (Priority: P1) 🎯 MVP

**Goal**: A capability with a past deadline is deterministically inert at the policy chokepoint; the denial is attributed to expiry, audited, and survives a restart.

**Independent Test**: Grant a short-deadline cap; use before (normal), use after (`deny rule=capability-expired`), confirm a non-expired sibling still satisfies, confirm the audit event records it, confirm it still denies after a store reload.

### Tests for User Story 1 (write first, MUST fail) ⚠️

- [X] T006 [P] [US1] Failing tests in tests/test_policy_engine.py: (a) `decide()` accepts an injected `now` used once per decision; (b) a scope-matching cap with `now >= expires_at` is treated as absent; (c) half-open boundary — at `now == expires_at` the cap is expired; (d) `now < expires_at` yields a decision byte-identical to the same cap with `expires_at=None` (C1/C2); (e) a non-expired sibling still satisfies when another matching cap is expired (C5); (f) only-matching caps all expired ⇒ `rule="capability-expired"`, distinct from generic no-capability (C4); (g) expiry composes with one-shot + `revoked_by` (C7)
- [X] T007 [P] [US1] Failing e2e test tests/test_time_bounded_e2e.py: grant short-TTL `QUEUE_PURCHASE` cap → dispatch before deadline (normal) → after (deny, `rule=capability-expired`) → SC-006 invariant: identical with `App(enable_policy_preview=False)`; assert no LLM client on the expiry path
- [X] T008 [P] [US1] Failing audit-assertion test in tests/test_time_bounded_e2e.py (FR-010/SC-005): after an expiry denial, read the audit log and assert the persisted `POLICY_DECIDED` event records `rule=capability-expired` and enough detail (the expired deadline) to reconstruct the denial **from the audit trail alone**, distinct from a no-capability denial
- [X] T009 [P] [US1] Failing restart-persistence test in tests/test_session_store.py (FR-008/SC-004 — relocated from Polish; this is MVP correctness): persist a time-bounded cap, reload via a fresh `SessionStore`, confirm `expires_at` unchanged and a post-deadline dispatch still denies with `rule=capability-expired`

### Implementation for User Story 1

- [X] T010 [US1] Add `now: datetime | None = None` to `decide()` in src/capabledeputy/policy/engine.py; resolve `None` → `datetime.now(UTC)` once at entry (single clock per decision; FR-004)
- [X] T011 [US1] In `decide()` src/capabledeputy/policy/engine.py, skip a scope-matching capability when `cap.expires_at is not None and now >= cap.expires_at` (half-open) — treated as absent (FR-002, C3, C5)
- [X] T012 [US1] Add reason constant `capability-expired` + attribution in src/capabledeputy/policy/engine.py: when only scope-matching caps were disqualified by expiry and none satisfied, deny with `rule="capability-expired"` (else generic denial unchanged); ensure the emitted `POLICY_DECIDED` audit payload carries the expired deadline so T008 passes (FR-003/FR-010, C4); mirror the `capability-revoked-by-prior-use` precedent
- [X] T013 [US1] Thread the decision clock from src/capabledeputy/tools/client.py into `decide()` (one `now` per dispatch; no inline `datetime.now()` in the comparison; FR-004, Principle I)
- [X] T014 [US1] Run `uv run pytest tests/test_policy_engine.py tests/test_time_bounded_e2e.py tests/test_session_store.py -q` — T006–T009 pass; full suite + `uv run ruff check src/ tests/` green

**Checkpoint**: MVP — self-expiring, deterministic, audited, restart-safe, LLM-isolated. Shippable alone.

---

## Phase 4: User Story 2 - Duration helper (Priority: P2)

**Goal**: Operators express a bound as "for N", resolved to an absolute deadline at grant time.

**Independent Test**: Create a cap "for N seconds" (deadline ≈ now+N); `ttl ≤ 0` ⇒ already expired at first use; `/grant --ttl N` produces such a cap.

### Tests for User Story 2 (write first, MUST fail) ⚠️

- [X] T015 [P] [US2] Failing tests in tests/test_policy_capabilities.py: `Capability.expiring_in(kind, pattern, ttl, now=…)` sets `expires_at == now + ttl`; non-positive `ttl` ⇒ `expires_at <= now` (already expired, FR-007)
- [X] T016 [P] [US2] Failing test in tests/test_chat_grant.py (NEW file; D1 — gates the CLI behavior): with `capabledeputy.cli.chat._call` monkeypatched, `/grant QUEUE_PURCHASE amazon --ttl 60` builds a capability dict whose `expires_at ≈ now+60s`; a non-numeric/negative `--ttl` is rejected with a clear error and no capability granted

### Implementation for User Story 2

- [X] T017 [US2] Add `Capability.expiring_in(...)` classmethod in src/capabledeputy/policy/capabilities.py: `expires_at = (now or datetime.now(UTC)) + ttl`; other attrs pass through; absolute deadline is the unit of truth
- [X] T018 [US2] Add `--ttl <seconds>` to `/grant` in src/capabledeputy/cli/chat.py (`_handle_grant`): set the granted cap dict's `expires_at = now + ttl`; reject bad values; show "(expires in Ns)" in the confirmation line — gated by T016
- [X] T019 [P] [US2] Add `--ttl` to `_GRANT_FLAGS` in src/capabledeputy/cli/completer.py so `/grant … --t<TAB>` completes
- [X] T020 [US2] Run `uv run pytest tests/test_policy_capabilities.py tests/test_chat_grant.py -q` — T015/T016 pass; full suite + ruff green

**Checkpoint**: US1 + US2 independently functional.

---

## Phase 5: User Story 3 - Operator visibility (Priority: P3)

**Goal**: Every operator surface that enumerates capabilities distinguishes a time-bounded one and shows remaining/expired state (FR-009 — all inspection views, not just two).

**Independent Test**: Grant a time-bounded cap; `/status`, `/caps`, the bottom toolbar, `/session [id]`, and the `/sessions` table all show it as time-bounded with remaining/expired; non-expiring caps unchanged.

### Tests for User Story 3 (write first, MUST fail) ⚠️

- [X] T021 [P] [US3] Failing tests in tests/test_repl_ui_helpers.py covering all three render paths (D2 + C1 explicit): (a) `_handle_status` (`/status`,`/caps`) annotates a future-deadline cap with remaining time and a past-deadline cap with "expired"; (b) `_make_bottom_toolbar` shows a bound/expired marker for a time-bounded cap (distinct render function — must be asserted, not inferred from (a)); (c) the session-detail/`/sessions` capability renderer annotates likewise; (d) a cap without `expires_at` renders exactly as today in all three

### Implementation for User Story 3

- [X] T022 [US3] Annotate time-bounded capabilities in `_handle_status` (`/status`, `/caps`) in src/capabledeputy/cli/chat.py: append remaining seconds or `expired`
- [X] T023 [US3] Annotate time-bounded capabilities in `_make_bottom_toolbar` in src/capabledeputy/cli/chat.py (live status band) — gated by T021(b)
- [X] T024 [US3] Annotate the capability listing in `_handle_session_show` and the `/sessions` table renderer in src/capabledeputy/cli/chat.py so FR-009 ("operator inspection views", plural) is fully covered, not just `/status`/toolbar (closes C1) — gated by T021(c)
- [X] T025 [US3] Run `uv run pytest tests/test_repl_ui_helpers.py -q` — T021 passes; full suite + ruff green

**Checkpoint**: All three stories independently functional; FR-009 fully covered.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T026 [P] Add demo walkthrough docs/demos/19-time-bounded-capabilities.md: `/grant … --ttl` → use → expire → `capability-expired`, plus the SC-006 `--no-policy-preview` invariant; update docs/demos/README.md table
- [X] T027 Execute quickstart.md steps 1–7 (live daemon) or confirm each step's automated equivalent passes; tick the spec checklist
- [X] T028 Final sweep: `uv run pytest -q` (610 + new tests, all green) and `uv run ruff check src/ tests/` clean; set spec.md/plan.md Status to "Implemented"

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (P1)**: none
- **Foundational (P2)**: after Setup — BLOCKS all user stories (the `expires_at` field + serialization)
- **US1 (Phase 3)**: after Foundational — MVP; no dependency on US2/US3. Now includes the audit (T008) and restart (T009) tests so the MVP is provably correct, not just functional
- **US2 (Phase 4)**: after Foundational — independent of US1
- **US3 (Phase 5)**: after Foundational — independent (read-only display)
- **Polish (P6)**: after the desired stories complete

### Within Each Story

- The failing-test task(s) precede implementation (Principle III). Every behavioral surface — engine, audit emission, CLI grant, all three display paths — has a preceding red test (D1/D2 closed).
- Each story ends with a green-suite + ruff gate.

### Parallel Opportunities

- T002 `[P]` (foundational tests).
- US1: T006–T009 are `[P]` (test_policy_engine, test_time_bounded_e2e ×2, test_session_store — different files) and all must fail before T010–T013.
- US2: T015‖T016 `[P]` (different test files); T019 `[P]` (completer.py) parallel with T018 (chat.py).
- US3: T021 is one `[P]` test task; T022/T023/T024 touch the same file (chat.py) → sequential.
- Polish: T026 `[P]` independent.
- US1/US2/US3 phases parallelizable across developers once Foundational done.

## Parallel Example: User Story 1

```bash
# All four US1 failing-test suites together (different files):
Task: "T006 engine tests in tests/test_policy_engine.py"
Task: "T007 e2e in tests/test_time_bounded_e2e.py"
Task: "T008 audit assertion in tests/test_time_bounded_e2e.py"
Task: "T009 restart persistence in tests/test_session_store.py"
# Then T010→T013 (engine.py sequential; T013 in client.py).
```

## Implementation Strategy

### MVP First (US1 only)

Setup → Foundational → US1 (now includes audit + restart proofs) →
STOP & VALIDATE → demo/ship. The MVP is provably correct under
restart and auditable, not merely functional.

### Incremental Delivery

Foundational → US1 (MVP, ship) → US2 (duration sugar, ship) →
US3 (full operator visibility, ship) → Polish.

## Notes

- `[P]` = different file, no incomplete dependency.
- Every implementation task is gated by a preceding failing test
  (Principle III, NON-NEGOTIABLE) — including CLI (T016→T018) and the
  toolbar render path (T021b→T023), which the analysis flagged.
- Single injected decision clock; never inline `datetime.now()` in the
  comparison (Principle I determinism).
- No `SCHEMA_VERSION` bump (backward-tolerant-read constraint).
- Commit after each green checkpoint.
