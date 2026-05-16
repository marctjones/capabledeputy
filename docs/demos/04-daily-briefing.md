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

## Interactive REPL walkthrough

The pytest above proves the architecture. The interactive REPL lets
you live the same flow: ask the agent in natural language, see the
policy enforce, and step through the recovery hatches.

```bash
# Terminal 1
export ANTHROPIC_API_KEY=$(cat CLAUDEAPI.KEY | tr -d '[:space:]')
uv run capdep daemon start

# Terminal 2
uv run capdep demo start daily-briefing
```

The scenario seeds three inbox messages (one friend, one work, one
newsletter), two calendar events, and two memory contacts. Try:

### Step 1 — agent reads inbox + calendar (works)

```
daily briefing> summarise my unread email and what's on my calendar
agent: <a useful summary>
  allow inbox.list labels+=untrusted.external
  allow inbox.read
  allow calendar.events_today labels+=confidential.personal
```

The session has now accumulated `untrusted.external` and
`confidential.personal`. Run `/status` to see them:

```
daily briefing> /status
labels (2): confidential.personal, untrusted.external
used kinds: CALENDAR_READ, READ_FS
capabilities (4):
  - READ_FS pattern=*
  - CALENDAR_READ pattern=*
  - CREATE_CAL pattern=*
  - SEND_EMAIL pattern=*
```

### Step 2 — naive forward fails (correctly)

```
daily briefing> forward the dinner email to my wife
agent: I'll first check policy preview before attempting…
  [agent calls policy.preview → decision=deny rule=untrusted-meets-egress]
agent: I checked — sending email from this session is blocked because we've
       read untrusted content. You can recover via /spawn or /extract.
```

The new system prompt nudges the agent to dry-run via `policy.preview`
rather than just attempting and failing. If you DO ask it to send,
the real call denies with the same rule.

### Step 3a — recovery via `/extract` + `/spawn` (the clean path)

```
daily briefing> /schemas
available declassification schemas:
  - ContactInfo
  - DailyBriefing
  - …
daily briefing> /extract m3 ContactInfo
╭─ declassified: ContactInfo from message m3 ────────────────╮
│ { "name": "Alex", "relationship": "friend" }                │
╰─────────────────────────────────────────────────────────────╯
this result carries no labels — paste into a /spawn-ed clean session.

daily briefing> /spawn email-julie-about-dinner
✓ spawned email-julie-about-dinner (a3f2b1c0, parent=2092fcfe, labels=trusted.user_direct)

email-julie-about-dinner> /grant SEND_EMAIL julie@example.com --one-shot
✓ granted SEND_EMAIL pattern=julie@example.com (one-shot)

email-julie-about-dinner> email julie@example.com that Alex (a friend) wants to grab
  dinner Friday 7pm. Ask if she's in.
agent: Sent.
  allow email.send
```

Three things to notice:

1. `/extract` returned a label-stripped fact (the quarantined LLM
   validated the message body against the `ContactInfo` schema).
2. `/spawn` created a child session with `parent` set to the original,
   labeled only `trusted.user_direct`. No `untrusted.external`.
3. `/grant ... --one-shot` made the SEND_EMAIL capability single-use
   and scoped to exactly one recipient. The cap expires after this
   send, so the agent can't drift into other actions.

### Step 3b — adding the dinner event to your calendar

`CREATE_CAL` is already granted in the daily-briefing scenario. From
either session:

```
daily briefing> add a calendar event "dinner with Alex" friday 7pm to 9pm
agent: Added.
  allow calendar.create_event
```

Adding an event is non-destructive (it's a CREATE) so it bypasses the
destructive-op gate. Modifying or deleting an existing event would
need either `allows_destructive=True` on the capability, or trigger
`destructive-op-needs-approval` → user approves via `/approve` →
purpose session dispatches.

## Files involved

- `tests/test_e2e_daily_briefing.py` — both tests
- `src/capabledeputy/quarantined/schemas.py` — `DailyBriefing` schema
- `src/capabledeputy/tools/native/calendar.py` — calendar tool stub
- `src/capabledeputy/tools/native/inbox.py` — inbox tool stub
- `src/capabledeputy/tools/native/extract.py` — `quarantined.extract` tool
- `src/capabledeputy/cli/chat.py` — REPL slash commands
- `src/capabledeputy/demo/scenarios.py` — daily-briefing seed
