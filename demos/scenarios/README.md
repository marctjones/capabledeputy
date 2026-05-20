# CapableDeputy Demo Scenarios

Nine narrated, executable demos. Each is a pytest-asyncio test that
exercises one or more security properties of the policy engine. Run
with `-s` to see the operator-facing narration:

```bash
uv run pytest demos/scenarios/run_all.py --no-cov -s
```

Or pick a single demo:

```bash
uv run pytest demos/scenarios/daily_briefing.py --no-cov -s
```

## The lineup

| # | Demo | What it proves |
|---|---|---|
| 1 | `daily_briefing` | FR-019 social-commitment + FR-038 override workflow on the most boring imaginable workflow |
| 2 | `override_workflow` | FR-036 single-use, distinct-attester grants; cannot self-attest, cannot reuse |
| 3 | `risk_dial` | FR-030 / SC-010 operator-owned autonomy dial steers within declared envelopes; hard-floor cells are immovable |
| 4 | `clinical_records_research` | Brewer-Nash conflict rule + FR-008 BLP read-up refusal |
| 5 | `hr_data_handling` | Profile-bound clearance; same caps, different profile, different outcome |
| 6 | `prompt_injection_defense` | Pattern (2) DUAL_LLM + FR-025 raise-only inspector — taint can only go up |
| 7 | `optimistic_burn` | FR-034 carve-out for reversible/system + non-egressing; 100 writes, 0 prompts |
| 8 | `bulk_approval_grouped` | Approval bundle — one review, many gates, source-hash pinned re-execution |
| 9 | `data_blind_disclosure` | Pattern (3) ReferenceHandle — planner manipulates UUIDs; dispatcher binds AFTER decide() |

## What's NOT here

These demos use stub tools and an in-memory store. They exercise the
policy engine and the substrate ports — the parts of CapableDeputy
that are load-bearing for the security argument. **Real-world**
integration tests (Gmail, Slack, GitHub, etc.) live under spec 004
once the MCP adapter lands.

## Reading the audit log

Each demo writes `audit.jsonl` under the test's tmp_path. The path is
printed at the end of the marquee demo. Grep for event types to see
the load-bearing chokepoints in action:

```
jq -r '.event_type' audit.jsonl | sort | uniq -c
```

Expect: `tool.dispatched`, `tool.refused`, `policy.decided`,
`label.propagated`, `override.requested`, `override.attested`,
`pattern3.handle_bind`.
