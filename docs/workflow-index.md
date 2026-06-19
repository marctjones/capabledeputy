# Workflow index

> For **status + results** (implemented? tested? what a run reveals —
> regression guard vs. finding), see the companion
> [**workflow-registry.md**](workflow-registry.md).

A categorized map of every workflow CapableDeputy demonstrates and tests —
the things people actually use a personal AI assistant for, and how the
policy engine responds (allow / require-approval / deny). Three surfaces:

- **Narrated demos** (`demos/scenarios/`, 26) — operator-facing
  USER/AI/TOOL/POLICY transcripts. Run:
  `uv run pytest demos/scenarios/run_all.py --no-cov -s`
- **Scenario catalogue** (`scripts/policy_assistant.py`, 1126) — the broad
  allow/deny matrix across realistic tasks. Run:
  `uv run python scripts/policy_assistant.py`
- **Enforcement suites** (`scripts/policy_*.py`, via
  `tests/test_policy_scripts.py`) — the focused per-rule matrix.

All run with a FakeLLM + in-memory native tools: **no real LLM, network,
email, purchase, or calendar side effects.**

**Legend** — outcome: ✓ allow · ? require-approval · ✗ deny.
Mechanisms: BLP (Bell-LaPadula) · Biba · BN (Brewer-Nash) · CW
(Clark-Wilson) · OCap (object-capability) · IFC (Denning information flow).
Flow patterns: ① turn-level · ② dual-LLM · ③ reference-handle ·
④ programmatic · ⑤ sealed.

---

## 1. Narrated demos by category

### Inbox & communication
| Demo | What it shows | Mechanisms |
|---|---|---|
| `secure_inbox_triage` | triage benign + injection mail safely | ②③ + raise-only inspector + BN |
| `prompt_injection_defense` | injection-tainted session can't egress | ② + raise-only inspector + IFC |
| `email_drafting_workflow` | compose locally (✓), send across the chokepoint | ① CW |
| `calendar_with_invites` | external invite = egress; taint propagates | ① IFC |

### Documents & files
| Demo | What it shows | Mechanisms |
|---|---|---|
| `local_doc_qa` | real `fs.read` / `fs.read_pdf`, then gated egress | ① IFC |
| `local_doc_drafting` | read → create → modify → **egress refused** | ① IFC BLP |
| `data_blind_disclosure` | planner sees only a handle UUID | ③ reference-handle (FR-047) |

### Finance & shopping
| Demo | What it shows | Mechanisms |
|---|---|---|
| `expense_categorization` | receipts → report → submit (financial taint) | raise-only inspector + BN |
| `financial_integrity_biba` | trusted bank sync updates ledger; emailed statement denied; ratified update allowed | Biba + CW |
| `travel_booking` | one-at-a-time vs. bundled approvals | ① CW (approval bundles) |
| `bulk_approval_grouped` | one prompt, many gates | CW separation-of-duty |

### Health & compliance
| Demo | What it shows | Mechanisms |
|---|---|---|
| `clinical_records_research` | health data is read-up-only, egress denied | BLP + BN |
| `hr_data_handling` | profile-bound clearance ceilings | BLP (FR-008) |
| `compliance_review_workflow` | BLP across three profile ceilings | BLP |

### Research & briefing
| Demo | What it shows | Mechanisms |
|---|---|---|
| `daily_briefing` | the marquee knowledge-worker routine | ①② IFC |
| `news_briefing_workflow` | fetch → inspect → dial → draft → send | ② inspector + envelope dial |
| `dial_assisted_research` | the envelope dial across a real workflow | envelope (FR-030) |
| `meeting_prep_routine` | calendar + fs + inbox + approval bundle | ① CW |

### Notes, tasks & daily routine
| Demo | What it shows | Mechanisms |
|---|---|---|
| `morning_assistant` | a morning routine: reads, reminder, blocked exfil, gated buy | ① BLP BN IFC OCap |
| `journal_daily` | `fs.*` + `tasks.*` + destructive delete (override) | CW destructive-op gate |
| `task_lifecycle` | full CRUD with destructive-op + reversibility | CW reversibility |
| `task_compartments` | Brewer-Nash on personal categories | BN |

### Autonomy & approval controls
| Demo | What it shows | Mechanisms |
|---|---|---|
| `risk_dial` | the operator-owned autonomy dial | envelope / risk preference |
| `optimistic_burn` | reversible/system + non-egressing → AUTO-allow | reversibility (FR-044) |
| `override_workflow` | dual-control + single-use override | CW dual-control (FR-036) |
| `multi_session_handoff` | taint travels along a session fork | IFC (sticky labels) |
| `flow_pattern_workflows` | five practical workflows for each of the five LLM flow patterns | ①②③④⑤ |

---

## 2. Scenario catalogue (1126) — the broad allow/deny matrix

`scripts/policy_assistant.py` enumerates realistic tasks × data categories ×
sinks, each asserted against the engine. Run as a `slow` batch in CI
(`tests/test_assistant_scenarios.py`).

| Group | Count | Sink | Covers |
|---|---|---|---|
| `email-*` | 528 | `email.send` | every recipient × task × data category |
| `buy-*` | 568 | `purchase.queue` | every vendor × item × data category, + over-limit |
| `note-*` | 30 | `memory.{read,update,delete}` | reads (✓) + destructive (?) |
| negatives | (in above) | — | no-capability, out-of-scope pattern, over-max |

**The decision matrix every scenario encodes:**

| Session carries → | email egress | purchase egress | local read | destructive write |
|---|---|---|---|---|
| nothing / personal / trusted / work | ✓ allow | ✓ allow | ✓ allow | ? approval |
| **health** | ✗ deny (health-meets-egress) | ✗ deny | ✓ | ? |
| **financial** | ✗ deny (financial-meets-email) | ? approval (financial-meets-purchase) | ✓ | ? |
| **untrusted** | ✗ deny (untrusted-meets-egress) | ✗ deny | ✓ | ? |
| no capability / out-of-scope / over-limit | ✗ deny | ✗ deny | — | — |

---

## 3. Enforcement suites (focused per-rule matrix)

Run in the default test suite via `tests/test_policy_scripts.py`.

| Suite | Scenarios | Focus |
|---|---|---|
| `policy_engine_harness` | 3 | smoke |
| `policy_allow` | 5 | ALLOW paths across the full tool sweep |
| `policy_deny` | 7 | every denying rule |
| `policy_require_approval` | 3 | the human-in-the-loop gates |
| `policy_constraints` | 3 | capability constraints |
| `policy_labels` | 8 | each label's egress effect |
| `policy_workflows` | 7 | multi-step business workflows |
| `policy_inspectors` | 4 | the Starlark decision-inspector layer end-to-end |

---

## Cross-reference — which workflows exercise which mechanism

- **Bell-LaPadula (confidentiality):** clinical_records_research,
  hr_data_handling, compliance_review_workflow, local_doc_drafting,
  morning_assistant + every health/financial scenario.
- **Biba (integrity):** financial_integrity_biba, flow_pattern_workflows,
  and the integrity-floor rule cases.
- **Brewer-Nash (conflict):** task_compartments, clinical_records_research,
  expense_categorization + the financial/health-meets-egress scenarios.
- **Clark-Wilson (gated transactions / approval):** override_workflow,
  bulk_approval_grouped, travel_booking, financial_integrity_biba,
  task_lifecycle, journal_daily.
- **Object-capability (confused-deputy):** the no-capability /
  out-of-scope negatives, morning_assistant.
- **IFC / sticky labels:** prompt_injection_defense, secure_inbox_triage,
  multi_session_handoff, calendar_with_invites, local_doc_qa.
- **Flow patterns:** ① flow_pattern_workflows / local_doc_qa /
  calendar_with_invites; ② flow_pattern_workflows / daily_briefing /
  prompt_injection_defense / secure_inbox_triage / news_briefing; ③
  flow_pattern_workflows / data_blind_disclosure / secure_inbox_triage; ④
  flow_pattern_workflows / bulk_approval_grouped; ⑤ flow_pattern_workflows.
- **Decision-inspector layer (Starlark):** policy_inspectors (relax /
  tighten / bounded-floor / relationship-aware).
