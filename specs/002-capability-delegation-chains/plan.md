# Implementation Plan: Capability Delegation Chains

**Branch**: `main` (no feature branch — established v0.4–v0.7 workflow) | **Date**: 2026-05-16 | **Status**: Planned | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/002-capability-delegation-chains/spec.md`

## Summary

A session may hand a child session it spawns a capability **derived**
from one it holds, never broader. The deterministic policy engine
derives the delegated capability by clamping every dimension to the
parent (kind, pattern-subset, max_amount, expiry, rate, destructive)
and refuses any request that would widen any dimension or that cannot
be *proven* narrower (fail-closed, Constitution VI). Delegation forms a
single-parent provenance tree; revoking / expiring / rate-exhausting an
ancestor makes every transitive descendant unusable. Cascade is
**computed at decision time by walking provenance**, not by eager
mutation — so it is a pure deterministic function with no new state
machine, matching the v0.7 expiry/rate model and Principle I. The model
only *requests*; the engine alone derives and validates.

## Technical Context

**Language/Version**: Python 3.12 / 3.13
**Primary Dependencies**: none new — standard-library only; reuses
`policy.engine.decide`, `policy.capabilities.Capability`,
`session.graph`, `approval.queue`, `audit.writer`
**Storage**: SQLite session store, currently `SCHEMA_VERSION = 4`. This
feature adds two attributes to the serialized capability JSON
(`parent_audit_id`, `depth`) and an explicit per-session revoked-set;
both are **data-shape evolutions handled by the existing
default-tolerant `Capability.from_dict` / session `from_dict`**
(missing → not-delegated / empty revoked-set). **No DDL, no
SCHEMA_VERSION bump** — same precedent as 001's expiry attribute
**Testing**: pytest, pytest-asyncio, hypothesis
**Target Platform**: Linux daemon + `capdep` CLI over the JSON-RPC Unix socket
**Project Type**: single project — library core + `capdep` CLI + daemon
**Performance Goals**: provenance walk is O(depth) with depth bounded
by the configured max (default 3); cascade check composes into the
existing per-dispatch `decide()` with no scan of unrelated sessions on
the hot path
**Constraints**: derivation + cascade MUST be deterministic and
LLM-isolated (FR-012 / SC-006 / SC-007); single-parent tree (not a
DAG); fail-closed on undecidable pattern-subset (FR-004)
**Scale/Scope**: per-session capability sets are small; delegation
depth is bounded by config; cascade traversal is bounded by depth

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution `.specify/memory/constitution.md` is **v1.1.0**
(Principles I–VII ratified). Evaluated:

- **I. Deterministic, LLM-Isolated Enforcement (NON-NEGOTIABLE)** —
  `derive_delegated_capability()` and the cascade check are pure
  functions of explicit inputs; the model supplies only a *request*,
  never the grant. Proven by SC-006/SC-007 invariant tests. **PASS**.
- **II. Security by Construction** — broadening is unrepresentable: the
  engine constructs the delegated capability by clamping; a
  model-supplied capability is never trusted as-is. **PASS**.
- **III. Test-First, Invariants as Tests (NON-NEGOTIABLE)** —
  exhaustive per-dimension attenuation matrix, cascade tests, and an
  LLM-isolation invariant test ship with the change. **PASS** (planned).
- **IV. Least Authority & Minimal Surface** — delegation is *strictly
  attenuating* by definition; the depth bound caps tree growth;
  delegation request stays object-level (no control-plane exposure to
  the model). **PASS — reinforces**.
- **V. Human-in-the-Loop as Deterministic State Machine** — cascade
  invalidates *pending* approvals deterministically (FR-008); the
  model cannot approve its own delegation (FR-012). **PASS**.
- **VI. Fail-Closed by Default (NON-NEGOTIABLE)** — undecidable
  pattern-subset → refuse (FR-004); delegate-from-dead → refuse
  (FR-013); cycle/over-depth → refuse. This feature is a direct
  expression of VI. **PASS**.
- **VII. Secure-by-Reduction; Owned TCB** — pure in-repo engine
  extension, no third party; single-parent tree deliberately chosen
  over a DAG to keep cascade auditable; reuses the one chokepoint.
  **PASS**.

**Gate result**: PASS on all of I–VII. No violations → Complexity
Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-capability-delegation-chains/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── delegation.md    # derivation + cascade contract
└── checklists/
    └── requirements.md  # from /speckit-specify
```

### Source Code (repository root)

```text
src/capabledeputy/
├── policy/
│   ├── capabilities.py  # Capability gains parent_audit_id: UUID|None
│   │                    #   and depth: int (default-tolerant JSON);
│   │                    #   pure derive_delegated_capability(parent,
│   │                    #   request) -> Capability | DelegationRefusal;
│   │                    #   conservative pattern_is_subset()
│   └── engine.py        # decide(): provenance-walk cascade guard —
│                        #   a capability is inert if any ancestor is
│                        #   expired / rate-exhausted / explicitly
│                        #   revoked; new reason "capability-cascaded"
├── session/
│   ├── model.py         # session carries an explicit revoked
│   │                    #   audit_id set (default empty; serialized)
│   └── graph.py         # delegate(parent_sid, child_sid, request):
│                        #   depth/cycle/fail-closed checks, records
│                        #   provenance; revoke(audit_id) marks revoked
├── approval/
│   └── queue.py         # pending approval whose authorizing cap is a
│                        #   cascaded descendant is invalidated
├── daemon/
│   └── *_handlers.py    # session.delegate / capability.revoke RPCs
│                        #   (control-plane, user/operator-driven)
└── audit/
    └── events.py        # delegation.granted / delegation.refused /
                         #   capability.cascade_revoked event types

tests/
├── test_policy_capabilities.py   # derive_delegated_capability per-
│                                 #   dimension matrix; pattern_is_subset
│                                 #   conservative cases; round-trip
├── test_policy_engine.py         # cascade guard at decide(); compose
│                                 #   with expiry/rate/revoked; reason
├── test_session_graph.py         # depth bound, cycle refusal,
│                                 #   provenance recorded, revoke()
├── test_approval_*.py            # pending approval cascade-invalidated
└── test_delegation_e2e.py        # NEW: spawn→delegate→attenuate→
                                  #   cascade end-to-end; LLM-isolation
                                  #   invariant (SC-006/SC-007)
```

**Structure Decision**: Single project, existing layout. Additive:
two optional attributes on `Capability`, one pure derivation function,
one explicit revoked-set on the session, one O(depth) guard inside the
existing `decide()` chokepoint. No new package, no new dependency, no
DDL. Cascade is computed at decision time (no eager sweep, no new
state machine) — the single most important design choice, justified in
research.md.

## Complexity Tracking

> All of I–VII pass; no violations to justify. The design adds zero new
> abstractions beyond the provenance attributes and one pure function;
> single-parent tree was chosen specifically to *avoid* DAG/cascade
> complexity. Recorded only to state the simpler-alternative bar was
> applied.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| (none)    | —          | —                                    |
