# CapableDeputy Demo Scenarios

Sixteen narrated, executable demos. Each is a pytest-asyncio test that
exercises one or more security properties of the policy engine. Run
with `-s` to see the operator-facing narration:

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

### Single-mechanism demos

| # | Demo | Mechanism |
|---|---|---|
| 2 | `override_workflow` | FR-036 single-use, distinct-attester grants — full FSM |
| 3 | `risk_dial` | FR-030 / SC-010 envelope dial + hard-floor invariant |
| 5 | `hr_data_handling` | Profile-bound clearance ceiling (FR-008 BLP) |
| 7 | `optimistic_burn` | FR-034 carve-out boundary (reversible/system + non-egressing) |
| 15 | `bulk_approval_grouped` | Approval bundle — program-hash-pinned re-execution |
| 16 | `data_blind_disclosure` | Pattern ③ ReferenceHandle structural invariants |

### Multi-mechanism demos (combinations + workflow context)

| # | Demo | Combination |
|---|---|---|
| 1 | `daily_briefing` | Brewer-Nash + FR-019 social-commitment + FR-038 override + FR-034 carve-out + dual-control attester |
| 4 | `clinical_records_research` | Brewer-Nash (PHI-meets-egress) + FR-008 BLP read-up |
| 6 | `prompt_injection_defense` | Pattern ② DUAL_LLM + FR-025 raise-only inspector (monotone composition) |
| 8 | `secure_inbox_triage` | **Pattern ② + Pattern ③ + FR-025 inspector + Brewer-Nash + FR-019** — canonical multi-mechanism inbox-triage workflow |
| 9 | `multi_session_handoff` | Session fork + label propagation + Brewer-Nash + operator-explicit re-scoping |
| 10 | `dial_assisted_research` | FR-030 envelope dial + multi-fetch UNTRUSTED_EXTERNAL accumulation + SC-010 hard floor |

### Personal-assistant common-workflow demos

| # | Demo | Workflow |
|---|---|---|
| 11 | `calendar_with_invites` | External calendar invite → accept → forward attempt refused (untrusted-meets-egress) |
| 12 | `task_compartments` | Personal tasks + financial summary on the same session; egress attempt fires Brewer-Nash financial-meets-email |
| 13 | `local_doc_qa` | **Real `fs.read` + `fs.read_pdf`** on local files; UNTRUSTED_USER_INPUT propagates; save-locally allowed, egress refused |
| 14 | `travel_booking` | Trip planning: 4 purchases. One-at-a-time vs. bundle. Bundle is program-hash pinned |

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
| Memory store (`memory.*`) | Real (in-process) |
| File system (`fs.read`, `fs.read_pdf`) | **Real** — reads from disk, uses pypdf |
| Inbox (`inbox.*`) | Stub (in-process `Inbox`) |
| Email send (`email.send`) | Stub (`EmailOutbox` records; doesn't deliver) |
| Calendar (`calendar.*`) | Stub (`CalendarStore`) |
| Tasks (`tasks.*`) | Stub (`TaskStore`) |
| Web fetch (`web.fetch`) | Stub (`WebMock` with pre-loaded URLs) |
| Purchase (`purchase.queue`) | Stub (`PurchaseQueue` records; doesn't buy) |
| Quarantined extract (`quarantined.extract`) | Real, but needs a configured `quarantined_llm` |

Demos that need PDF reading construct the PDF at test-time via
`pypdf.PdfWriter` so they're hermetic — no checked-in fixture files.

## What's NOT here

External-service integrations (Gmail, Slack, GitHub, Google Calendar,
real shopping APIs) are deliberately out of scope; they need the MCP
adapter, which is spec-004 work (`U001-U020`).

## Reading the audit log

Each demo writes `audit.jsonl` under the test's tmp_path (printed at
the end of the marquee demo). Grep for event types to see the
load-bearing chokepoints in action:

```bash
jq -r '.event_type' audit.jsonl | sort | uniq -c
```

Expect: `tool.dispatched`, `tool.refused`, `policy.decided`,
`label.propagated`, `override.requested`, `override.attested`,
`pattern3.handle_bind`.
