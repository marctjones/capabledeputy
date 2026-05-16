# Demo 12: Programmatic Mode End-to-End

**Audience:** anyone evaluating the marquee v0.3 feature.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`.

The planner LLM responds with a single Python program describing the
entire data flow. The harness parses it against a strict AST subset,
optionally dry-runs to preview the policy decisions, and executes.
The user sees the whole plan before any tool fires.

## What the demo proves

1. A session created with `prefer_programmatic=True` routes turns
   through the programmatic planner loop. The LLM writes a code block;
   the harness parses + executes.
2. `dry_run_program` flags hidden violations *before* execution. No
   side effects from a workflow whose policy decisions would fail.
3. Programmatic mode composes with bundled approvals (Demo 10): a
   workflow with multiple approval-gated calls produces a single
   bundle the user reviews.

## Walkthrough

```bash
uv run pytest tests/test_e2e_programmatic_mode.py -v
```

### The planner emits a program

```python
program = """\
I'll read the user's note then save a copy with a clear name.

```python
note = call("memory.read", key="source")
saved = call("memory.write", key="copy", value=note["value"])
```
"""
```

The agent loop's programmatic planner extracts the fenced block,
parses against the AST subset (no `import`, no `class`, no `def`, no
attribute access — see DESIGN.md §10.5), and runs it through the
label-aware interpreter. Each `call(...)` dispatches via
`LabeledToolClient`; labels propagate through every operation.

### Dry-run flags violations before execution

The static analyzer (in `programmatic/runner.py`) symbolically
executes the program with the policy engine in dry-run mode. A
hidden `health-meets-egress` violation surfaces as a violation in the
report — without ever actually dispatching the email tool.

```python
report = await dry_run_program(src, registry, initial_scope=...)
assert not report.ok
assert any(v.rule == "health-meets-egress" for v in report.violations)
assert app.email_outbox.all() == []  # nothing dispatched
```

### Bundled approvals layer on top

Three purchase calls in a financial-tainted session each fire
`financial-meets-purchase` (REQUIRE_APPROVAL). The bundle collector
groups them into one impact tree: ONE human decision authorises all
three. See Demo 10 for the full bundle workflow.

## Why programmatic mode + bundles is the right shape

Turn-level mode can't preview future calls — the LLM hasn't decided
what to do next until the previous tool result lands. Programmatic
mode's **plan is the call graph**. That's the property that makes
preview, audit, and batched approval possible.

## Files

- `src/capabledeputy/programmatic/` — interpreter, parser, runner
- `src/capabledeputy/agent/programmatic_loop.py` — planner loop
- `tests/test_e2e_programmatic_mode.py`
