# CapableDeputy Demo Scenarios

Twenty-six narrated, executable demos. Each is a pytest-asyncio test
that exercises one or more security properties of the policy engine.
For a categorized map of these demos + the 1126-scenario allow/deny
catalogue + the enforcement suites, see
[**docs/workflow-index.md**](../../docs/workflow-index.md).
Run with `-s` to see the operator-facing narration:

```bash
uv run pytest demos/scenarios/run_all.py --no-cov -s
```

Or pick a single demo:

```bash
uv run pytest demos/scenarios/daily_briefing.py --no-cov -s
```

Every demo prints a top banner listing the security models and flow
patterns it exercises, so you can skim a `-s` run and know what's
about to be demonstrated before reading the steps.

## The lineup

### Single-mechanism intros

| # | Demo | Mechanism |
|---|---|---|
| 2 | `override_workflow` | FR-036 single-use, distinct-attester grants — full FSM |
| 3 | `risk_dial` | FR-030 / SC-010 envelope dial + hard-floor invariant |
| 7 | `optimistic_burn` | FR-034 carve-out boundary (reversible/system + non-egressing) |
| 24 | `bulk_approval_grouped` | Approval bundle — program-hash-pinned re-execution |
| 25 | `data_blind_disclosure` | Pattern ③ ReferenceHandle structural invariants |
| 26 | `flow_pattern_workflows` | 25 practical workflows: five for each LLM flow pattern |

### Clearance / profile / Brewer-Nash

| # | Demo | Mechanism |
|---|---|---|
| 4 | `clinical_records_research` | Brewer-Nash (PHI-meets-egress) + FR-008 BLP |
| 5 | `hr_data_handling` | Profile-bound clearance ceiling |
| 6 | `compliance_review_workflow` | FR-008 BLP across 3 profile ceilings (audit context) |

### Defense-in-depth

| # | Demo | Combination |
|---|---|---|
| 1 | `daily_briefing` | Brewer-Nash + FR-019 + FR-038 + FR-034 + dual-control attester |
| 8 | `prompt_injection_defense` | Pattern ② DUAL_LLM + FR-025 raise-only inspector |

### Multi-mechanism workflows

| # | Demo | Combination |
|---|---|---|
| 9 | `secure_inbox_triage` | Pattern ② + Pattern ③ + FR-025 + Brewer-Nash + FR-019 |
| 10 | `multi_session_handoff` | Session fork + label propagation + Brewer-Nash |
| 11 | `dial_assisted_research` | FR-030 dial + multi-fetch UNTRUSTED_EXTERNAL accumulation |
| 12 | `news_briefing_workflow` | FR-025 + FR-030 + FR-034 + Brewer-Nash + FR-038 (full pipeline) |

### Personal-assistant common workflows

| # | Demo | Workflow |
|---|---|---|
| 13 | `calendar_with_invites` | External invite → accept → forward refused |
| 14 | `meeting_prep_routine` | calendar + inbox + fs + approval bundle for batch attendee email |
| 15 | `task_compartments` | Tasks + financial summary; egress fires financial-meets-email |
| 16 | `task_lifecycle` | Full CRUD: add → list → edit → complete → delete (refused) |
| 17 | `local_doc_qa` | REAL `fs.read` + `fs.read_pdf` |
| 18 | `local_doc_drafting` | REAL `fs.read` + `fs.create` + `fs.modify` |
| 19 | `journal_daily` | fs + tasks + memory.delete override |
| 20 | `email_drafting_workflow` | Drafts (non-egressing) → send refused → override → sent |
| 21 | `expense_categorization` | Receipts → Pattern ② inspect → Pattern ③ handle → financial override |
| 22 | `financial_integrity_biba` | Trusted bank sync updates ledger; emailed statement refused; ratified update allowed |
| 23 | `travel_booking` | Trip = 4 purchases. One-at-a-time vs. bundle |

### Reading the output

Each step renders as:

```
  Step 3  Email the summary to bob@example.com
  ────────────────────────────────────────────
  AI     │ call email.send(to="bob@example.com", …)
  POLICY │ ✗ DENY  ·  health-meets-egress
           Brewer-Nash: PHI in session blocks egress.
  TOOL   │ (skipped)
```

The actor column (`USER`, `AI`, `POLICY`, `TOOL`, `AUDIT`) is keyed to
72-column rows so you can scan a long run for ✓ / ✗ outcomes.

## What runs against REAL tools vs. stubs

| Surface | Real or stub? |
|---|---|
| `memory.*` | Real (in-process) |
| `fs.read` / `fs.read_pdf` / `fs.create` / `fs.modify` | **Real** — reads/writes to disk, uses pypdf |
| `inbox.*` | Stub (in-process `Inbox`) |
| `email.send` | Stub (`EmailOutbox` records; doesn't deliver) |
| `email.draft_*` | Stub (`DraftBox` in-process; full CRUD) |
| `calendar.*` | Stub (`CalendarStore`) |
| `tasks.*` | Stub (`TaskStore`; full CRUD) |
| `web.fetch` | Stub (`WebMock` with pre-loaded URLs) |
| `purchase.queue` | Stub (`PurchaseQueue` records; doesn't buy) |
| `quarantined.extract` | Real, but needs a configured `quarantined_llm` |

Demos that need PDF reading construct the PDF at test-time via
`pypdf.PdfWriter` so they're hermetic — no checked-in fixture files.

## What's NOT here

External-service integrations (Gmail, Slack, GitHub, Google Calendar,
real shopping APIs) are deliberately out of scope; they need the MCP
adapter, which is spec-004 work. The current `fs.*` write tools
deliberately omit `fs.delete` — delete is the highest blast-radius
primitive and belongs with spec-005's binding-driven write_discipline.

## Reading the audit log

Each demo writes `audit.jsonl` under the test's tmp_path. Grep for
event types:

```bash
jq -r '.event_type' audit.jsonl | sort | uniq -c
```
