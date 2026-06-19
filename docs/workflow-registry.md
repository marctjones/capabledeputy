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
| Narrated demos (`demos/scenarios/`) | 26 | ✅ | 26 passed |
| Adversarial / pressure suite (`tests/test_workflow_pressure.py`) | 18 | ✅ | 18 passed |
| Flow-pattern / assurance probes (`tests/test_security_alignment_probes.py`, pattern ②/③, provenance, dispatcher) | 28 | ✅ | 28 passed |
| Decision-inspector suite (`scripts/policy_inspectors.py`) | 4 | ✅ | passed |
| Enforcement rule-cases (`scripts/policy_{allow,deny,…}.py`) | 36 | ✅ | 41 passed (incl. guards) |
| Scenario catalogue (`scripts/policy_assistant.py`) | 1126 | ✅ | 1126 passed (~11s) |
| Prior unimplemented workflow gaps | 7 | ✅ | covered or recategorized |
| Standing explicit boundaries | 2 | 🟡 | labeling oracle; model-reasoning contamination |

**~84 distinct named workflows/properties + 28 focused assurance probes +
the 1126-permutation catalogue.** Almost everything green; the *value* is
split between regression guards (most), adversarial/model-derived pressure
tests, and the findings logged at the bottom.

A standing caveat on "results": a green regression guard means *no
regression today* — it does not independently prove correctness. The
model-derived pressure tests (§2) and the findings log (§7) are where real
signal lives.

---

## 1. Narrated demos (26) — ✅ all green

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
| financial_integrity_biba | trusted bank sync updates ledger; emailed statement refused; ratified update allowed | ✅ guard |
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

> Historical note: the pre-registry demo suite broke after the R7 redesign
> (deleted `Label`/`AxisA`) and was silently un-tested
> (`testpaths=["tests"]`); migrated + CI-guarded — see finding F4.

---

## 2. Adversarial / pressure suite (18) — ✅ all green — the high-signal set

`tests/test_workflow_pressure.py`. **Model-derived** (expectations from
BLP/Brewer-Nash, not probed), **multi-step** (taint produced by reading),
**adversarial** (tries to break the guarantee), and **runtime-composed**
(operator scripts / overrides / purposes through the real chokepoint).

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
| purpose_limited_workflow_blocks_inadmissible_contamination | employee-evaluation can use work-performance data but health cannot be granted/delegated in | purpose pressure | ✅ — practical purpose-contamination boundary |
| frequency_aggregation_tightens_real_chokepoint_after_n_sends | first 3 send-like dispatches allowed; 4th requires approval via history-aware operator script | aggregation pressure | ✅ |
| sanctioned_declassification_lowers_taint_enabling_egress | certified schema declassifier lowers untrusted taint; raw path still denied | declassification | ✅ |
| uncertified_taint_removal_is_refused | non-declassifier label removal raises | declassification adversarial | ✅ |
| v2_communication_egress_requires_approval_by_default | real v2 config routes ordinary communication egress to approval | v2 pressure | ✅ |
| v2_purchase_keeps_irreversible_deny | real v2 config keeps purchase/commitment at deny | v2 pressure | ✅ — pins F2 |
| v2_super_sensitive_egress_requires_override | restricted-tier communication escalates to override-required | v2 pressure | ✅ |
| v2_super_sensitive_egress_resolved_by_override_grant | exact single-use override allows once, then expires by use | v2 pressure | ✅ |

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

## 6. Prior gap list — now covered or recategorized

| Workflow class | Purpose | Why it matters | Status |
|---|---|---|---|
| Pattern ② dual-LLM declassification e2e | confidential read → quarantined extract → schema-declassified summary; planner never sees raw bytes | the safe-disclosure path | ✅ `flow_pattern_workflows`, `test_pattern2_dual_llm_adversarial.py`, `test_security_alignment_probes.py` |
| Pattern ③ reference-handle redirection-resistance | injected/forged/stolen handles cannot redirect a handle-bound destination | the confused-deputy moat | ✅ `test_pattern3_redirection_resistance.py` + `data_blind_disclosure` |
| Pattern ⑤ sealed containment | effect runs contained; output keeps source labels (containment ≠ declassification) | prevents the sandbox-as-declassifier footgun | ✅ `flow_pattern_workflows` + `test_security_alignment_probes.py` |
| Certified declassification | the ONLY sanctioned taint-lowering; abuse attempts fail | declassifier is the trust hinge | ✅ `test_workflow_pressure.py` + `test_declassifier_wired.py` |
| Operator policy DSL + envelope dial | `rules.yaml`/RulePredicates, envelope bounded-relax, profiles/bindings under real config | expressive operator layer | ✅ `policy_inspectors`, `risk_dial`, `dial_assisted_research`, v2 pressure tests |
| Frequency / aggregation at scale | "N sends/reads → tighten" using bounded read-only history | defense T4 / cumulative behavior | ✅ real chokepoint workflow in `test_workflow_pressure.py` |
| Purpose-contamination | block unrelated sensitive categories from entering a purpose-scoped workflow | practical purpose-limitation boundary | ✅ grant/delegation pressure test; deeper model-reasoning contamination remains an explicit non-goal |

Remaining explicit boundaries:

| Boundary | Why it remains | Evidence |
|---|---|---|
| Labeling oracle completeness | protections fire only on labels that exist; unlabeled sensitive data is silently underprotected | standing test `oracle_unlabeled_data_silently_unprotected` |
| Model-reasoning purpose contamination | if data is admissible and no egress occurs, proving it did not inappropriately influence model cognition would require model-internal interpretability | documented non-goal in `governance-scope.md` / `responsible-ai-frameworks.md`; practical read-admissibility is tested |

---

## 7. Findings log — what running these actually surfaced

Concrete regressions/opportunities identified while building + reviewing
(this is the part that isn't just "green"):

- **F1 — bounded-relax floor bug (FIXED).** The activated inspector layer
  let a `relax` cross a structural DENY (a script could relax BLP/Biba/
  capability DENY → ALLOW). Found in review; clamped to REQUIRE_APPROVAL-
  only; guarded by `bounded-relax-floor-refused` + a unit test.
- **F2 — v2 egress defaults are pinned (UPDATED).** Under the real config,
  ordinary communication egress routes to human approval, purchases/commitments
  remain denied by `reversibility-irreversible`, and restricted-tier
  communication escalates to `override_required`. Guarded by v2 pressure tests.
- **F3 — labeling-oracle gap (STANDING TEST).** Unlabeled sensitive data is
  read with no label → defense silently absent (governance contingency #1).
  Encoded as `oracle_unlabeled_data_silently_unprotected` so it stays visible.
  *Opportunity:* content-scan rules + the raise-only LLM labeler.
- **F4 — pre-registry demos silently broken (FIXED).** The demo suite imported
  deleted symbols post-R7 and never ran in CI (`testpaths=["tests"]`).
  Migrated + added `test_demos_smoke.py` so it can't recur.
- **F5 — outbound email was enabled (FIXED, on main).** GWS + IMAP send tools
  registered; added `disabled_tools`/`disabled_kinds` and disabled them.
- **F6 — provenance masking (OBSERVED).** `fs.read`'s EXTERNAL_UNTRUSTED
  provenance dominates every egress deny, so the fs *category* label can't be
  isolated at an egress sink (only in the label state). *Opportunity:* a
  non-egress decision or a declassification path to exercise the category leg.
- **F7 — catalogue circularity (ADDRESSED).** The 1126 are correct-by-
  construction (derived from the engine). The §2 pressure suite is the
  model-derived counter-measure; more model-derived properties wanted.

> CI cadence: §1–§4 + the §2/§6/§7 guards run in the default fast suite; the
> 1126 catalogue (§5) runs as a `slow` batch. Re-run any surface via the
> commands above; a red result is a regression or a model violation.
