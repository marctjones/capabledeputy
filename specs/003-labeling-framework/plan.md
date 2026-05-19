# Implementation Plan: Labeling Framework (v0.9)

**Branch**: `003-labeling-framework` (spec'd on `main` per project precedent) | **Date**: 2026-05-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification at `/specs/003-labeling-framework/spec.md`

## Summary

Build the v0.9 four-axis labeling framework — orthogonal axes A/B/C/D, deterministic sensitivity-resolution layer with context profiles and named Source/Location Label Bindings, unified Reversibility & Mutability labels (degree × agent), Risk-Preference Profile + Outcome Envelopes, Override Policy distinct from ordinary approval, Relationship Groups, Expectation Bindings, EXECUTE-class tiering, isolation-posture rules and Reference-Handle (Pattern ③) — on top of the existing v0.7+ TCB (chokepoint `decide()`, capability/label engine, async session graph, SQLite columnar store, audit). Deliver US1–US6 as independently testable increments. **Out of scope (deferred to spec 004 substrate track):** `SandboxActuator` implementation, `EXECUTE.sandbox` jailed tool, provider source adapters (filesystem MCP, Microsoft Graph / SharePoint / OneDrive, network shares), versioned-write actuator implementations — 003 specifies only the in-TCB rules, ports/contracts, and resolver.

## Technical Context

**Language/Version**: Python 3.12+ (`pyproject.toml: requires-python = ">=3.12"`).
**Primary Dependencies**: `anyio` (async), `typer` (CLI), `rich` (terminal), `litellm` (model abstraction), `mcp` (upstream MCP adapter), `pytest` + `hypothesis` (test).
**Storage**: SQLite (existing `~/.local/share/capabledeputy/state.db`, columnar `sessions` table). **SCHEMA_VERSION 5 → 6** for axis-orthogonal storage (FR-045) + new tables (Source/Location Bindings, Risk Register, Override Policies, Expectation Bindings, Relationship Groups, Outcome Envelopes, Risk-Preference Profile, Reference Handles).
**Testing**: `pytest` with property-based testing via Hypothesis (existing pattern, DESIGN §9); CI gate = `ruff` + `pyright` + `pytest`.
**Target Platform**: Linux & macOS (POSIX); rootless container hardening reused from `src/capabledeputy/upstream/isolation.py` for any 004 follow-on.
**Project Type**: Long-running daemon + CLI (single project).
**Performance Goals**: `decide()` < 1 ms per call (pure function over in-memory state); ingest-time label resolution < 5 ms; storage migration on first launch < 1 s for 10⁴ rows.
**Constraints**: Deterministic + LLM-isolated (Principle I, CI-enforced); fail-closed everywhere unknown (Principle VI); never `git add .` (explicit paths only).
**Scale/Scope**: Single-tenant personal AI agent; 10s of concurrent sessions; <1 MB persisted state per session; deeply nested delegation depth bounded by `CAPDEP_MAX_DELEGATION_DEPTH` (default 3, v0.8).

## Constitution Check

*GATE: passes pre-Phase 0. Re-check after Phase 1 design (see §Constitution Re-check below).*

| Principle | Status | Justification |
|---|---|---|
| **I.** Deterministic, LLM-isolated enforcement (NON-NEGOTIABLE) | ✓ | FR-007/010/021/026/029/031/036/037 are all pure deterministic functions over logged inputs; no FR adds an LLM call into resolution or decision. A CI invariant test (`test_enforcement_llm_independence`) already proves this for v0.7 — extended to cover new resolution paths. |
| **II.** Security by construction, not classifier | ✓ | Four orthogonal axes + structural admissibility exclusion at spawn (FR-009/FR-046) + sealed isolation (FR-040) + Reference Handle (FR-047) make disallowed flows unrepresentable, not merely detected. |
| **III.** Test-first, invariants as tests (NON-NEGOTIABLE) | ✓ | Every SC-001…022 is shaped as a measurable test (storage-shape audit, never-auto, restricted-via-③, destination-id canonicalization, etc.); each FR ships with at least one test in its phase task set. |
| **IV.** Least authority & minimal surface | ✓ | Capabilities remain narrow/one-shot/scoped (delegation 002 unchanged); Override Authorization is a scoped per-principal capability, not ambient; Reference Handles minimize planner authority/exposure. |
| **V.** Human-in-loop as deterministic FSM | ✓ | Approval state machine continues; Override Grant is a distinct mechanism with its own FSM (FR-032/038); approve-at-effect-abstraction preserved. |
| **VI.** Fail-closed by default (NON-NEGOTIABLE) | ✓ | FR-023, FR-026(d), FR-037, FR-039, FR-040, FR-043, FR-046, FR-047, FR-048 each explicitly fail-closed. CI test extends `test_unmapped_input_refused` to the new resolver paths (Source/Location bindings, Purpose Handle, destination-id, mutability). |
| **VII.** Secure-by-reduction; owned policy TCB | ✓ | FR-025 forbids a runtime content classifier; provider adapters live behind ports (FR-040/042/048); SandboxActuator + provider sources stay outside the TCB (Constitution §Sec. Constraints). Non-goals enumerated. |
| **VIII.** Model-faithful implementation; deviations documented | ✓ | FR-045 *requires* storage shape express axis orthogonality (the explicit model-faithful check from the live-session dump). Every new FR traces to a row in `docs/security-models.md`; deviations (single-parent provenance tree, intransitive declass, dynamic taint) already recorded. |

**No unresolved `NEEDS CLARIFICATION` in the spec.** Clarifications §Session 2026-05-19 in spec.md records 17 resolved decision points across two clarify passes.

**Constitution violations: 0.** Complexity Tracking is therefore empty.

## Project Structure

### Documentation (this feature)

```text
specs/003-labeling-framework/
├── plan.md              # this file
├── research.md          # Phase 0 — decisions + rationale (one entry per design choice)
├── data-model.md        # Phase 1 — entities, fields, relationships, persistence shape, state transitions
├── quickstart.md        # Phase 1 — canonical scenarios (HR→SharePoint deny, versioned write, restricted via ③, override w/ dual-control)
├── contracts/
│   ├── policy_engine.md       # Phase 1 — decide() signature, axis inputs, outcome type, audit object shape
│   ├── source_binding_port.md # Phase 1 — adapter contract: canonical destination id, ingest-time label application
│   ├── reference_handle.md    # Phase 1 — pattern ③ bind interface + where-secret-landed provenance
│   ├── override.md            # Phase 1 — Override Policy / Authorization / Grant FSM (distinct from approval)
│   └── tool_definition.md     # Phase 1 — ToolDefinition extension (effect-class tier, defaults, destination-id)
├── checklists/
│   └── requirements.md   # /speckit-specify quality checklist (all pass)
└── tasks.md             # Phase 2 — created by /speckit-tasks (NOT this command)
```

### Source Code (repository root)

Extends the existing single-project layout — no new top-level dirs. Concrete file plan:

```text
src/capabledeputy/
├── policy/
│   ├── labels.py             # EXTEND — axes A (category+tier) / B (provenance) / C (effect class) / D (decision context) as distinct types
│   ├── tiers.py              # NEW — strict total order none<sensitive<regulated<restricted<prohibited (FR-027); comparisons; max-tier clearance helpers
│   ├── reversibility.py      # NEW — Reversibility Label (degree×agent) + Mutability Label + composition (FR-037/039)
│   ├── resolution.py         # NEW — sensitivity-resolution layer (FR-007/008): (category,user,use-case,purpose)→tier; baseline + bounded-relax composition (FR-026)
│   ├── bindings.py           # NEW — Source/Location Label Binding resolver (FR-043/048); canonical destination-id matching; subtree inheritance; alias canonicalization
│   ├── risk_register.py      # NEW — internal risk register + external framework refs (FR-015/028); orphan-label audit
│   ├── relationships.py      # NEW — Relationship Group registry (FR-033)
│   ├── expectations.py       # NEW — Expectation Binding registry + match function (FR-029)
│   ├── envelope.py           # NEW — Outcome Envelope + Risk-Preference Profile (FR-030); dial selection within envelope
│   ├── overrides.py          # NEW — Override Policy / Authorization / Grant FSM, distinct from ordinary approval (FR-032/036/038); dual-control attestation
│   ├── purposes.py           # NEW — Purpose registry + admissibility rules (FR-009/046)
│   ├── decide.py             # EXTEND — orchestrate axes A–D, envelopes, hard floors, Reference-Handle gating, EXECUTE-tier dispatch
│   └── capabilities.py       # EXTEND — capability `origin` distinguishes user_approved vs override_granted; effect-class tier from ToolDefinition
├── patterns/
│   ├── __init__.py           # NEW MODULE — first-class flow patterns
│   ├── reference_handle.py   # NEW — Pattern ③: opaque per-session handles + controlled bind + where-secret-landed provenance (FR-047)
│   └── isolation_posture.py  # NEW — isolation-boundary semantics (FR-040/041/042); containment≠declassification rule; depends on substrate port (deferred to 004)
├── session/
│   ├── model.py              # EXTEND — Session gets purpose_handle, axis-A/B/D structured fields, reference_handle_set, override_grant_set
│   └── store.py              # EXTEND — SCHEMA_VERSION 5→6, ALTER TABLE for axis-orthogonal columns + new tables (see data-model.md)
├── tools/
│   └── registry.py           # EXTEND — ToolDefinition gains effect-class tier (EXECUTE.{sandbox|host|remote|deploy}), default reversibility/mutability, social-commitment flag, tool-provenance, canonical-destination-id contract
├── mode/
│   └── dispatcher.py         # EXTEND — select_mode adds ③ + ⑤ slots; restricted ⇒ requires ③ or ⑤ (FR-047), else fail-closed
├── audit/
│   └── events.py             # EXTEND — new events: binding.applied, override.granted/attested/refused, pattern3.handle_bind, isolation_region.{created,discarded}, envelope.dial_changed, risk_register.audit
└── substrate/                # NEW PORT DIR (interfaces only; impls deferred to spec 004)
    ├── source_port.py        # NEW — port the binding resolver expects: list resources / fetch with canonical destination id (FR-048)
    ├── version_write_port.py # NEW — port: write with verified prior-version retention (FR-044)
    └── sandbox_actuator.py   # NEW (port only) — disposable-isolation actuator interface (FR-040); impl in spec 004

tests/
├── policy/   # one file per new module + property-based tests; storage-shape audit; never-auto; fail-closed
├── patterns/ # pattern ③ leak/aliasing tests; restricted-without-③/⑤ fail-closed
└── e2e/      # canonical quickstart scenarios as e2e tests
```

**Structure Decision**: **Single project**, extending the existing `src/capabledeputy/*` layout. No new top-level packages; new modules cluster under `policy/`, a new `patterns/` package crystallizes flow-pattern primitives (was previously ad-hoc), and a new `substrate/` directory holds **ports only** (Constitution VII) — implementations live in spec 004.

## Phase 0 — Outline & Research

The spec has **no `NEEDS CLARIFICATION` markers** (clarify session 2026-05-19 resolved 13 questions in spec; 4 more in this design pass — see `research.md`). Phase 0 enumerates **design decisions** (chosen approach + rationale + alternatives considered) for each non-trivial 003 mechanism, so the data-model and contracts in Phase 1 inherit a clean set of decisions. Output: `research.md`.

## Phase 1 — Design & Contracts

1. **`data-model.md`** — Entities (Data Category, Provenance Level, Effect Class, Decision Context, Context Profile, Admissibility Rule, Purpose Handle, Source/Location Label Binding, Reversibility Label, Mutability Label, Expectation Binding, Relationship Group, Outcome Envelope, Risk-Preference Profile, Override Policy / Authorization / Grant, Human-Authored Decision Rule, Risk Register Entry, Residual-Risk Exception, Label-Assignment Record, Reference Handle, Disposable Isolation Region), their fields, relationships, validation rules drawn from the FRs, persistence shape (SCHEMA v6 ALTER TABLE + new tables), state transitions (Override-Policy lifecycle, Reference-Handle bind, Purpose-Handle resolution).
2. **`contracts/`** — five interface contracts the in-TCB code exposes:
   - `policy_engine.md` — extended `decide()` signature (axes A–D inputs, outcome ∈ `{auto, suggest, require-approval, deny}`, distinct Override Grant return path, audit-object shape with risk-register id).
   - `source_binding_port.md` — adapter contract (canonical destination-id semantics FR-048; ingest-time labeling FR-022/FR-043; alias canonicalization or fail-closed).
   - `reference_handle.md` — pattern ③ port: handle creation, controlled bind point, where-secret-landed provenance, leak invariants.
   - `override.md` — Override Policy / Authorization / Grant FSM, dual-control attestation contract.
   - `tool_definition.md` — `ToolDefinition` extension fields and validation.
3. **`quickstart.md`** — four canonical scenarios as integration-test sketches: HR-folder → SharePoint deny via named Source/Location Binding; versioned-write reversibility (git repo = `reversible/system`); `restricted`-tier session via Reference Handle (Pattern ③); Override Grant for an otherwise-`prohibited` action with `dual-control` policy.
4. **Agent context update** — point `CLAUDE.md` SPECKIT markers to `specs/003-labeling-framework/plan.md`.

## Constitution Re-check (post-Phase 1)

After Phase 1 design (data-model + contracts), re-check that no new mechanism introduces a non-deterministic input into `decide()` (Principle I), a new TCB dependency (Principle VII), a fail-open default (Principle VI), or an undocumented model deviation (Principle VIII). The contracts in §Phase 1 are designed to preserve all four: every adapter sits behind a port; every resolver path is pure-function; every unknown input fail-closes; every new mechanism is mapped to its security-model row (already updated in `docs/security-models.md`, `docs/llm-flow-patterns.md`). **Re-check passes.**

## Complexity Tracking

> No constitution violations → no entries.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| *(none)* | | |
