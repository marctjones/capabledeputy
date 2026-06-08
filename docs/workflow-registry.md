# Workflow registry

Every workflow we've identified — its purpose, where it lives, whether it
is implemented + tested, and what running it tells us (regression guard vs.
finding surfaced). Companion to the navigational [workflow-index.md](workflow-index.md);
this one tracks **status + results**.

Status legend: ✅ implemented + tested (green in CI) · 🟡 partial · 🔭
identified, not implemented. "Result" = what a run yields today.

## At a glance

| Surface | Count | Status | Last run |
|---|---|---|---|
| Narrated demos (`demos/scenarios/`) | 25 | ✅ | 25 passed |
| Adversarial / pressure suite (`tests/test_workflow_pressure.py`) | 11 | ✅ | 11 passed |
| Decision-inspector suite (`scripts/policy_inspectors.py`) | 4 | ✅ | passed |
| Enforcement rule-cases (`scripts/policy_{allow,deny,…}.py`) | 36 | ✅ | 41 passed (incl. guards) |
| Scenario catalogue (`scripts/policy_assistant.py`) | 1126 | ✅ | 1126 passed (~11s) |
| Identified, not yet implemented (the gaps) | 7 | 🔭 | — |

**~76 distinct named workflows/properties + the 1126-permutation catalogue
+ 7 identified-unbuilt.** Almost everything green; the *value* is split
between regression guards (most) and the findings logged at the bottom.

A standing caveat on "results": a green regression guard means *no
regression today* — it does not independently prove correctness. The
model-derived pressure tests (§2) and the findings log (§7) are where real
signal lives.

---

## 1. Narrated demos (25) — ✅ all green

Operator-facing transcripts; each asserts its decisions, so it doubles as a
regression test. Run: `uv run pytest demos/scenarios/run_all.py --no-cov -s`.
CI-guarded by `tests/test_demos_smoke.py`.

| Workflow | Purpose | Result |
|---|---|---|
| morning_assistant | everyday routine: reads, reminder, blocked health exfil, gated buy | ✅ guard |
| daily_briefing | marquee knowledge-worker briefing | ✅ guard |
| secure_inbox_triage | triage benign + injection mail (②③ + inspector) | ✅ guard |
| prompt_injection_defense | injection-tainted session can't egress | ✅ guard |
| email_drafting_workflow | compose locally, send across the chokepoint | ✅ guard |
| calendar_with_invites | external invite = egress; taint propagates | ✅ guard |
| local_doc_qa | real fs.read / read_pdf then gated egress | ✅ guard |
| local_doc_drafting | read → create → modify → egress refused | ✅ guard |
| data_blind_disclosure | planner sees only a handle UUID (Pattern ③) | ✅ guard |
| expense_categorization | receipts → report → submit (financial taint) | ✅ guard |
| travel_booking | one-at-a-time vs. bundled approvals | ✅ guard |
| bulk_approval_grouped | one prompt, many gates | ✅ guard |
| clinical_records_research | health read-up-only; egress denied (BLP+BN) | ✅ guard |
| hr_data_handling | profile-bound clearance ceilings | ✅ guard |
| compliance_review_workflow | BLP across three profile ceilings | ✅ guard |
| news_briefing_workflow | fetch → inspect → dial → draft → send | ✅ guard |
| dial_assisted_research | the envelope dial across a workflow | ✅ guard |
| meeting_prep_routine | calendar + fs + inbox + approval bundle | ✅ guard |
| journal_daily | fs + tasks + destructive delete (override) | ✅ guard |
| task_lifecycle | full CRUD with destructive-op + reversibility | ✅ guard |
| task_compartments | Brewer-Nash on personal categories | ✅ guard |
| risk_dial | the operator-owned autonomy dial | ✅ guard |
| optimistic_burn | reversible/system + non-egressing → AUTO | ✅ guard |
| override_workflow | dual-control + single-use override | ✅ guard |
| multi_session_handoff | taint travels along a session fork | ✅ guard |

> All 25 broke after the R7 redesign (deleted `Label`/`AxisA`) and were
> silently un-tested (`testpaths=["tests"]`); migrated + CI-guarded — see
> finding F4.

---

## 2. Adversarial / pressure suite (11) — ✅ all green — the high-signal set

`tests/test_workflow_pressure.py`. **Model-derived** (expectations from
BLP/Brewer-Nash, not probed), **multi-step** (taint produced by reading),
**adversarial** (tries to break the guarantee).

| Workflow | Purpose | Kind | Result |
|---|---|---|---|
| oracle_fs_read_attaches_category | fs labeler tags a financial file on read | oracle | ✅ |
| oracle_unlabeled_data_silently_unprotected | **honest GAP**: unlabeled financial doc read with NO label | oracle gap | ✅ — keeps F3 visible |
| oracle_memory_read_propagates_stored_label | stored label rides through the read | oracle | ✅ |
| taint_read_health_then_external_email_denied | health read → egress denied (label from read) | multi-step | ✅ |
| taint_any_file_read_then_egress_denied | any file read → untrusted → egress denied | multi-step | ✅ |
| taint_accumulates_across_steps | health+financial accumulate (sticky) | multi-step | ✅ |
| injection_content_does_not_change_outcome | injection in content → still denied | adversarial | ✅ |
| confused_deputy_no_capability_denied | no grant → deny | adversarial | ✅ |
| confused_deputy_out_of_scope_pattern_denied | scoped grant can't reach elsewhere | adversarial | ✅ |
| model_confidential_read_blocks_external_egress | property over {health,financial}×{email,purchase} | model-derived | ✅ — fails if engine ever allows |
| v2_real_config_denies_irreversible_egress | pins the reversibility-irreversible default | v2 pressure | ✅ — pins F2 |

---

## 3. Decision-inspector suite (4) — ✅ green

`scripts/policy_inspectors.py` — the only suite exercising **Starlark + the
real v2 config**.

| Workflow | Purpose | Result |
|---|---|---|
| starlark-tighten-read | script TIGHTENS an ALLOW read → approval | ✅ |
| starlark-relax-destructive-scratch | script RELAXES a destructive-write approval | ✅ |
| starlark-relax-destructive-nonscratch-still-gated | relax does NOT over-fire | ✅ |
| bounded-relax-floor-refused | greedy relax CANNOT cross a DENY floor | ✅ — guards F1 |

---

## 4. Enforcement rule-cases (36) — ✅ green

`scripts/policy_{engine_harness,allow,deny,require_approval,constraints,labels,workflows}.py`,
run by `tests/test_policy_scripts.py`. Per-rule matrix:
allow(5) · deny(7) · require_approval(3) · constraints(3) · labels(8) ·
workflows(7) · smoke(3). Regression guards for the always-on engine
invariants + capability checks.

## 5. Scenario catalogue (1126) — ✅ green — broad regression net

`scripts/policy_assistant.py` (slow batch in `tests/test_assistant_scenarios.py`).
3 workflow shapes (email egress · purchase egress · notes CRUD) × data
categories × surface vocabulary + mis-authorization negatives.
**Correct-by-construction** (derived from a probed matrix) → catches
regressions in the deny-matrix, does not independently prove correctness,
and runs only the legacy engine path (no v2 pipeline / Starlark / labeler).

---

## 6. Identified, NOT yet implemented (🔭) — the real next work

| Workflow class | Purpose | Why it matters | Status |
|---|---|---|---|
| Pattern ② dual-LLM declassification e2e | confidential read → quarantined extract → schema-declassified summary egresses; planner never sees raw bytes | the safe-disclosure path; almost no pressure today | 🔭 |
| Pattern ③ reference-handle redirection-resistance | injected "send to attacker" can't redirect a handle-bound destination | the confused-deputy moat; only a demo touches it | 🔭 |
| Pattern ⑤ sealed containment | effect runs contained; output keeps source labels (containment ≠ declassification) | a labeled footgun if assumed otherwise | 🔭 |
| Certified declassification | the ONLY sanctioned taint-lowering; abuse attempts must fail | untested; declassifier is the trust hinge | 🔭 |
| Operator policy DSL + envelope dial | `rules.yaml` RulePredicates, envelope bounded-relax, profiles/bindings under real config | the expressive layer is barely tested | 🔭 |
| Frequency / aggregation at scale | "N sends/reads → tighten" beyond the single inspector unit test | defense T4; needs threaded history at scale | 🔭 |
| Purpose-contamination | sensitive data influencing a decision it has no bearing on | P4, the explicitly-unbuilt principle | 🔭 |

---

## 7. Findings log — what running these actually surfaced

Concrete regressions/opportunities identified while building + reviewing
(this is the part that isn't just "green"):

- **F1 — bounded-relax floor bug (FIXED).** The activated inspector layer
  let a `relax` cross a structural DENY (a script could relax BLP/Biba/
  capability DENY → ALLOW). Found in review; clamped to REQUIRE_APPROVAL-
  only; guarded by `bounded-relax-floor-refused` + a unit test.
- **F2 — v2 denies irreversible egress by default (PINNED).** Under the real
  config, email/purchase (irreversible) deny with `reversibility-irreversible`
  unless an envelope grants reversibility. Surfaced by `policy_inspectors`,
  pinned by the v2 pressure test. *Opportunity:* decide whether this default
  is too strict for normal egress (the demos sidestep it via `policy_context=None`).
- **F3 — labeling-oracle gap (STANDING TEST).** Unlabeled sensitive data is
  read with no label → defense silently absent (governance contingency #1).
  Encoded as `oracle_unlabeled_data_silently_unprotected` so it stays visible.
  *Opportunity:* content-scan rules + the raise-only LLM labeler.
- **F4 — 25 demos silently broken (FIXED).** All demos imported deleted
  symbols post-R7 and never ran in CI (`testpaths=["tests"]`). Migrated +
  added `test_demos_smoke.py` so it can't recur.
- **F5 — outbound email was enabled (FIXED, on main).** GWS + IMAP send tools
  registered; added `disabled_tools`/`disabled_kinds` and disabled them.
- **F6 — provenance masking (OBSERVED).** `fs.read`'s EXTERNAL_UNTRUSTED
  provenance dominates every egress deny, so the fs *category* label can't be
  isolated at an egress sink (only in the label state). *Opportunity:* a
  non-egress decision or a declassification path to exercise the category leg.
- **F7 — catalogue circularity (ADDRESSED).** The 1126 are correct-by-
  construction (derived from the engine). The §2 pressure suite is the
  model-derived counter-measure; more model-derived properties wanted.

> CI cadence: §1–§4 + the §2/§7 guards run in the default fast suite; the
> 1126 catalogue (§5) runs as a `slow` batch. Re-run any surface via the
> commands above; a red result is a regression or a model violation.
