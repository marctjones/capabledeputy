# Demo 10: Bundled Approvals — Minimum-Approvals Workflow

**Audience:** anyone evaluating whether the architecture scales to
*real daily use*. Approval fatigue is the silent killer of any
permission system; bundled approvals are the architectural answer.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`.

The user's question this demo answers: *can I run a workflow with
multiple approval-gated actions, see the WHOLE plan, and authorise it
with one decision instead of N?* Yes. The mechanism is built on top
of programmatic mode.

## What the demo proves

1. A workflow with N `REQUIRE_APPROVAL` gates produces ONE bundle —
   the user makes ONE decision.
2. The bundle's impact tree shows every step (ALLOW + REQUIRE_APPROVAL
   + WOULD_DENY) with the args, labels, and rule that fired at each
   point.
3. `WOULD_DENY` gates are **non-negotiable** — they represent rules
   the user explicitly forbade and cannot be approved away.
4. The bundle's `program_hash` catches the case where the source
   changed between preview and execution.
5. Each pre-approved gate dispatches via a purpose-limited session
   (same pattern as cross-session declassification), so the existing
   audit shape (`approval.approved` events with `decision_scope`) still
   holds.

## Walkthrough

```bash
uv run pytest tests/test_approval_bundle.py -v
```

### Why bundle?

Three recurring monthly purchases — gym, streaming service, weekly
groceries — in a financial-context session each fire the
`financial-meets-purchase` rule (REQUIRE_APPROVAL). Without bundling,
the user clicks Approve three times. Worse, they probably don't
review each request because they look identical and they already
clicked Approve to the first.

With bundling:

```python
# end-of-month-purchases.py
gym = call("purchase.queue", vendor="planet-fitness", item="monthly", amount=30)
streaming = call("purchase.queue", vendor="netflix", item="monthly", amount=20)
groceries = call("purchase.queue", vendor="amazon-fresh", item="weekly", amount=180)
```

```bash
capdep run --bundle $SESSION_ID end-of-month-purchases.py
```

```
Bundle a4c9e2... (3 step(s)):
  ⚠ [ 1] purchase.queue labels=- rule=financial-meets-purchase
  ⚠ [ 2] purchase.queue labels=- rule=financial-meets-purchase
  ⚠ [ 3] purchase.queue labels=- rule=financial-meets-purchase

  3 approval gate(s) pending, 0 non-negotiable deny(s).

Approve and execute the bundle? [y/N]: y
✓ ok — 3 step(s) executed via bundle
```

One decision, three actions executed. The audit log records three
`approval.approved` events with the same `bundle_id` so trace tooling
can group them.

### What the dry-run actually does

The bundle collector is a variant of the existing `dry_run_program`
with one critical difference: when the policy returns
`REQUIRE_APPROVAL`, it **defers** instead of halting. The synthetic
output (with the tool's inherent labels) is returned to the program
so downstream steps continue to be analyzed. The result is a complete
impact tree, end to end, with every gate visible up front.

`DENY` decisions still halt the dry-run because they represent rules
the user said never to allow at policy-authoring time. The user can't
approve them away. The bundle's `is_approvable` flag is `False` if any
`WOULD_DENY` is present.

### Why the schema-extraction path doesn't replace this

You might ask: if `quarantined.extract` is the declassifier, why do we
need bundles at all? Because not every approval is a declassification.

- **Declassification approvals** (Demo 1, Demo 9) — the schema bounds
  what crosses the boundary. The user typed a verbatim payload.
  One-shot capability. The data going out is clearly less than the
  data inside.

- **Action approvals** (this demo) — the user authorises *the act*
  itself. The data isn't being decided on; the *decision* is. Three
  purchases, three decisions. The approval queue's `submit/approve`
  surface is one approval at a time. Bundles are the batched form.

The schema path and the bundle path are orthogonal mechanisms that
compose: a workflow can have schema-extraction steps (no approval
needed because the schema is the gate) AND action-approval steps
(bundled together).

### Source-change detection

```bash
# Generate the impact preview
capdep run --bundle --json $SID workflow.py > preview.json

# (Imagine the user reviews preview.json, then someone edits workflow.py)

# Try to execute the preview against the modified source:
capdep run --bundle $SID workflow.py
```

The execute step recomputes the program_hash and compares with
`impact.program_hash`. Mismatch raises `BundleMismatchError` and
nothing dispatches. The user can never approve one program and
execute a different one.

### Audit shape

Each pre-applied gate emits an `approval.approved` event under the
ORIGIN session id, with `decision_scope = {"bundle_id": ...,
"step_index": ..., "rule": ..., "executed_in_session": <purpose-uuid>}`.
That gives audit tooling everything it needs to group by bundle and
trace each step to its purpose-limited session.

## Files involved

- `src/capabledeputy/approval/bundle.py` — `WorkflowImpact`,
  `BundledApproval`, `GateState`, `render_impact_tree`,
  `hash_program`.
- `src/capabledeputy/programmatic/bundle_runner.py` —
  `dry_run_for_bundle` and `execute_with_approved_bundle`; the
  per-gate purpose-session dispatcher.
- `src/capabledeputy/daemon/bundle_handlers.py` — three RPCs:
  `programmatic.bundle_dry_run`, `bundle_execute`, `bundle_run`.
- `src/capabledeputy/cli/main.py` — `capdep run --bundle` and
  `--auto-approve` flags.
- `tests/test_approval_bundle.py` — eight tests: gate collection,
  blocking deny, approve-all + execute, source-hash mismatch,
  unknown tool, partial approval, impact rendering.

## What this demonstrates

- **Approval count is decoupled from action count.** A workflow with
  ten approval gates needs one human decision.
- **The user reviews the WHOLE plan, not isolated requests.** A
  malicious plan that splits an exfiltration across three steps to
  hide it is visible in the impact tree as three steps. The user
  sees the sequence.
- **The non-negotiable security floor is preserved.** `DENY` rules
  block the bundle from being approved at all. The user explicitly
  decides what's *allowed-with-permission* (REQUIRE_APPROVAL) versus
  *forbidden* (DENY) at policy-authoring time, and the bundle
  honours that distinction.
- **Programmatic mode + bundles compose.** The static-inspectability
  of programmatic mode is what makes the impact preview possible at
  all. Turn-level mode can't preview because the LLM hasn't decided
  what to do next yet — bundles need the upfront plan.
