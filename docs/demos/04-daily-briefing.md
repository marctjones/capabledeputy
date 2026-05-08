# Demo 4: Daily Briefing (Schema-Validated Aggregation)

**Audience:** anyone asking "what's this *for*?" — the assistant
workflow most users actually want.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`.

This demo shows the most-requested AI-assistant workflow with the full
information-flow story applied: a daily briefing built from calendar +
inbox + notes, where the planner never sees the raw labeled bytes.

## What the demo proves

1. **Schema extraction is the declassification gate.** The planner LLM
   sees a typed `DailyBriefing` (date, event count, top priority, focus
   suggestion) — never the raw calendar entries or inbound message
   bodies.
2. **The naive path fails closed.** Reading inbox + calendar directly
   into a session and trying to email the result back to yourself is
   blocked by `untrusted-meets-egress`.
3. **A counter-example demonstrates *why* you'd use schemas.** The
   second test in the file runs the naive flow and asserts the deny.

## Walkthrough

```bash
uv run pytest tests/test_e2e_daily_briefing.py -v
```

Two tests pass.

### The schema-extraction path

A sync job (out-of-band) populates `briefing.source` in labeled memory
with the day's combined calendar + inbox + notes content. The agent:

```python
# Planner turn 1
call quarantined.extract(
    key="briefing.source",
    schema="DailyBriefing"
) -> {
    "found": True,
    "schema": "DailyBriefing",
    "data": {
        "date": "2026-05-07",
        "n_calendar_events": 3,
        "n_unread_emails": 5,
        "top_priority": "1:1 with Maria at 10am",
        "suggested_focus": "ship the migration; ..."
    }
}
```

The quarantined LLM ran with no tools and a Pydantic `DailyBriefing`
schema. Output was schema-validated. The planner LLM sees the
*structured fields*, never the raw briefing source.

The test asserts directly that the planner's recorded second-turn
context contains `"1:1 with Maria"` (came in via the schema) but does
**not** contain inbound senders like `"alice@example.com"` or raw
text fragments like `"CALENDAR"` from the source.

### The naive counter-example

```python
# Reading inbox + email back to self → blocked
inbox.list  -> session gains untrusted.external
email.send(to="me@example.com", ...)  -> DENIED
  rule="untrusted-meets-egress"
```

The session that pulled inbox content can't egress to anyone, even
the user themselves. This is *correct* — once `untrusted.external` is
in scope, inline routing of that content is exactly the prompt-injection
vector we structurally prevent.

## What this demonstrates

- **The "useful assistant" workflow lives at the right altitude.**
  The schema is small enough to be auditable, large enough to capture
  the briefing's value.
- **Aggregation through a schema is safer than aggregation through
  the planner.** The planner sees structured fields with bounded
  lengths; the raw labeled bytes are inaccessible to it by
  construction.
- **The architecture biases users toward safe idioms.** The naive path
  doesn't fail silently — it fails with a clear policy decision the
  user can read.

## Files involved

- `tests/test_e2e_daily_briefing.py` — both tests
- `src/capabledeputy/quarantined/schemas.py` — `DailyBriefing` schema
- `src/capabledeputy/tools/native/calendar.py` — calendar tool stub
- `src/capabledeputy/tools/native/inbox.py` — inbox tool stub
- `src/capabledeputy/tools/native/extract.py` — `quarantined.extract` tool
