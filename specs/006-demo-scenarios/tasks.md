---
description: "Spec 006 task list — 9 demo scenarios + shared helpers + driver + writeups"
---

# Tasks: Spec 006 Demo Scenarios

**Input**: Design documents from `/specs/006-demo-scenarios/`
**Prerequisites**: spec.md, plan.md, v0.9.0-rc.6 baseline.

## Format: `[ID] [P?] Description`

- **[P]**: Different file, no incomplete-task dependency.
- ID prefix `D` for "demo scenario."

---

## Foundation

- [ ] **D001** Create `demos/__init__.py` + `demos/scenarios/__init__.py`.
- [ ] **D002** Shared helpers in `demos/scenarios/_helpers.py`:
  `make_app()`, `make_session()`, `narrate()`,
  `assert_audit_sequence()`, frozen-clock fixture.

## The 9 demo scenarios

- [ ] **D003** [P] `demos/scenarios/daily_briefing.py` — inbox.read +
  quarantined.extract + memory.write + email.send DENY +
  override request+attest+retry. Brewer-Nash + reversibility +
  override workflow + Pattern ②.
- [ ] **D004** [P] `demos/scenarios/override_workflow.py` — denied
  action → CLI request → dual-control attest → ALLOW with
  origin=override_granted. FR-038 + SC-014.
- [ ] **D005** [P] `demos/scenarios/risk_dial.py` — same task,
  cautious vs balanced vs permissive dial. Hard-floor cell
  immovable. FR-030 + SC-010.
- [ ] **D006** [P] `demos/scenarios/clinical_records_research.py` —
  clinical-regulated profile reads regulated OK; restricted →
  DENY with CLEARANCE_REFUSED_RULE. FR-008.
- [ ] **D007** [P] `demos/scenarios/hr_data_handling.py` — HR-folder
  + TeamSharePoint bindings; personal data to team share → DENY
  via canonical destination + rule. FR-043 + FR-048.
- [ ] **D008** [P] `demos/scenarios/prompt_injection_defense.py` —
  web.fetch untrusted page → axis-B taint → administer.* DENY.
  FR-018.
- [ ] **D009** [P] `demos/scenarios/optimistic_burn.py` — 100
  reversible/system writes auto-approved; 1 in-place destructive
  write requires approval. FR-034 + FR-019.
- [ ] **D010** [P] `demos/scenarios/bulk_approval_grouped.py` —
  500 actions, 2 rationales → 2 groups. FR-035 + SC-012.
- [ ] **D011** [P] `demos/scenarios/data_blind_disclosure.py` —
  wrap_output_with_handles + accepts_handles tool + bind audit.
  Pattern ③ + SC-021.

## Driver + writeups

- [ ] **D012** `demos/scenarios/run_all.py` — iterates every demo
  and produces a consolidated report.
- [ ] **D013** [P] Companion `.md` files for each demo
  (`demos/scenarios/*.md`) — operator-facing narrative.
- [ ] **D014** README excerpt linking to `demos/scenarios/`.
- [ ] **D015** Final sweep + tag `v1.0.0-demos-shipped`.

---

## Dependencies & Execution Order

- D001 + D002 block everything.
- D003-D011 are mutually parallel after the foundation lands.
- D012-D015 run last.

## Acceptance criteria

- All 9 scenarios pass `uv run pytest demos/`.
- `python -m demos.scenarios.run_all` exits 0.
- Each demo produces a stable audit-event sequence (SC-002 replay
  determinism).
- ruff + ruff format + pyright clean.
