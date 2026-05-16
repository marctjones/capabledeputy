# Phase 0 Research: Time-Bounded Capabilities

No `NEEDS CLARIFICATION` items: the feature is a scoped tracker item
(#51) on a well-understood codebase. The "research" here is the set
of design decisions that resolve the spec's edge cases against the
existing architecture.

## Decision 1: Where expiry is evaluated

- **Decision**: Inside the existing `decide()` pure function in
  `policy/engine.py`, at the point where a candidate capability is
  matched against the action. An expired capability is skipped as if
  it did not match.
- **Rationale**: `decide()` is the single deterministic chokepoint
  already run unconditionally at every dispatch (`LabeledToolClient`).
  Putting expiry anywhere else would create a second enforcement
  surface and risk divergence. Satisfies FR-002, FR-004.
- **Alternatives rejected**: (a) Filtering expired capabilities at
  grant/lookup time — fails the decision-time-evaluation edge case
  (a cap valid at grant but lapsed before use must deny at use).
  (b) A pre-dispatch sweep that deletes expired caps — mutates state
  as a side effect of a read; breaks reproducibility/audit.

## Decision 2: Clock source

- **Decision**: `decide()` accepts an injected `now` (timezone-aware
  UTC `datetime`) defaulting to the real current time. The
  `LabeledToolClient` passes the same value it uses for the rest of
  the decision so one decision has one consistent "now."
- **Rationale**: Determinism and testability without monkeypatching
  global wall-clock. Resolves the "two evaluations microseconds apart"
  edge case (Decision Clock entity).
- **Alternatives rejected**: Reading `datetime.now()` inline at the
  comparison — untestable at the boundary instant and non-injectable.

## Decision 3: Boundary semantics

- **Decision**: Half-open window. A capability is valid for `now <
  expires_at` and expired for `now >= expires_at`.
- **Rationale**: Matches the spec edge case ("valid up to but not
  including the deadline") and the common TTL convention; makes
  "duration 0 ⇒ already expired" fall out naturally (FR-007).

## Decision 4: Persistence

- **Decision**: Serialize the deadline as an ISO-8601 UTC string in
  the existing capability JSON blob. `Capability.to_dict` adds the
  key; `Capability.from_dict` reads it with a tolerant default
  (absent ⇒ no expiry).
- **Rationale**: The session store persists capabilities as a JSON
  array; the codebase already evolves capability shape this way
  (`allows_destructive`, `revoked_by` were added the same way with
  no DDL). No `SCHEMA_VERSION` bump or migration SQL required; old
  rows load as non-expiring. Satisfies FR-008.
- **Alternatives rejected**: A dedicated SQL column — unnecessary DDL
  + migration for a value already inside the JSON blob; inconsistent
  with how every other capability attribute is stored.

## Decision 5: Denial attribution

- **Decision**: New reason rule constant `capability-expired`. When an
  action would have matched a capability that is expired, and no
  non-expired capability matches, the `PolicyDecision` denies with
  `rule = "capability-expired"` rather than the generic
  no-capability denial.
- **Rationale**: FR-003 / SC-005 require audits to distinguish
  "expired" from "never had it." Mirrors the existing
  `capability-revoked-by-prior-use` precedent (same shape: a
  capability that *would* match is disqualified for a stated reason).
- **Alternatives rejected**: Reusing the generic no-match reason —
  fails SC-005 (auditors can't tell why).

## Decision 6: Composition order with one-shot and revocation

- **Decision**: A capability is usable iff it (a) scope/pattern
  matches, (b) is not consumed (one-shot), (c) is not revoked by
  prior use, **and** (d) is not expired — all simultaneously.
  Evaluation order among the disqualifiers is irrelevant to the
  outcome; for attribution, expiry is reported when expiry is the
  reason a would-match capability was rejected and nothing else
  satisfied the action.
- **Rationale**: FR-011. Keeps each constraint independent; no
  constraint silently overrides another.
- **Alternatives rejected**: Special-casing precedence between expiry
  and revocation — adds ordering complexity with no behavioral
  benefit (an unusable capability is unusable regardless of which
  condition tripped first).

## Decision 7: Duration helper

- **Decision**: A constructor/classmethod-style helper on
  `Capability` (e.g. "expiring in `timedelta`") computes
  `expires_at = created_now + duration` using the same UTC clock
  convention. Non-positive duration ⇒ `expires_at <= now` ⇒ already
  expired by the half-open rule (FR-007).
- **Rationale**: FR-006/FR-007; keeps absolute deadline as the single
  unit of truth (duration is sugar resolved at creation).

## Decision 8: Operator visibility

- **Decision**: `/status`, `/caps`, and the bottom toolbar annotate a
  time-bounded capability with remaining time, or "expired". Reuses
  the existing capability rendering path; additive only.
- **Rationale**: FR-009 / User Story 3. No new view.

## Decision 9: LLM-isolation invariant test

- **Decision**: An e2e test asserts expiry enforcement is identical
  with the policy-introspection tool absent entirely (daemon started
  `enable_policy_preview=False`), and that no LLM client is on the
  expiry path.
- **Rationale**: SC-006; converts the architectural principle into a
  CI-enforced guarantee, matching the precedent set by
  `test_policy_preview_toggle.py::test_enforcement_unchanged_when_preview_disabled`.

**Output**: all spec edge cases and FRs have a resolved design
decision. Ready for Phase 1.
