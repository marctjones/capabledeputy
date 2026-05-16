# Quickstart: Time-Bounded Capabilities

Validates the feature end-to-end once implemented. Each step maps to
a contract guarantee / success criterion.

## Prerequisites

- Daemon running: `uv run capdep daemon start`
- A demo session: `uv run capdep demo start accountant`

## 1. Grant a capability that expires in 1 minute (FR-006, US2)

```
accountant> /grant QUEUE_PURCHASE amazon --ttl 60
✓ granted QUEUE_PURCHASE pattern=amazon (expires in 60s)
```

`/status` shows it as time-bounded with remaining seconds (FR-009).

## 2. Use it before the deadline → normal behavior (C2, SC-002)

Within the minute, drive an action that matches it. The decision is
exactly what it would be for a non-expiring capability (allow, or the
usual approval gate for purchases). Confirm via `/audit --full` that
the `rule` is the normal one, **not** `capability-expired`.

## 3. Use it after the deadline → deterministic deny (C3/C4, SC-001/05)

Wait past 60s (`/status` now shows `expired`). Drive the same action.

Expected: `deny`, `rule=capability-expired`, with a reason naming the
lapsed deadline. The bottom toolbar / `/audit` reflect it. 100% of
post-deadline attempts deny (SC-001); attribution is `capability-expired`,
never the generic no-capability denial (SC-005).

## 4. Sibling survival (C5, FR-005, US1-S3)

Grant a second, non-expiring `QUEUE_PURCHASE amazon` cap, let the
TTL one lapse, retry: the action is allowed via the non-expired
sibling. An expired capability is inert, not poisonous.

## 5. Restart persistence (FR-008, SC-004)

Grant a `--ttl 3600` cap, `Ctrl-C` the daemon, restart it, reload the
session. The deadline is unchanged (absolute, not relative to process
start); using it past the original hour still denies with
`capability-expired`.

## 6. Determinism / LLM isolation (C6, SC-006)

Restart the daemon with `--no-policy-preview`. Repeat steps 2–3.
Behavior is byte-identical: expiry enforcement does not depend on the
language model or on the policy-introspection tool existing. The
automated invariant test
(`tests/test_time_bounded_e2e.py`) encodes this so CI enforces it.

## 7. Zero/negative duration (FR-007)

`/grant READ_FS * --ttl 0` → the very next matching use denies with
`capability-expired` (already expired by the half-open rule).

## Automated equivalents

Every step above has a deterministic test (no real LLM, injected
clock) under `tests/`:

- `test_policy_capabilities.py` — field, `matches()` skip,
  serialization round-trip, `expiring_in` helper, ttl<=0
- `test_policy_engine.py` — C1–C5/C7, half-open boundary instant
- `test_session_store.py` — C-restart (deadline survives reload)
- `test_time_bounded_e2e.py` — full grant→use→lapse→deny + SC-006
  invariant with preview disabled
