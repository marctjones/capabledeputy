# Demo 5: Email Triage with Schema-Validated Review

**Audience:** the assistant workflow people actually want every day.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`.

The most common AI-assistant ask after "summarize this" is "help me
deal with my inbox." That request is also a prompt-injection
playground: any inbound message could try to convince the model to
exfiltrate, reply to attackers, or impersonate. CapableDeputy's
schema-extraction path makes the workflow safe by construction.

## What the demo proves

1. **The planner never sees raw inbound bodies.** Triage rows come back
   as a typed `EmailTriageItem`: sender, subject, urgency, one-line
   summary. Senders' verbatim content stops at the quarantined LLM.
2. **Replies require explicit human approval.** The user provides the
   verbatim payload (no LLM paraphrase) and a recipient. Approval
   spawns a one-shot purpose session with a scoped `SEND_EMAIL`
   capability.
3. **Auto-approval patterns can ride on top.** A `SEND_EMAIL` pattern
   for a frequent recipient (e.g. `colleague@company.com` with a TTL
   and a payload glob) auto-approves recurring replies but still emits
   the full audit pair.

## Walkthrough

```bash
uv run pytest tests/test_e2e_email_triage.py -v
```

### Stage 1 — triage via schema

```python
inbox.add(
    InboundMessage(
        id="m1",
        sender="alice@example.com",
        subject="Q2 proposal review",
        body="Hi — please review the attached Q2 proposal by Friday. "
             "Marc asked you to drive this. Thanks!"
    )
)
memory.write("inbox.m1", body, frozenset({Label.UNTRUSTED_EXTERNAL}))
```

The agent calls
`quarantined.extract(key="inbox.m1", schema="EmailTriageItem")`. The
quarantined LLM produces:

```json
{
  "sender": "alice@example.com",
  "subject": "Q2 proposal review",
  "urgency": "high",
  "one_line_summary": "Asks for review by Friday on attached proposal"
}
```

The planner LLM sees this structured row. The test asserts that the
planner's second-turn context contains the schema fields (e.g.
`"high"` for urgency) but does **not** contain inflammatory body
phrases like `"Marc asked you to drive this"` — that text never
crossed the schema boundary.

### Stage 2 — approval-gated reply

```bash
capdep approval submit \
  --from-session $SID \
  --action SEND_EMAIL \
  --payload "Reviewing today, will respond by Friday EOD. — m" \
  --target alice@example.com \
  --label untrusted.external \
  --justification "reply to high-urgency proposal review"

capdep approval approve 1
```

The approval-execute path:

1. Spawns a fresh purpose session (cf. Demo 1's prescription flow).
2. Grants it a one-shot `SEND_EMAIL(pattern="alice@example.com")`.
3. Replaces its label set with `{trusted.user_direct}` — the
   `untrusted.external` label is shed because the *user* is the source
   of the verbatim payload, not the inbound message.
4. Dispatches `email.send`. Aborts the purpose session.

The originating triage session is unchanged: no SEND_EMAIL
capability, no taint outside what the schema allowed.

### Stage 3 (optional) — pattern auto-approval

For recurring correspondents you'd add a pattern:

```bash
capdep approval pattern create \
  --action SEND_EMAIL \
  --target alice@example.com \
  --ttl-hours 168
```

Future submissions to `alice@example.com` auto-approve on submit. The
audit pair `approval.requested → approval.approved` still fires; the
`decided_by` field becomes `pattern:<rule-id>`.

## What this demonstrates

- **Email triage is the everyday assistant workflow** — and CapableDeputy
  doesn't make it harder. It makes the safe path the natural path.
- **The verbatim payload rule is ergonomic.** The user types what they
  want sent. The agent triages, summarizes, suggests; it doesn't
  paraphrase the egress content.
- **Pattern rules tame approval fatigue.** Recurring decisions move out
  of the synchronous loop without becoming silent.

## Files involved

- `tests/test_e2e_email_triage.py`
- `src/capabledeputy/quarantined/schemas.py` — `EmailTriageItem`
- `src/capabledeputy/tools/native/inbox.py` — `inbox.list` / `inbox.read`
- `src/capabledeputy/approval/queue.py` — submit/approve lifecycle
- `src/capabledeputy/approval/pattern.py` — pattern rules
