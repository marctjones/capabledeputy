# CapableDeputy Demos

Twenty-one end-to-end walkthroughs across four buckets: **security
demonstrations**, **user workflow demos**, **adversarial demos**, and
**feature spotlights**.

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
| 11 | [Destructive-Op Gate](11-destructive-ops.md) | security + UX | Clark-Wilson / CRUD decomposition | nothing | ~3 min |
| 12 | [Programmatic Mode](12-programmatic-mode.md) | feature | LLM emits Python; static analysis | nothing | ~3 min |
| 13 | [Tool-Token Aliasing](13-tool-aliasing.md) | feature | strict ocap | nothing | ~2 min |
| 14 | [Multi-Tenant Household](14-multi-tenant.md) | feature | per-principal compartments | nothing | ~2 min |
| 15 | [Federation](15-federation.md) | feature | phone-to-laptop approval handoff | nothing | ~3 min |
| 16 | [Per-Tool Isolation](16-per-tool-isolation.md) | feature + ops | container hardening | nothing | ~2 min |
| 17 | [Interactive REPL](17-interactive-repl.md) | feature + UX | talk to the agent live | API key | ~5 min |
| 18 | [Recoverable Blocks](18-recoverable-blocks.md) | security + UX | DENY vs approval vs declassify | nothing | ~5 min |
| 19 | [Time-Bounded Capabilities](19-time-bounded-capabilities.md) | feature + security | self-expiring authority | nothing | ~3 min |
| 20 | [Rate-Limited Capabilities](20-rate-limited-capabilities.md) | feature + security | self-throttling authority | nothing | ~3 min |
| 21 | [Unified Console](21-unified-console.md) | feature + UX | drive+monitor+approve in one window | API key | ~5 min |

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
- **Feature spotlights (10-21)** cover bundled approvals, destructive-op
  gates, programmatic mode, tool-token aliasing, multi-tenant labels,
  federation, per-tool isolation, REPL/console operation, recoverable
  blocks, time/rate-limited capabilities, and the unified console.

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

### Feature & v0.5–v0.7 properties (demos 11–21)

The matrix above covers the v0.1–v0.4 era. Demos 11–21 verify the
later capability-constraint, programmatic, federation, and
surface/UX properties:

| Property | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Destructive-op gate (Clark-Wilson) | ✓ | | | | | | | ~ | | | |
| Programmatic mode / static analysis | | ✓ | | | | | | | | | |
| Tool-token aliasing (strict ocap) | | | ✓ | | | | | | | | |
| Per-tenant compartment scoping | | | | ✓ | | | | | | | |
| Signed federation envelope | | | | | ✓ | | | | | | |
| Per-tool container isolation | | | | | | ✓ | | | | | |
| Interactive REPL drives real agent | | | | | | | ✓ | ~ | | | ✓ |
| DENY vs approval vs declassify map | | | | | | | | ✓ | | | ~ |
| Time-bounded capability (`capability-expired`) | | | | | | | | | ✓ | | ~ |
| Rate-limited capability (`rate-limit-exceeded`) | | | | | | | | | | ✓ | ~ |
| Prior-use revocation | ~ | | | | | | | ✓ | | | |
| Server-side chokepoint approval registration | | | | | | | ~ | ✓ | | | ✓ |
| Unified drive+monitor+approve surface | | | | | | | | | | | ✓ |
| LLM-isolation invariant (`--no-policy-preview`) | | | | | | | | ✓ | ✓ | ✓ | |
| Audit reconstructs the decision | ✓ | ✓ | | ✓ | | | | ✓ | ✓ | ✓ | ✓ |

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

The original "what's still missing" punch list is now closed; demos
12–16 cover the v0.3/v0.4 features that previously lacked
walkthroughs.

Open items beyond shipped code:

- **Asciicasts / demo videos** — recording requires a terminal
  session.
- **Real-LLM walkthroughs of demos 4–11** — the existing real-LLM
  test (`integration/test_real_llm.py`) covers Demo 1 against
  `claude-haiku-4-5`; extending to other workflows is incremental
  cost.
