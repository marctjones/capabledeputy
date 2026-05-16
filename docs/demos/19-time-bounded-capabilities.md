# Demo 19: Time-Bounded Capabilities

**Audience:** anyone who wants authority that cleans itself up.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync` (no API key — deterministic).

A capability can carry an absolute expiry deadline. Once the deadline
passes, the policy engine deterministically treats it as absent and
denies with the dedicated rule `capability-expired` — distinct from a
"never had it" denial so the audit trail can tell them apart. The
operator grants a bound the human way ("for N seconds"); the runtime
resolves it to an absolute deadline. Enforcement is at the single
deterministic chokepoint, fully isolated from the LLM.

This is the v0.7 follow-on to tool-identity revocation: revocation
keys off *what was used*, expiry keys off *time*. They compose — any
single disqualifier makes a capability unusable.

## What the demo proves

1. A future-deadline capability decides **byte-identically** to a
   non-expiring one (it is transparent until it lapses).
2. A past-deadline capability is **inert**: skipped as if absent. A
   non-expired sibling matching the same action still satisfies it —
   expired is inert, not poisonous.
3. The half-open boundary: valid for `now < expires_at`, expired at
   exactly `now == expires_at`.
4. An expiry denial is attributed to **`capability-expired`** and the
   reason carries the lapsed deadline — reconstructable from the audit
   trail alone (FR-010 / SC-005).
5. The deadline is **absolute**: it survives a runtime restart and
   still denies past the original time (FR-008 / SC-004).
6. **LLM isolation invariant**: enforcement is byte-identical with the
   `policy.preview` tool absent entirely (`--no-policy-preview`), and
   no LLM client is on the expiry path (SC-006).

## Walkthrough (automated, deterministic)

```bash
uv run pytest tests/test_time_bounded_e2e.py \
              tests/test_policy_engine.py \
              tests/test_policy_capabilities.py \
              tests/test_session_store.py -q
```

These encode the quickstart steps with an **injected clock** (no
real wall-clock sleeps, no real LLM):

- `test_policy_engine.py` — half-open boundary, expired-as-absent,
  sibling survival, expiry-vs-no-capability attribution, composition
  with `revoked_by`.
- `test_time_bounded_e2e.py` — grant → use before (allow) → use after
  (deny `capability-expired`) → audit-trail assertion → the
  `--no-policy-preview` SC-006 invariant.
- `test_session_store.py` — deadline survives a fresh `SessionStore`
  over the same DB (restart) and still denies.
- `test_policy_capabilities.py` — `expires_at` field, `is_expired`
  half-open, `Capability.expiring_in`, serialization round-trip,
  backward-tolerant `from_dict`.

## Live REPL

```bash
uv run capdep daemon start          # one terminal
uv run capdep demo start accountant # another
```

```
accountant> /grant QUEUE_PURCHASE amazon --ttl 60
✓ granted QUEUE_PURCHASE pattern=amazon (expires in 60s)

accountant> /caps
  - QUEUE_PURCHASE pattern=amazon (expires in 59s)

# …within the minute the action decides normally…
# …after 60s:

accountant> /caps
  - QUEUE_PURCHASE pattern=amazon (expired)
```

Drive a matching purchase after the minute: the trace shows
`deny capability-expired`, and the bottom toolbar's `caps` segment
shows the `ttl` marker (yellow while bounded, red once expired).
`/status` and `/session <id>` annotate identically — every operator
inspection surface (FR-009).

`--ttl 0` (or negative) yields an already-expired capability at first
use (half-open rule).

## How it works

- `Capability` gains an optional `expires_at` (tz-aware UTC). `None`
  ⇒ never expires (prior behavior unchanged).
- `decide()` resolves **one** clock value per decision (injected,
  defaults to UTC now — never read inline) and skips a matching cap
  when `now >= expires_at`. If the only scope-matching caps are
  expired, it denies with `rule="capability-expired"` and a reason
  naming the deadline; otherwise the generic no-capability denial is
  unchanged.
- Serialized inside the capability JSON blob (ISO-8601 UTC) with a
  tolerant default — **no `SCHEMA_VERSION` bump**, old rows load as
  non-expiring.
- `LabeledToolClient` threads a single `now` into `decide()` at the
  chokepoint.

## Files

- `src/capabledeputy/policy/capabilities.py` — `expires_at`,
  `is_expired`, `expiring_in`, serialization
- `src/capabledeputy/policy/engine.py` — `CAPABILITY_EXPIRED_RULE`,
  injected clock, expired-as-absent + attribution
- `src/capabledeputy/tools/client.py` — single decision clock
- `src/capabledeputy/cli/chat.py` — `/grant --ttl`, `_expiry_marker`,
  `/status` `/caps` `/session` + toolbar annotation
- `specs/001-time-bounded-capabilities/` — spec, plan, tasks,
  contracts (this feature was built spec-first via Spec Kit)
