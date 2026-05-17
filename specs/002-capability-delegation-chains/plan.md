# Implementation Plan: Capability Delegation Chains

**Branch**: `main` (no feature branch — established v0.4–v0.7 workflow) | **Date**: 2026-05-17 | **Status**: Planned (re-planned post-clarify) | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/002-capability-delegation-chains/spec.md` (incl. Clarifications Session 2026-05-17 → FR-015, FR-016)

## Summary

A session hands a child it spawns a capability **derived** from one it
holds, never broader. The deterministic engine clamps every dimension
to the parent and refuses any widening or unprovable-narrower request
(fail-closed, Constitution VI). Provenance is a single-parent tree;
revoking / expiring / rate-exhausting an ancestor makes every
descendant inert — **computed at decision time by an O(depth)
provenance walk**, no eager mutation. The post-clarify spec adds two
requirements that extend the design: **FR-015** pooled rate accounting
(a delegated use is recorded against every ancestor's rate window, so
US2-4 holds by construction) and **FR-016** inherit-restrictive
non-enumerated fields (`revoked_by` ⊇ parent, `expiry` lifetime clamped
default `one_shot`, `origin = DELEGATED`). The model only *requests*;
the engine alone derives and validates.

## Technical Context

**Language/Version**: Python 3.12 / 3.13
**Primary Dependencies**: none new — reuses `policy.engine.decide`,
`policy.capabilities.Capability`, `session.graph`, `session.store`,
`approval.queue`, `audit.writer`
**Storage**: SQLite session store, `SCHEMA_VERSION = 4` — **unchanged**.
`parent_audit_id`/`depth` are additive default-tolerant capability
JSON; `CapabilityOrigin.DELEGATED` is a new enum *value* (serialized as
a string, default-tolerant); pooled rate accounting reuses the existing
serialized `cap_uses` map (v0.7) — fan-out adds entries under existing
keys, **no new column, no migration** (001 precedent)
**Testing**: pytest, pytest-asyncio, hypothesis
**Target Platform**: Linux daemon + `capdep` CLI over JSON-RPC Unix socket
**Project Type**: single project — library core + `capdep` CLI + daemon
**Performance Goals**: provenance walk O(depth) (depth ≤ configured
max, default 3); pooled-use fan-out O(depth) writes on a *granted*
delegated call; rate check unchanged O(window). No scan of unrelated
sessions on the hot path
**Constraints**: derivation + cascade + pooled accounting MUST be
deterministic and LLM-isolated (FR-012 / SC-006 / SC-007); single-parent
tree; fail-closed on undecidable pattern-subset (FR-004) and on every
non-enumerated field (FR-016, Constitution VI)
**Scale/Scope**: small per-session capability sets; bounded depth;
cascade + fan-out bounded by depth

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution **v1.1.0** (Principles I–VII). Evaluated incl. FR-015/FR-016:

- **I. Deterministic, LLM-Isolated (NON-NEGOTIABLE)** — derivation,
  cascade, and the pooled-use fan-out are pure functions of explicit
  inputs; model supplies only a request. SC-006/SC-007 invariant tests.
  **PASS**.
- **II. Security by Construction** — broadening unrepresentable
  (clamp-not-trust); FR-015 makes "no rate circumvention" structural,
  not a separate check; FR-016 makes non-enumerated widening
  unrepresentable. **PASS — strengthened**.
- **III. Test-First (NON-NEGOTIABLE)** — per-dimension matrix, cascade,
  pooled-rate, non-enumerated-inheritance, and LLM-isolation invariant
  tests ship with the change. **PASS** (planned).
- **IV. Least Authority & Minimal Surface** — delegation strictly
  attenuating across *all* fields now (FR-016); depth-bounded;
  delegate/revoke are control-plane, model-unreachable. **PASS —
  reinforced**.
- **V. Human-in-the-Loop** — cascade *invalidates* pending approvals
  via `inert(approval.capability_requested)` (deny-ward); model cannot
  approve its own delegation. **PASS**.
- **VI. Fail-Closed by Default (NON-NEGOTIABLE)** — undecidable
  pattern-subset → refuse; non-enumerated fields default to the
  most-restrictive value (FR-016 `expiry` default `one_shot`,
  `revoked_by` ⊇ parent); delegate-from-dead/cycle/over-depth → refuse.
  This feature is a direct expression of VI. **PASS**.
- **VII. Secure-by-Reduction; Owned TCB** — pure in-repo extension;
  single-parent tree; pooled accounting is a graph-local fan-out, no
  third party, no new abstraction. **PASS**.

**Gate result**: PASS on all of I–VII (pre- and, per the post-design
note below, after Phase 1). No violations → Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-capability-delegation-chains/
├── plan.md              # This file (re-planned post-clarify)
├── research.md          # Phase 0 — D1–D9 (D8 pooled rate, D9 non-enum)
├── data-model.md        # Phase 1 — incl. FR-015/FR-016 extensions
├── quickstart.md        # Phase 1 — incl. pooled-rate + non-enum steps
├── contracts/
│   └── delegation.md    # derivation + cascade + pooled-rate contract
└── checklists/
    └── requirements.md  # from /speckit-specify
```

### Source Code (repository root)

```text
src/capabledeputy/
├── policy/
│   ├── capabilities.py  # +parent_audit_id/depth; CapabilityOrigin
│   │                    #   gains DELEGATED; pattern_is_subset();
│   │                    #   derive_delegated_capability() clamps the 6
│   │                    #   dims + FR-016 (revoked_by ⊇, expiry
│   │                    #   lattice default one_shot, origin=DELEGATED)
│   └── engine.py        # decide(): provenance-walk inert() guard;
│                        #   rate disqualification uses POOLED chain
│                        #   windows (FR-015); reason capability-cascaded
├── session/
│   ├── model.py         # session +revoked_audit_ids (default empty)
│   ├── graph.py         # delegate() depth/cycle/fail-closed + record
│   │                    #   provenance; revoke(); pooled record_cap_use
│   │                    #   fans the timestamp to every ancestor cap's
│   │                    #   use log (FR-015)
│   └── store.py         # serialized fields default-tolerant (no DDL)
├── tools/
│   └── client.py        # on a granted delegated use, record_cap_use
│                        #   fan-out across the provenance chain
├── approval/
│   └── queue.py         # pending approval inert via
│                        #   inert(approval.capability_requested)
├── daemon/
│   └── session_handlers.py  # session.delegate / capability.revoke RPCs
├── cli/
│   └── main.py          # capdep session delegate / capability revoke
└── audit/
    └── events.py        # delegation.granted/refused/cascade_revoked

tests/
├── test_policy_capabilities.py  # per-dimension matrix; FR-016 clamps;
│                                #   pattern_is_subset; round-trip
├── test_policy_engine.py        # cascade guard; POOLED rate (FR-015);
│                                #   reason distinctness; composition
├── test_session_graph.py        # depth/cycle; provenance; revoke();
│                                #   pooled record_cap_use fan-out
├── test_approval_*.py           # pending approval cascade-invalidated
└── test_delegation_e2e.py       # spawn→delegate→attenuate→cascade→
                                 #   pooled-rate→non-enum; LLM-isolation
```

**Structure Decision**: Single project, existing layout. Additive:
two `Capability` fields, one enum value (`DELEGATED`), one pure
derivation function, one explicit revoked-set, an O(depth) `inert()`
guard in the existing `decide()`, and a pooled `record_cap_use`
fan-out. No new package/dependency/DDL. Cascade is computed at decision
time; pooled rate accounting is the heaviest delta (justified in
research D8).

## Complexity Tracking

> All of I–VII pass; no violations. FR-015 (pooled accounting) is the
> only non-trivial mechanism added; it is a graph-local O(depth)
> fan-out reusing the v0.7 `cap_uses` structure — chosen specifically
> over a downward subtree index or a separate use-log store
> (research D8). Recorded only to state the simpler-alternative bar was
> applied; nothing heavier was adopted.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| (none)    | —          | —                                    |
