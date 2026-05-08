# CapableDeputy Demos

Nine end-to-end walkthroughs across three buckets: **security
demonstrations** that show what the architecture prevents, **user
workflow demos** that show what it's *for*, and **adversarial
demos** that flip the architecture and use external agents.

| # | Demo | Bucket | Audience | Requires | Time |
|---|---|---|---|---|---|
| 01 | [Prescription-to-Wife](01-prescription-to-wife.md) | security | security/AI safety | nothing | ~3 min |
| 02 | [Real Claude Blocked, Then Approved](02-real-claude-blocked-then-approved.md) | security | demo to anyone | API key | ~5 min |
| 03 | [Claude Code as Adversarial Agent](03-claude-code-adversarial.md) | adversarial | architecture / positioning | Claude Code subscription | ~7 min |
| 04 | [Daily Briefing](04-daily-briefing.md) | workflow | "what's this *for*?" | nothing | ~3 min |
| 05 | [Email Triage](05-email-triage.md) | workflow | most-asked workflow | nothing | ~3 min |
| 06 | [Recurring Purchase](06-recurring-purchase.md) | workflow | approval-fatigue story | nothing | ~2 min |
| 07 | [Untrusted Web Research](07-untrusted-research.md) | workflow + security | indirect injection | nothing | ~3 min |
| 08 | [Note-Taking](08-note-taking.md) | workflow | compartment hygiene | nothing | ~2 min |
| 09 | [Accountant Summary](09-accountant.md) | workflow + security | cross-compartment send | nothing | ~3 min |
| 10 | [Bundled Approvals](10-bundled-approvals.md) | workflow + UX | approval-fatigue answer | nothing | ~3 min |

## How they fit together

- **Security demos (01-03)** prove the architectural property — that
  PHI can't egress without an explicit gated declassification —
  through three lenses: scripted, real-LLM, and external-MCP-host.
- **Workflow demos (04-06, 08)** show the assistant capabilities a
  user *actually* wants and the idioms that keep them safe (schema
  extraction, approval patterns, compartment-per-session).
- **Workflow + security demos (07, 09)** sit at the boundary: each
  shows a real workflow whose security is the entire point of doing
  it through CapableDeputy at all (web research that can't exfiltrate;
  financial summaries that can't leak exact numbers).

## What's verified by each

| Property | 01 | 02 | 03 | 04 | 05 | 06 | 07 | 08 | 09 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Health → email egress structurally denied | ✓ | ✓ | ✓ | | | | | ✓ | | |
| Untrusted → email egress structurally denied | | | | ✓ | | | ✓ | | | |
| Financial → email egress structurally denied | | | | | | | | | ✓ | |
| Financial → purchase requires approval | | | | | | | | | | ✓ |
| Approval workflow spawns purpose session | ✓ | ✓ | ~ | | ✓ | | | | ✓ | ✓ |
| Schema extraction declassifies | ~ | | | ✓ | ✓ | | ✓ | | ✓ | |
| Pattern rules auto-approve recurring | | | | | ~ | ✓ | | | | |
| Bundled approvals (N gates → 1 decision) | | | | | | | | | | ✓ |
| Source-hash mismatch detection | | | | | | | | | | ✓ |
| Audit log captures every step | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Real LLM correctly identifies fired rule | — | ✓ | ~ | — | — | — | — | — | — | — |
| External MCP host can drive policy | — | — | ✓ | — | — | — | — | — | — | — |
| Compartments stay separate across sessions | ~ | | | | | | | ✓ | | |
| Schema as privacy filter (bucketing) | | | | | | | ~ | | ✓ | |

`✓` shown directly · `~` shown indirectly · `—` not applicable

## Run them all

```bash
# Deterministic demos (no API key)
uv run pytest tests/test_e2e_prescription_to_wife.py \
              tests/test_e2e_daily_briefing.py \
              tests/test_e2e_email_triage.py \
              tests/test_e2e_recurring_purchase.py \
              tests/test_e2e_web_research.py \
              tests/test_e2e_notes.py \
              tests/test_e2e_accountant.py -v

# Real-LLM demo (API key required)
ANTHROPIC_API_KEY=$(cat CLAUDEAPI.KEY) \
  uv run pytest tests/integration/ -v
```

All deterministic demos run in <10s combined.

## What's still missing

These would round out the picture but aren't shipped as demos yet:

- **Programmatic mode demo** — show the planner emitting a Python
  program; `capdep dry-run` flagging a hidden violation; user
  reviews; `capdep run` executes.
- **Tool-token aliasing demo** — `capdep session new --tool-tokens`
  then show that the LLM-visible names are random.
- **Multi-tenant household demo** — Alice and Bob as `Tenant`s with
  per-compartment policies.
- **Federation demo** — phone-to-laptop signed approval handoff.
- **Per-tool isolation demo** — fetch server fails to read /etc/shadow
  because its container has `network=none` and an empty volume bind.

These are recorded in the project's open issues for future demos.
