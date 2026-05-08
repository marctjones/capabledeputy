# Demo 8: Note-Taking with Labeled Memory

**Audience:** people asking how compartments hold up *across sessions*
when both kinds of data live in the same store.
**Time:** ~2 minutes.
**Requires:** nothing beyond `uv sync`.

Personal AI assistants accumulate notes — grocery lists, lab results,
meeting summaries, financial planning. The architecture's promise is
that reading one *kind* of note doesn't taint a session with labels
from a *different* kind, even when both live in the same memory store.

## What the demo proves

1. **Compartments stay separate.** A session that reads only grocery
   notes never gains a health label, even when health notes exist in
   the same memory store.
2. **Egress works for the right compartment.** A personal-only session
   can email recommendations; the same code path is denied when health
   notes are in scope.

## Walkthrough

```bash
uv run pytest tests/test_e2e_notes.py -v
```

Two tests pass.

### Test 1 — compartment hygiene

```python
app.memory.write("notes.grocery", "milk, eggs, bread",
                 frozenset({Label.CONFIDENTIAL_PERSONAL}))
app.memory.write("notes.lab-results", "BP 120/80; LDL 110",
                 frozenset({Label.CONFIDENTIAL_HEALTH}))
```

A session reads `notes.grocery` only:

```python
final_personal = app.graph.get(personal_session_id)
assert Label.CONFIDENTIAL_PERSONAL in final_personal.label_set
assert Label.CONFIDENTIAL_HEALTH not in final_personal.label_set
```

A *different* session reads `notes.lab-results` only:

```python
final_health = app.graph.get(health_session_id)
assert Label.CONFIDENTIAL_HEALTH in final_health.label_set
assert Label.CONFIDENTIAL_PERSONAL not in final_health.label_set
```

Both sessions read from the same `LabeledMemoryStore`. Each session
inherited only the labels of the keys it touched. **Reads of unrelated
keys do not contaminate.**

### Test 2 — egress per compartment

A personal-only session sending a book recommendation works:

```python
personal session:
  memory.read("notes.book-rec")  → allow, gains personal label
  email.send(to="friend@example.com", ...)  → allow
```

The exact same prompt with health data in scope fails:

```python
health session:
  memory.read("notes.med-list")  → allow, gains health label
  email.send(to="friend@example.com", ...)  → deny
                                  rule="health-meets-egress"
```

The outbox holds exactly one email — from the personal session. The
health session's attempt was structurally blocked.

## What this demonstrates

- **Sessions are the unit of compartmentalization.** Memory is shared;
  what differs is which session reads which keys, and therefore which
  labels propagate where.
- **The same workflow code can be safe in one session and unsafe in
  another.** That's the right shape — the data, not the logic, drives
  the policy decision.
- **Compartments are easy to stay inside.** No special syntax, no
  separate stores. Just don't read keys you don't need; the labels do
  the rest.

## Files involved

- `tests/test_e2e_notes.py`
- `src/capabledeputy/tools/native/memory.py` — `memory.read` /
  `memory.write` with label propagation
- `src/capabledeputy/policy/rules.py` — the conflict rules
