# Implementation Plan: Labeling Framework

**Branch**: `003-labeling-framework` | **Date**: 2026-05-25 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/003-labeling-framework/spec.md`

## Summary

003 promotes capdep's information-flow model from a single 8-value prefixed label set into four independent axes (data-category, provenance/integrity, effect class, decision context) with a deterministic, engine-side resolution layer and a structured Purpose Handle on every Session. The framework adds:

- **Orthogonal axes** with structured persistence (FR-001 / FR-045)
- **Per-purpose risk-preference dial** with operator-declared per-cell outcome envelopes; non-deterministic inputs may only tighten, never relax (FR-030 / FR-031)
- **Scoped, time-boxed, audited Override Grants** under an operator-configured Override Policy (`disallowed | single-authorized | dual-control`) with 15-min default / 60-min hard-cap expiry (FR-032 / FR-036)
- **Source/Location Label Bindings** referenceable by name in rules with canonical destination-id contracts (FR-043 / FR-048)
- **Reference Handles (flow-pattern ③)** mandatory for `restricted`-tier data (FR-047)
- **Disposable Isolation Regions** (`EXECUTE.sandbox`) as the preferred posture, with containment ≠ declassification (FR-040 / FR-041 / FR-042) — actuator implementation deferred to spec 004
- **Per-Risk-Register-Entry residual-risk thresholds** so FR-016 exceptions cite the specific risk crossed (FR-016 / FR-028)
- **Ratification authorization** reusing the Override Policy / Override Authorization model (FR-014)
- **Decision-latency SLO**: p95 ≤ 50 ms / p99.9 ≤ 250 ms (SC-023)

The implementation lives entirely inside the existing in-repo trusted computing base: `src/capabledeputy/policy/` + `src/capabledeputy/session/`. Substrate (sandbox actuator, provider source adapters, version-preserving write actuators) lives behind in-repo ports and is delivered separately by spec 004.

## Technical Context

**Language/Version**: Python 3.12 (existing project baseline; `pyproject.toml` declares `requires-python = ">=3.12"`).
**Primary Dependencies**: `prompt-toolkit` (REPL input), `rich` + `textual` (REPL output), `anyio` + `mcp` (daemon RPC + upstream MCP servers), `litellm` (LLM client), `pyyaml` (config), `httpx`/`pydantic` (transitive). No new dependencies added by 003 — all axis math, registry, and resolver code lives in pure Python.
**Storage**: SQLite via the existing per-process `state.db` (`src/capabledeputy/store/`). Single file at `~/.local/share/capabledeputy/state.db`. Audit log is append-only JSONL at `~/.local/share/capabledeputy/audit.jsonl`. Risk register and bindings are operator-edited YAML/JSON in `configs/` (file is the source of truth, not a SQLite table).
**Testing**: `pytest` (existing project standard) + `pytest-asyncio` for the async-generator agent loop. Coverage already at 60%+; 003 work tracked via `tests/test_*.py` named after the FR they verify (e.g. `test_axis_storage_shape.py` for FR-045, `test_purpose_handle.py` for FR-046).
**Target Platform**: Linux x86_64 (operator's local machine; single-tenant). macOS support is a non-goal for v0.9 but Python code stays portable; container substrate uses Podman (Linux-native rootless).
**Project Type**: CLI / local daemon — single-operator agent runtime with no remote / hosted surface.
**Performance Goals**:
- **Decision latency** (SC-023): p95 ≤ 50 ms; p99.9 ≤ 250 ms per chokepoint dispatch (axes A-D evaluation + composition + rule lookup + capability match + audit emission).
- **Determinism**: SC-002 requires byte-identical re-runs from logged inputs.
- **Throughput**: not separately specified; bounded by interactive operator pace (~1-10 decisions/sec typical).
**Constraints**:
- **Constitution Principle I**: zero LLM participation in decisions (every gate must be a pure function over explicit inputs).
- **Constitution Principle VI**: fail-closed on unclassifiable input — unbound source ⇒ deny, no canonical destination ⇒ deny.
- **Constitution Principle VII**: substrate stays behind in-repo ports; the SandboxActuator implementation, the provider adapters, and the version-preserving write actuators are spec-004 substrate, not 003 TCB.
- **Constitution Principle VIII**: every mechanism in 003 must trace to a named security model (Denning lattice for axis B, Brewer-Nash for FR-009 admissibility exclusion, Clark-Wilson for FR-019 destructive gate, Bell-LaPadula for FR-008 read-up refusal, Biba for the integrity floor, noninterference for FR-047 Pattern ③).
- **Backward compatibility**: forward-only migration per FR-024 — legacy `sessions.label_set` rows read at most-restrictive position; never lower effective protection.
**Scale/Scope**:
- Single operator, single machine, single daemon process.
- Rule corpus: ≥1k human-authored decision rules tractable.
- Risk Register: ~50-200 entries (one per OWASP LLM/Agentic + MITRE ATLAS + NIST AI RMF + EU AI Act + FIPS 199 + FAIR risk currently relevant).
- Concurrent sessions per operator: typical 1-3, max ~10.
- Audit log: rotated when >100 MB; latest 10 archives retained.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluating 003 against each principle in `.specify/memory/constitution.md` (v1.2.0):

| Principle | Status | Justification |
|---|---|---|
| I. Deterministic, LLM-isolated enforcement | **PASS** | All axes A–D resolution and outcome composition (FR-007/010/026) are pure functions of `(session_state, rule_set, registry, clock)`. No LLM participates. FR-031 asymmetry invariant explicitly forbids non-deterministic inputs from relaxing outcomes. |
| II. Security by construction | **PASS** | Disallowed flows are unrepresentable: a `restricted` session that cannot route through ③ or ⑤ **fails closed at spawn** (FR-047); an unbound source location resolves fail-closed (FR-023 / FR-043); a tool whose effect class is `EXECUTE.host` cannot run un-contained on the AI's claim (FR-042). |
| III. Test-first, invariants as tests | **PASS** | Every FR is covered by ≥1 SC; every SC is a CI-checkable assertion. T120 tripwire (FR-014) enforces "unratified ⇒ 0 effect" structurally. The full taxonomy of edge cases in `spec.md §Edge Cases` maps to test fixtures. |
| IV. Least authority & minimal surface | **PASS** | The agent never sees control-plane operations (FR-018). Ratification authority itself is a scoped capability (FR-014 + Ratification Authorization, today's Q3 clarification). The dial is per-purpose, never per-session-mutable at runtime (today's Q1 clarification, FR-030). |
| V. Human-in-the-loop deterministic state machine | **PASS** | Override Policy is a state machine valued in `{disallowed \| single-authorized \| dual-control}` (FR-036). Operator reviews verbatim engine-authored facts, not model prose. Ratification reuses the same state machine (Q3 clarification). |
| VI. Fail-closed by default | **PASS** | Unclassifiable data → most-restrictive (FR-023). Unbound source → fail-closed (FR-043). No canonical destination → fail-closed (FR-048). Unlabeled reversibility → `irreversible/none` (FR-037). Override Policy default → `disallowed` for hard floors (FR-036). 60-min absolute cap on Override Grant expiry (Q2 / FR-032) prevents misconfiguration from yielding all-day bypasses. |
| VII. Secure-by-reduction; owned policy TCB | **PASS** | The SandboxActuator port + EXECUTE.sandbox actuator + provider source adapters are explicitly **out of scope for 003** (spec 004 substrate); 003 specifies only the in-TCB labeling/decision rule. The decision plane stays in `src/capabledeputy/policy/` — no third-party policy engine adopted. |
| VIII. Model-faithful implementation; deviations documented | **PASS** | Model lineage per mechanism: axis B = Denning lattice + Biba; FR-008 read-up = Bell-LaPadula; FR-009 admissibility = Brewer-Nash; FR-019 destructive gate = Clark-Wilson well-formed transactions; FR-047 pattern ③ = noninterference; capabilities = object-capability. Documented in `docs/security-models.md`. |

**No deviations to track.** All non-negotiable principles (I, III, V, VI) are satisfied by construction. Complexity Tracking has no entries.

## Project Structure

### Documentation (this feature)

```text
specs/003-labeling-framework/
├── plan.md              # This file
├── research.md          # Phase 0: design decisions (D1–D9 from May 19; D10–D14 from May 25 clarifications)
├── data-model.md        # Phase 1: SCHEMA_VERSION 5→6 shape + new tables
├── quickstart.md        # Phase 1: operator-facing walkthrough
├── contracts/           # Phase 1: port contracts
│   ├── override.md
│   ├── policy_engine.md
│   ├── reference_handle.md
│   ├── source_binding_port.md
│   └── tool_definition.md
├── checklists/          # CI-checked invariants
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

Single-project layout. 003 work modifies existing modules in-place rather than creating new top-level packages.

```text
src/capabledeputy/
├── policy/
│   ├── capabilities.py         # CapabilityKind enum + back-compat union; custom-kind registry
│   ├── engine.py               # decide() — chokepoint; baseline-and-bounded-relax (FR-026); destructive gate (FR-019)
│   ├── rules.py                # human-authored decision rules
│   ├── bindings.py             # Source/Location Label Binding resolver (FR-043/048) — already exists
│   ├── envelopes.py            # NEW — per-cell outcome envelopes (FR-030)
│   ├── ratification.py         # NEW — Ratification Authorization state machine (FR-014, Q3)
│   ├── reference_handle.py     # NEW — Pattern ③ runtime (FR-047)
│   └── risk_register.py        # NEW — risk-id resolution + threshold lookup (FR-016/028, Q5)
├── session/
│   ├── graph.py                # SessionGraph — add purpose_handle, axis_a/b/d storage (FR-045/046)
│   └── model.py                # Session dataclass — axis fields per FR-045
├── store/
│   ├── schema.py               # SCHEMA_VERSION 5→6 migration (forward-only per FR-024)
│   └── migrations/v6.py        # NEW — legacy label_set forward-only migration
├── audit/
│   ├── events.py               # Add: pattern3.handle_bind, residual_risk_exception, ratification.applied, decision.latency_degraded
│   └── writer.py
├── tools/
│   ├── client.py               # LabeledToolClient — already dispatches through chokepoint
│   └── registry.py             # ToolDefinition — accepts_handles flag (FR-047)
├── override/
│   ├── policy.py               # Override Policy state machine (FR-036) — already exists
│   ├── grant.py                # Override Grant — 15-min default expiry, 60-min cap (FR-032, Q2)
│   └── authorization.py        # Override / Ratification Authorization mapping (FR-014, FR-036)
├── upstream/
│   └── adapter.py              # Surfaces canonical destination-id at decide-time (FR-048)
└── cli/
    └── chat.py                 # /override, /grant, /server, /ratify (UI deferred per FR-014)

configs/
├── risk_register.json          # NEW — operator-editable risk register (FR-028)
├── purposes.yaml               # EXTENDED — each purpose entry carries risk_preference_dial (FR-030, Q1)
├── source_bindings.yaml        # EXISTS — Source/Location Label Bindings (FR-043)
└── override_policy.yaml        # EXISTS — Override Policy + Authorization (FR-036)

tests/
├── test_axis_storage_shape.py            # FR-045 / SC-019
├── test_purpose_handle.py                # FR-046 / SC-020
├── test_reference_handle.py              # FR-047 / SC-021
├── test_destination_id.py                # FR-048 / SC-022
├── test_source_binding.py                # FR-043 / SC-018
├── test_outcome_envelope.py              # FR-030 / SC-010
├── test_asymmetry_invariant.py           # FR-031 / SC-010
├── test_override_policy.py               # FR-036 / SC-014
├── test_override_grant_expiry.py         # NEW (Q2) — verifies 15-min default + 60-min cap
├── test_ratification_authorization.py    # NEW (Q3) — verifies ratification reuses Override Authorization
├── test_risk_register_thresholds.py      # FR-016/028 / Q5 — per-entry thresholds
├── test_decision_latency.py              # NEW (Q4) — p95/p99.9 benchmark
└── test_t120_unratified_zero_effect.py   # FR-014 tripwire
```

**Structure Decision**: Existing single-project layout (`src/capabledeputy/`) is the right shape. 003 modifies existing modules in-place and adds 5 new modules (`envelopes.py`, `ratification.py`, `reference_handle.py`, `risk_register.py`, `migrations/v6.py`). No new top-level packages. No new external dependencies. The `policy/` package is the in-TCB decision plane; the `upstream/` adapter layer surfaces destination ids but stays outside the TCB per Constitution VII.

## Complexity Tracking

> Constitution Check passes without deviation; no entries needed.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _none_ | _n/a_ | _n/a_ |
