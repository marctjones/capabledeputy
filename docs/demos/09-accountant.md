# Demo 9: Cross-Compartment Summary for an Accountant

**Audience:** the user who has the most realistic personal-assistant
ask: "summarize my financials so I can send them to my accountant."
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`.

Sending data across compartments is the recurring real-world workflow
this architecture exists for. Health to a spouse (Demo 1). Triaged
inbox to a contact (Demo 5). Financials to an accountant (this one).
The cross-cutting pattern: an explicit declassification gate, a
purpose-limited session, an audit pair.

This demo adds one more layer: the **schema itself** acts as a privacy
filter. The `FinancialSummaryForAccountant` schema buckets dollar
figures (`"100k-500k"`) instead of holding exact numbers. Even an
approved send doesn't leak precise totals.

## What the demo proves

1. A session that reads financial source data through
   `quarantined.extract` does NOT carry `confidential.financial` —
   the schema is the declassifier (DESIGN.md §5.2).
2. The user submits an approval with the bucketed payload as verbatim
   text. Approval spawns a one-shot purpose session with a
   recipient-scoped `SEND_EMAIL` capability.
3. The exact dollar figures from the source never appear in the sent
   email — the schema's buckets are the entire surface.

## Walkthrough

```bash
uv run pytest tests/test_e2e_accountant.py -v
```

### Setup

The financial source is held in labeled memory:

```python
app.memory.write(
    "finance.q1",
    "Q1 2026 detail:\n"
    "Income: $327,432.18 from 12 invoices to Customer A; ...\n"
    "Expenses: $74,108.92 across 412 transactions ...\n",
    frozenset({Label.CONFIDENTIAL_FINANCIAL}),
)
```

### Stage 1 — schema-validated extraction

The agent runs:

```python
quarantined.extract(
    key="finance.q1",
    schema="FinancialSummaryForAccountant"
)
```

The quarantined LLM (no tools, no network, schema-bound output) returns:

```json
{
  "period": "Q1 2026",
  "total_income_bucket": "100k-500k",
  "total_expenses_bucket": "50k-100k",
  "n_transactions": 412,
  "notable_categories": ["consulting", "infra", "travel"]
}
```

The exact figure `$327,432.18` does **not** appear anywhere in the
schema. The schema's `total_income_bucket` field is intentionally
constrained to coarse buckets — the privacy filter is in the schema
shape, not in any post-hoc redaction.

### Stage 2 — approval-gated send

The user reviews the bucketed summary, decides it's fine to share,
and submits:

```bash
capdep approval submit \
  --from-session $SID \
  --action SEND_EMAIL \
  --payload "Q1 2026 summary: Income bucket: 100k-500k, ..." \
  --target filer@accountant.example.com \
  --label confidential.financial \
  --justification "quarterly filing to accountant"

capdep approval approve 1
```

The approval mechanism:

1. Spawns a fresh purpose session with intent `"declassified send to
   filer@accountant.example.com (approved from <orig>)"`.
2. Grants a one-shot
   `Capability(SEND_EMAIL, pattern="filer@accountant.example.com")`.
3. Replaces label set with `{trusted.user_direct}`.
4. Dispatches `email.send` with the bucketed payload.
5. Aborts the purpose session.

### Test assertions

```python
body = app.email_outbox.all()[0].body
assert "100k-500k" in body         # bucketed value reached the wire
assert "327,432" not in body       # exact figure did NOT
```

```python
after = app.graph.get(s.id)
assert Label.CONFIDENTIAL_FINANCIAL not in after.label_set
assert app.memory.labels_of("finance.q1") == frozenset(
    {Label.CONFIDENTIAL_FINANCIAL}
)
```

The originating session was never tainted (extract is the
declassifier). The memory source's labels are unchanged. The purpose
session is dead.

## What this demonstrates

- **Schemas are privacy filters, not just structuring tools.** The
  bucketing is part of the contract: the planner *cannot* see the
  exact figure because there's no field for it.
- **Cross-compartment workflows are the point of the architecture.**
  Health to spouse, financial to accountant, untrusted research to
  notes — same primitives, same audit shape.
- **The schema and the approval gate compose.** Either alone is
  weaker than the pair: the schema bounds what could be sent; the
  approval ensures someone deliberate decides to send it.

## Files involved

- `tests/test_e2e_accountant.py`
- `src/capabledeputy/quarantined/schemas.py` — `FinancialSummaryForAccountant`
- `src/capabledeputy/approval/queue.py` — approval lifecycle
- `src/capabledeputy/daemon/approval_handlers.py` — purpose-session execution
