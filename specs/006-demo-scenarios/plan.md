---
description: "Spec 006 implementation plan — 3-5 day scope for 9 demo scenarios"
---

# Plan: Spec 006 Demo Scenarios

## Approach

Each demo is structured the same way:

1. **Setup block** — constructs an `App` with a tailored `PolicyContext`
   (relevant rules / bindings / envelopes / overrides for this scenario)
   and a tmpdir audit log.
2. **Workflow block** — drives a sequence of tool dispatches through
   `app.tool_client.call_tool(...)`.
3. **Assertion block** — verifies the `Decision`, `rule`, and audit
   event sequence. Failure to match the expected security promise
   fails the test.
4. **Narrative block** — `print(...)` statements describing the
   scenario in operator-facing language. Visible with pytest `-s`.

A shared helper module (`demos/scenarios/_helpers.py`) provides:

- `make_app(tmp_path, policy_context_kwargs)` factory.
- `make_session(app, profile_id, axis_a_categories)` for typical
  session bootstrapping.
- `narrate(title, body)` for consistent narrative output.
- `assert_audit_sequence(audit_writer, expected_event_types)` for the
  audit-event chain assertions.

## Schedule

### Day 1 — Foundation + 3 demos

- `_helpers.py` — shared fixtures.
- `daily_briefing.py` — the marquee workflow demo (most complex).
- `override_workflow.py` — exercises CLI ↔ daemon IPC.
- `risk_dial.py` — envelope dial cross-product.

### Day 2 — 3 demos

- `clinical_records_research.py` — BLP clearance.
- `hr_data_handling.py` — binding-driven egress.
- `prompt_injection_defense.py` — control-plane reflexivity.

### Day 3 — 3 demos + driver + writeups

- `optimistic_burn.py` — 100-action burn.
- `bulk_approval_grouped.py` — 500-action grouping.
- `data_blind_disclosure.py` — Pattern ③.
- `run_all.py` — consolidated driver.
- Companion `.md` writeups for each scenario.

## Dependencies

- All demos depend on the rc.6 baseline (App + PolicyContext + native
  tools + override IPC handlers).
- No new tool implementations needed — every demo uses existing stub
  tools (email, memory, inbox, web, calendar, etc.).
- Override workflow demo depends on the CLI ↔ daemon override handlers
  shipped in rc.6.

## Risk register

- **R1: Tests depend on absolute time.** Mitigation: use frozen
  clocks via `now=` kwarg on every `decide()` call.
- **R2: Audit-event order non-determinism.** Mitigation: assert
  event-type SET equality first; event-order assertions only where
  truly deterministic.
- **R3: Demos drift from production code path.** Mitigation: every
  demo uses `App(...)` + `app.tool_client.call_tool(...)` rather than
  hand-constructed PolicyContext + manual `decide()` calls.

## Definition of done

- 9 demos in `demos/scenarios/`, all passing under pytest.
- 9 companion `.md` files.
- `run_all.py` driver + a passing CI invocation.
- README excerpt added pointing readers at the demos.
- Tag `v1.0.0-demos-shipped`.
