# CapableDeputy Demo Scenarios

Twelve narrated, executable demos. Each is a pytest-asyncio test that
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
| 11 | `bulk_approval_grouped` | Approval bundle — program-hash-pinned re-execution |
| 12 | `data_blind_disclosure` | Pattern ③ ReferenceHandle structural invariants |

### Multi-mechanism demos (combinations + workflow context)

| # | Demo | Combination |
|---|---|---|
| 1 | `daily_briefing` | Brewer-Nash + FR-019 social-commitment + FR-038 override + FR-034 carve-out + dual-control attester |
| 4 | `clinical_records_research` | Brewer-Nash (PHI-meets-egress) + FR-008 BLP read-up |
| 6 | `prompt_injection_defense` | Pattern ② DUAL_LLM + FR-025 raise-only inspector (monotone composition) |
| 8 | `secure_inbox_triage` | **Pattern ② + Pattern ③ + FR-025 inspector + Brewer-Nash + FR-019** — the canonical multi-mechanism inbox-triage workflow |
| 9 | `multi_session_handoff` | Session fork + label propagation + Brewer-Nash + operator-explicit re-scoping |
| 10 | `dial_assisted_research` | FR-030 envelope dial + multi-fetch UNTRUSTED_EXTERNAL accumulation + SC-010 hard floor |

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

## What's NOT here

These demos use stub tools and an in-memory store. They exercise the
policy engine and the substrate ports — the parts of CapableDeputy
that are load-bearing for the security argument. **Real-world**
integration tests (Gmail, Slack, GitHub, etc.) live under spec 004
once the MCP adapter lands.

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
