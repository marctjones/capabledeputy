# Demo 20: Rate-Limited Capabilities

**Audience:** anyone who wants authority that throttles itself.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync` (deterministic, no API key).

A capability can carry a sliding-window use limit: at most `max_uses`
dispatches within any trailing `window_seconds`. The policy engine
counts prior uses (recorded per-session-per-capability at the
chokepoint) and denies the over-rate call with the dedicated rule
`rate-limit-exceeded` — distinct from `capability-expired` and from
"never had it" so audits separate the three. Enforcement is
deterministic and LLM-isolated; the use log persists across restart.

This completes the v0.7 capability-constraint family:

| Constraint | Keys off | Rule |
|---|---|---|
| destructive-op gate | action kind | `destructive-op-needs-approval` |
| prior-use revocation | what was used | `capability-revoked-by-prior-use` |
| time bound (#51) | wall clock | `capability-expired` |
| **rate limit (#52)** | **use frequency** | **`rate-limit-exceeded`** |

All four compose independently — any single disqualifier makes a
capability unusable; a still-usable sibling can satisfy the action.

## What the demo proves

1. The first `max_uses` dispatches in a window allow normally; the
   next is denied with `rate-limit-exceeded`.
2. The window **slides**: uses older than `window_seconds` no longer
   count, so the capability frees up again.
3. The use log is keyed by `audit_id`, persisted, and **survives a
   runtime restart** (you can't reset the limit by bouncing the
   daemon).
4. Attribution is distinct from expiry / no-capability (SC-style
   audit separation).
5. A non-rate-limited sibling matching the same action still
   satisfies it (rate-exhausted is inert, not poisonous).
6. Byte-identical with `--no-policy-preview`; no LLM on the path.

## Walkthrough (automated, deterministic)

```bash
uv run pytest tests/test_rate_limit_e2e.py \
              tests/test_policy_engine.py \
              tests/test_policy_capabilities.py -q
```

- `test_rate_limit_e2e.py` — N allowed → N+1 denied through the real
  tool client; log pruned to the window; persists across a fresh
  `SessionStore`; identical with preview disabled.
- `test_policy_engine.py` — under/at limit, window slide, attribution
  distinct from expired/no-cap, sibling survival, expiry-precedence.
- `test_policy_capabilities.py` — `RateLimit` serialization, in-window
  counting, backward-tolerant `from_dict`.

## Live REPL

```bash
uv run capdep daemon start          # one terminal
uv run capdep demo start accountant # another
```

```
accountant> /grant QUEUE_PURCHASE amazon --rate 3/3600
✓ granted QUEUE_PURCHASE pattern=amazon (rate 3/3600s)

accountant> /caps
  - QUEUE_PURCHASE pattern=amazon (rate 3/3600s)
```

Drive 3 matching purchases — all allowed. The 4th within the hour:
`deny rate-limit-exceeded`. `/status`, `/caps`, and `/session`
annotate the limit (`--rate` also TAB-completes after `/grant`).

`--rate` validates `MAX/WINDOW`, both > 0 (`--rate 0/60` is rejected,
nothing granted).

## How it works

- `RateLimit(max_uses, window_seconds)` on `Capability`; `None` ⇒
  unlimited (prior behavior). Serialized in the capability JSON blob.
- `Session.cap_uses: {audit_id: (timestamp, …)}` — per-capability use
  log. New store column `cap_uses` (schema **v3 → v4**, idempotent
  migration; old rows default `{}`).
- `LabeledToolClient` records the matched capability's use at the
  single dispatch clock and prunes the log to the window.
- `decide()` resolves one clock, skips a matching cap whose in-window
  count has reached `max_uses`, and attributes the denial to
  `rate-limit-exceeded` (expiry takes precedence when both apply).

## Files

- `src/capabledeputy/policy/capabilities.py` — `RateLimit`,
  `Capability.rate_limit`, `is_rate_exceeded`, serialization
- `src/capabledeputy/policy/engine.py` — `RATE_LIMIT_EXCEEDED_RULE`,
  skip + attribution
- `src/capabledeputy/session/{model,store,graph}.py` — `cap_uses`
  field, schema v4, `record_cap_use`
- `src/capabledeputy/tools/client.py` — record-on-dispatch + prune
- `src/capabledeputy/cli/chat.py` — `/grant --rate`, `_rate_marker`
