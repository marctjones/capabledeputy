# Demo 6: Recurring Purchase via Pattern Library

**Audience:** people who care about whether the architecture *scales
to daily use*. Approval fatigue is the silent killer of secure
designs; pattern rules are the answer.
**Time:** ~2 minutes.
**Requires:** nothing beyond `uv sync`.

Recurring decisions — weekly groceries, regular spousal correspondence,
daily briefings — are the bulk of approvals. If every one demanded a
synchronous click, users would either disable the gate or click
without reading. Pattern rules let users **pre-decide** specific
recurring shapes, on a TTL, with strict pattern validation, while
preserving every audit invariant.

## What the demo proves

1. The shipped `configs/approval-patterns.yaml` library imports
   cleanly into a fresh registry.
2. Submissions matching a pattern auto-approve at submit time.
3. Submissions that don't match stay pending — pattern rules are
   specific by design.
4. Auto-approved submissions still emit the **full** audit pair
   (`approval.requested` + `approval.approved`) with the matched
   rule's id in `decision_scope`.

## Walkthrough

```bash
uv run pytest tests/test_e2e_recurring_purchase.py -v
```

The test imports the starter library:

```python
from capabledeputy.approval.library import apply_library, load_library_file

entries = load_library_file("configs/approval-patterns.yaml")
apply_library(entries, app.approval_queue.patterns)
```

The starter pack registers patterns including
`spouse-prescription-emails` (target = `spouse@example.com`,
payload glob = `*prescription*`, TTL = 7 days).

### Matching submission auto-approves

```python
matching = await app.approval_queue.submit(
    action=ApprovalAction.SEND_EMAIL,
    payload="Updated prescription summary attached.",
    target="spouse@example.com",
    ...
)

assert matching.status.value == "approved"
assert matching.decided_by.startswith("pattern:")
```

The submission landed straight in `approved` state. `decided_by`
points to the rule that matched.

### Off-pattern submission stays pending

```python
pending = await app.approval_queue.submit(
    action=ApprovalAction.SEND_EMAIL,
    payload="hi",
    target="random-stranger@example.com",
    ...
)

assert pending.status.value == "pending"
```

The `random-stranger` recipient doesn't match any pattern in the
library, so the request awaits human review.

### Audit fidelity

The audit log shows both events for the auto-approved submission:

```python
events = [...]
matched = [
    e for e in events
    if e.event_type.value == "approval.approved"
    and e.payload.get("decision_scope", {}).get("matched_rule")
]
assert len(matched) >= 1
```

Auto-approval is *not* silent. The matched rule id is in the
`decision_scope` payload of the `approval.approved` event so a
reviewer can audit which rule fired and how often.

## CLI workflow

```bash
# Import the library
capdep approval pattern import configs/approval-patterns.yaml

# List active patterns
capdep approval pattern list

# Revoke if you change your mind
capdep approval pattern revoke <rule-id>
```

## What this demonstrates

- **Pattern rules are specific by construction.** The pattern
  validator rejects bare `*` recipients, requires domain anchors for
  globs, caps TTL at 30 days. The footgun guards live in
  `src/capabledeputy/approval/pattern.py`.
- **Approval fatigue has an architectural answer.** Pre-decide the
  shapes, not the instances.
- **No silent approvals.** Every match emits the full audit pair —
  CapableDeputy is auditable end-to-end whether decisions are made
  by a human at request time or by a pre-declared rule.

## Files involved

- `tests/test_e2e_recurring_purchase.py`
- `src/capabledeputy/approval/library.py` — YAML library loader
- `src/capabledeputy/approval/pattern.py` — `ApprovalPatternRule`
- `configs/approval-patterns.yaml` — starter pack
