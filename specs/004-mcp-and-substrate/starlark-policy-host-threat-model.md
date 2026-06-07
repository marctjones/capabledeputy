# Starlark policy host — threat model (spec-004 P3)

Status: **implemented** (`StarlarkScriptHost` in
`src/capabledeputy/substrate/policy_script_host.py`). Backed by
starlark-rust via the `starlark-pyo3` binding (module `starlark`),
shipped as the optional extra `capabledeputy[starlark]`.

## What it is

A `PolicyScriptHost` lets an operator author a decision-refinement
(relax / tighten / abstain) as a *script* instead of full-trust Python.
The chokepoint runs the script as a `DecisionInspector` after the
standard policy decision; the script can only **tighten** an outcome
or **relax** within operator-authored bounds — it never replaces the
capability/label enforcement, which runs first.

Policy-language contract (shared with the Python reference host): the
script defines

```python
def inspect(action, session, proposed_outcome):
    ...
    return relax(to="allow", rule="...", rationale="...")   # or
    return tighten(to="deny", rule="...", rationale="...")  # or
    return abstain()
```

`action` / `session` / `proposed_outcome` are plain dicts; `relax` /
`tighten` / `abstain` are the only callables in scope (defined in a
Starlark prelude prepended to the source).

## Trust model

The script author is **semi-trusted**: the file lives in the
daemon-owned `policies/` directory and is reviewed by the operator. The
host is defense-in-depth — it bounds what a *buggy or over-reaching*
script can do, and prevents an attacker who can only influence script
*inputs* (action/session data, which may be model-controlled) from
escaping into the host.

## Boundary — what Starlark guarantees (language-level)

Unlike `SafePythonScriptHost` (best-effort AST filtering of Python,
escapable), Starlark is a sandbox by construction:

- **No `import` / module access.** The binding is created without a
  `FileLoader`, so `load(...)` fails; there is no other import path.
- **No Python builtins.** `open`, `eval`, `exec`, `__import__`,
  `__builtins__` are simply not names in the Starlark environment.
- **No I/O.** No filesystem, network, environment, subprocess, or stdin
  access exists in the language.
- **Deterministic.** No clock, no randomness, no ambient state. A frozen
  module is evaluated fresh per call with no cross-call mutation.
- **Bounded control flow.** No `while` loops and no recursion (Starlark
  rejects self-referential calls); iteration is only over finite
  iterables.
- **Host-object opacity.** Only JSON-shaped values (dict/list/str/
  int/float/bool/None) cross the boundary in and out; the script cannot
  obtain a reference to a live host object.

## Residual risks + mitigations

1. **No hard step / CPU budget.** `starlark-pyo3` 2026.1 does not expose
   a per-eval instruction or fuel limit, so a pathological script (e.g.
   iterating a huge `range`) can burn CPU. Mitigations: (a) the operator
   authors + reviews the script; (b) Starlark's no-`while`/no-recursion
   rule removes the easy infinite loop; (c) evaluation runs off the event
   loop in a worker thread (`anyio.to_thread`) so it cannot stall the
   chokepoint's async loop. **Not yet enforced:** a hard wall-clock kill
   — a Python worker thread cannot be preempted, so a true timeout needs
   a subprocess host or a binding-exposed fuel limit. Tracked as
   follow-up; revisit when `starlark-pyo3` exposes an execution budget.
2. **Memory.** Likewise unbounded by the binding; same mitigations.
3. **Logic errors in the script.** Any exception, non-dict return, or
   unknown `kind` is converted to a `ScriptOutcome(kind="error")` and
   surfaced in the audit log — it never crashes the chokepoint, and an
   `error` outcome composes as *abstain* (no relaxation), i.e.
   fail-closed.

## Operational notes

- The runtime is an **optional** dependency. Core installs do not pull
  in the Rust/PyO3 wheel; `StarlarkScriptHost` lazily imports `starlark`
  and raises `PolicyScriptHostUnavailableError` (typed, fail-closed) when
  the extra is absent.
- `get_script_host(runtime_kind)` is the registry/factory; it
  fail-closes on an unknown runtime rather than silently falling back to
  the non-sandboxed Python reference host.
- The WebAssembly/wasmtime host (former P4) is **dropped** — Starlark
  covers the same operator need at lower complexity (see ROADMAP).
