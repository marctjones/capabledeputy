# Stabilization Plan

**Last refreshed:** 2026-09-05

This is the short-term plan to get CapableDeputy into a stable, honest MVP
state. It is intentionally narrower than the long-term roadmap: the goal is to
make the currently-shipped daemon-first secure assistant dependable, testable,
and internally consistent before broadening capability.

## MVP boundary

The MVP is:

- one local daemon as the single authority;
- CapDepMac as the primary conversational surface;
- CLI/TUI as secondary clients over the same daemon contract;
- deterministic policy, approvals, audit, and provenance;
- a small real tool surface that is already in-repo and policy-gated;
- first-class Google Workspace + GitHub connection/readiness;
- executable workflow and invariant coverage that proves the shipped claims.

The MVP is **not**:

- in-session vision;
- flow-aware planning;
- broader packaging/distribution work;
- new ambient-automation breadth;
- any claim that model reasoning correctness is solved.

## Stabilization gates

### Gate 1: Truth-source consistency

The repo must stop disagreeing with itself.

- package metadata, runtime version, tags, README, roadmap, and changelog must
  describe the same current release;
- the current active milestone must be the same in docs and GitHub;
- docs that are historical or draft must say so clearly;
- shipped-vs-deferred language must use concrete issue numbers.

### Gate 2: MVP reliability

The MVP must work without terminal-only escape hatches.

- CapDepMac override flow is complete end to end (`#331`);
- approvals surface reliably even in menu-bar-only mode (`#332`);
- first-run onboarding reaches a working chat turn (`#333`);
- artifact/media/approval rendering is consistent across surfaces (`#334`);
- reconnect is reliable across daemon restarts (`#319`);
- Google Workspace + GitHub are first-class and honest about readiness (`#325`,
  `#328`).

### Gate 3: Contract-backed assurance

Claims only count when they are traceable to code and tests.

- each active contract has named code owners/modules and at least one invariant
  or workflow test;
- core daemon boundaries are enforced by CI, not by convention;
- the workflow matrix remains the assurance north star, not raw test count;
- release criteria reference the real workflow/contract gates.

## Immediate execution checklist

### A. Repo consistency

- [x] Sync `pyproject.toml` and `capabledeputy.version` to the documented
  release line and add a regression test.
- [x] Update implementation docs to reflect the post-2026-07-18 milestone split:
  `NOW`, `v0.59`, and credential-gated integrations.
- [x] Align active working guidance and mark the original labeling implementation
  plan historical. Historical changelog entries retain their original versions.

### B. MVP closure

- [ ] Finish `#319`, `#331`, `#332`, `#333`, `#334`, `#325`, and `#328`.
- [ ] Do not start `v0.60+` feature work until those are green.
- [ ] Keep packaging work in the deferred bucket unless explicitly resumed.

### C. Architecture enforcement

- [x] Add no-authority denial probes for every registered native tool and a
  static direct-handler call tripwire. Dynamic bypass totality remains unproven.
- [x] Add a client authority-import guard and remove the override CLI local
  store fallback. Static checks do not prove all possible client effects.
- [ ] Keep surface-specific logic presentation-only unless a daemon RPC cannot
  express it.

### D. Design traceability

- [x] Create a design-contract traceability index for active contracts.
- [ ] Add/maintain one traceability row for every new contract-bearing feature.
- [ ] Require contract changes to update linked tests in the same change.

### E. Release criteria

- [x] Define one release checklist for "stable MVP":
  exact issue set closed, deterministic suite green, required GUI-sensitive
  smokes green, docs/metadata in sync, and workflow-plan gaps either closed or
  explicitly accepted. See [release-checklist-mvp.md](release-checklist-mvp.md).

## Current recommendation

Treat **v0.59** as the stabilization/MVP milestone, not as another feature
milestone. The project already has enough capability to demonstrate its core
thesis; what it lacks is convergence between product state, docs, and
contract-backed tests.


## Execution contract

Product statement: a local personal assistant that completes useful work under
explicit, inspectable authority, even when its model or inputs are untrustworthy.

The initial acceptance workflows are:

1. Research a topic and save a cited memo, preserving external-source provenance.
2. Summarize local documents and prepare a reviewed revision within a selected folder.
3. Triage an inbox and prepare drafts; sending requires a separate authorized action.

For each workflow record successful completion, denied unauthorized variants,
exact approval scope, cancellation, restart recovery, retained labels, honest
failure messages, and whether terminal rescue was needed. Configuration validation
is necessary but does not count as a live workflow pass.

Keep model-quality work inside stabilization when it addresses these workflows.
Do not broaden vision, automation, or packaging. Each feature must identify its
user task, required authority/data, enforced boundary, and positive/negative tests.

A stable MVP requires seven days of actual dogfooding. Record date, revision,
workflow, outcome, latency, approval count, unnecessary approvals, terminal rescue,
and incorrect success reports. No elapsed-day or credential gate may be checked
from unit tests. See stabilization-evidence-2026-09-05.md for execution evidence.
