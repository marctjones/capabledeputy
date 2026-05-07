# Demo 1: Prescription-to-Wife (Cross-Session Declassification)

**Audience:** security engineers, AI safety reviewers, anyone evaluating the
information-flow story.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`. No API key. No network.

This demo shows CapableDeputy's two architectural pillars in one scenario:
*structural denial* (a session that has read PHI cannot egress) and
*gated declassification* (the user can explicitly approve a one-shot
purpose-limited send).

## What the demo proves

1. A session that reads `confidential.health`-labeled data is **structurally
   blocked** from sending email, regardless of what the LLM tries.
2. The user can submit an explicit approval request with a verbatim payload
   they want sent to a specific recipient.
3. Approving spawns a **fresh purpose-limited session** with a one-shot
   capability scoped exactly to that recipient and that payload.
4. The originating health-context session **never gains** the egress
   capability. The audit log captures every step.

## Walkthrough

Run the test:

```bash
uv run pytest tests/test_e2e_prescription_to_wife.py -v
```

Both tests pass. Now walk through what each assertion verifies, in
order. Open `tests/test_e2e_prescription_to_wife.py` and follow along.

### Setup

The test creates an App with a `FakeLLMClient` scripted to do exactly
what a real LLM would: try to read a memory key, then try to email the
result. Pre-populates `memory["rx"]` with the prescription text labeled
`confidential.health`. Creates a session with both `READ_FS` and
`SEND_EMAIL` capabilities — the session has the *capability* to email,
but policy will block it.

### Phase 1: structural denial

```python
result = await agent["session.send"]({
    "session_id": str(health_session.id),
    "message": "Read my prescription and email a summary to wife@example.com.",
})

assert any(o["decision"] == "deny" for o in result["tool_outcomes"])
deny_outcome = next(o for o in result["tool_outcomes"] if o["decision"] == "deny")
assert deny_outcome["rule"] == "health-meets-egress"
assert len(app.email_outbox.all()) == 0
```

The agent loop:
1. LLM calls `memory.read({"key": "rx"})` → policy ALLOWS, label
   `confidential.health` propagates into the session.
2. LLM calls `email.send({"to": "wife@example.com", ...})` → policy
   DENIES with rule `health-meets-egress`. The denial returns to the LLM
   as a tool message.
3. LLM produces a final answer explaining the block.

The outbox is empty. The LLM did not get to send.

### Phase 2: explicit approval

```python
submitted = await approvals["approval.submit"]({
    "from_session": str(health_session.id),
    "action": "SEND_EMAIL",
    "payload": "Updated prescription: Lisinopril 10mg daily, recheck in 6 weeks.",
    "target": "wife@example.com",
    "labels_in": ["confidential.health"],
    "justification": "user wants spouse informed of new dose",
})
assert submitted["status"] == "pending"
```

The user submits an approval request. The verbatim payload is stored;
nothing is paraphrased. The request is in PENDING.

```python
approve_result = await approvals["approval.approve"]({
    "id": submitted["id"], "decided_by": "marc",
})
```

The user approves. The system:
1. Marks the approval APPROVED.
2. Creates a fresh `purpose_session` with intent
   `"declassified send to wife@example.com (approved from <orig>)"`.
3. Grants it a `Capability(SEND_EMAIL, pattern="wife@example.com",
   expiry=ONE_SHOT, origin=USER_APPROVED)`.
4. Replaces its label set with `{trusted.user_direct}` — no
   `confidential.health`.
5. Dispatches `email.send` in the purpose session with the approved
   payload.
6. Aborts the purpose session.

### Phase 3: invariant assertions

```python
assert len(app.email_outbox.all()) == 1
sent = app.email_outbox.all()[0]
assert sent.to == "wife@example.com"
assert "Lisinopril" in sent.body
```

Exactly one email, to exactly the approved recipient, containing the
approved text.

```python
after_health = app.graph.get(health_session.id)
assert Label.CONFIDENTIAL_HEALTH in after_health.label_set
assert all(
    c.kind != CapabilityKind.SEND_EMAIL or c.pattern != "wife@example.com"
    for c in after_health.capability_set
)
```

The originating health session **still** carries the health label and
**did not** gain a wife@example.com capability. The egress did not
happen *in* that session; it happened *via* a derived purpose-limited
session.

```python
purpose = app.graph.get(UUID(purpose_session_id))
assert purpose.status == SessionStatus.ABORTED
assert Label.CONFIDENTIAL_HEALTH not in purpose.label_set
assert Label.TRUSTED_USER_DIRECT in purpose.label_set
```

The purpose session is dead. It never had the health label. It existed
just long enough to send the approved payload to the approved
recipient.

### Phase 4: audit log

```python
events = await app.audit.read_all()
types = [e.event_type.value for e in events]
assert "approval.requested" in types
assert "approval.approved" in types
assert "session.created" in types
```

Every step of the flow is in the audit log: original blocked attempt,
approval request, approval decision, purpose-session creation, email
dispatch attribution, purpose-session abort.

## What this demonstrates

- **Capability holds at the structural level**, not at the LLM level.
  Even if the LLM had been compromised by prompt injection in the
  prescription text, it could not have sent the email — the harness
  enforces, not the LLM.
- **Declassification is explicit, scoped, and audited.** The user
  approves a specific payload to a specific recipient. The capability
  is one-shot. The session that uses the capability is purpose-limited
  and torn down after use.
- **Compartmentalization survives.** The original health session still
  carries the health label and remains correctly gated for any future
  attempts.

## Variants worth showing

- Run with `--reason "no thanks"` on the deny path to show the denied-
  approval audit trail.
- Submit an approval, then create a pattern rule that auto-approves
  similar patterns; show that subsequent submits land as APPROVED on
  arrival but with `decided_by="pattern:<id>"`.

```bash
# Auto-approve future SEND_EMAIL to wife@example.com for 24 hours
capdep approval pattern create \
  --action SEND_EMAIL --target wife@example.com --ttl-hours 24
```

## Files involved

- `tests/test_e2e_prescription_to_wife.py` — the test
- `src/capabledeputy/approval/queue.py` — submit/approve lifecycle
- `src/capabledeputy/daemon/approval_handlers.py` — the
  `_execute_declassified_email` workflow
- `src/capabledeputy/tools/native/email.py` — the stub outbox
- `src/capabledeputy/policy/rules.py` — the `health-meets-egress` rule
