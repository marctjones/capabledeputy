---
description: "Spec 006 — automated demo scenarios exercising each security model + flow pattern end-to-end, using stub tools only"
---

# Spec 006: Demo Scenarios

## Goal

Ship a `demos/scenarios/` directory of scripted end-to-end workflows
that exercise CapableDeputy's security model against the kinds of work
people use OpenClaw and similar personal AI agents for. **All stub
tools — no real Gmail / GitHub / Google Workspace integration needed.**

Each scenario is simultaneously:

- An **integration test** that runs in CI on every commit.
- An **operator-facing artifact** with audit-log evidence demonstrating
  what CD does and how it differs from the rest of the "Claws" landscape.
- A **regression demonstration** that pins specific behaviors against
  the documented OpenClaw incident corpus (Meta-director, ToxicSkills).

## Non-goals

- Real provider integrations (Gmail/Calendar/GitHub/etc.) — that's
  spec 004 / spec 005.
- Spec 004 tier-1 MCP server integrations — defers to spec 004 phase 2.
- Spec-004 container substrates (Podman/Modal/Firecracker) — these demos
  do NOT exercise `code.execute` against a real sandbox. The
  `InProcessSandboxActuator` is acceptable.
- WebAuthn / Duo / OAuth — these demos exercise the security model
  with `--confirm` boolean attestation, not hardware-backed signatures.
- A polished demo viewer / playback UI — we ship pytest + markdown.

## Scope — the 9 demo scenarios

Each scenario maps to a security model or flow pattern + the documented
"value proposition" that distinguishes CD from OpenClaw/NemoClaw/
DefenseClaw.

### A. Workflow demos (4 scenarios)

These exercise typical knowledge-worker flows that an operator would
recognize from OpenClaw's use cases.

1. **`daily_briefing.py`** — Spawn session with `daily-briefing`
   profile. Read inbox via stub `inbox.read`. Pass labeled values
   through `quarantined.extract` (Pattern ② DUAL_LLM). Save summary
   to `memory.write`. Attempt to email a recipient with `email.send`
   → DENY (FR-019 social-commitment irreversible). Operator
   requests override; dual-control attest; re-attempt; ALLOW with
   `origin=override_granted`.
   Demonstrates: Brewer-Nash + Reversibility/social-commitment +
   override-distinct-from-approval workflow + Pattern ②.

2. **`bulk_approval_grouped.py`** — 500 actions with 2 distinct
   rationales. Group via `group_pending_approvals`. Assert 2 groups
   not 500 prompts (SC-012).
   Demonstrates: FR-035 semantic approval grouping (Demo #3).

3. **`optimistic_burn.py`** — 100 reversible/system writes to
   scratch storage. Auto-approved via optimistic-auto carve-out
   (FR-034). Then attempt one in-place destructive write →
   REQUIRE_APPROVAL via reversibility gate.
   Demonstrates: FR-034 + FR-019 + FR-039 (Demo #4).

4. **`risk_dial.py`** — Same task under cautious vs balanced vs
   permissive dial. Outcomes differ within the envelope; hard-floor
   cell immovable regardless of dial.
   Demonstrates: FR-030 + SC-010 envelope dial (Demo #1).

### B. Security-model demos (3 scenarios)

These prove specific security-model invariants the other Claws don't
enforce.

5. **`clinical_records_research.py`** — `clinical-regulated` profile
   (max_tier=regulated). Read health record at `regulated` tier →
   ALLOW. Attempt to read a `restricted` document → DENY with
   `CLEARANCE_REFUSED_RULE`.
   Demonstrates: FR-008 BLP read-up refusal (Demo #8).

6. **`hr_data_handling.py`** — HR-folder binding + TeamSharePoint
   binding. Personal/regulated data in axis_a. Attempt to write to
   teams.sharepoint.com → DENY via canonical destination id +
   operator-authored rule.
   Demonstrates: FR-043 + FR-048 + canonical destination (Demo #6).

7. **`prompt_injection_defense.py`** — `web.fetch` an untrusted
   page. Session's axis_b raises to `external-untrusted`. Planner
   attempts an `administer.label_edit` effect → DENY with
   `CONTROL_PLANE_TAINTED_RULE`.
   Demonstrates: FR-018 control-plane reflexivity (Demo #7).

### C. Flow-pattern demo (1 scenario)

8. **`data_blind_disclosure.py`** — `wrap_output_with_handles` on a
   medical record read. Planner context inspection shows UUIDs not
   raw values. Subsequent API tool with `accepts_handles=True`
   binds at the boundary; `pattern3.handle_bind` audit event records
   the canonical destination id.
   Demonstrates: Pattern ③ end-to-end (Demo #5; SC-021).

### D. Override workflow demo (1 scenario)

9. **`override_workflow.py`** — Action denied. Operator runs
   `capdep override request` via the IPC handlers. Dual-control
   attestation by distinct principal. Re-attempt → ALLOW with
   `origin=override_granted`. Audit trail distinct from ordinary
   approval.
   Demonstrates: FR-038 override-distinct-from-approval +
   SC-014 distinct attester (Demo #2).

## Functional Requirements

- **FR-300** Each demo MUST be a runnable pytest test.
- **FR-301** Each demo MUST emit a human-readable narrative (printed
  to stdout when run with `-s`) describing what just happened.
- **FR-302** Each demo MUST assert specific audit event sequences;
  failure to emit the expected events fails the test.
- **FR-303** Demos MUST run end-to-end against stub tools (no real
  Gmail/etc.) so CI doesn't require external network or credentials.
- **FR-304** Each demo MUST ship with a companion `.md` write-up
  in the same directory explaining the scenario, the threat model,
  and what CapableDeputy does that the alternatives don't.
- **FR-305** Demos MUST exercise the production code path (App +
  LabeledToolClient + PolicyContext) — not test-fixture-only
  shortcuts.
- **FR-306** A `demos/scenarios/run_all.py` driver MUST iterate
  every scenario and produce a consolidated report.
- **FR-307** Demos MUST NOT modify shared state between scenarios
  (each scenario constructs its own App + tmp paths).

## Success Criteria

- **SC-300** All 9 demos pass in CI (`uv run pytest demos/`).
- **SC-301** Each demo's narrative + audit log is reproducible
  byte-for-byte (SC-002 replay determinism).
- **SC-302** `python -m demos.scenarios.run_all` produces a clean
  consolidated report that an operator can read in under 5 minutes.
- **SC-303** Each demo's companion `.md` is publishable as a
  standalone artifact (no external context required).

## Out of scope

- Live MCP server integration (spec 004)
- Container sandbox demonstrations beyond the in-process actuator
- Multi-session / multi-user coordination demos
- Performance/load demos (those live under `tests/perf/`)
- TUI-based demos (defer to spec 005 onboarding wizard)

## Open questions

1. **Should `daily_briefing.py` use the demo's own ApprovalQueue or
   stub the approval flow inline?** Lean: stub inline; the queue is
   exercised by spec-005 work.
2. **Should the narratives include audit-log excerpts or summaries?**
   Lean: summaries with file paths to the full JSONL for reproducibility.
3. **Should we publish a screencast?** Out of scope for spec 006 —
   defer to spec 005 onboarding deliverable.
