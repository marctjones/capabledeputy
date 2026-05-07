# CapableDeputy Demos

Three end-to-end walkthroughs of the architecture in different audiences
and modes.

| Demo | Audience | Requires | Time |
|---|---|---|---|
| [01 — Prescription-to-Wife](01-prescription-to-wife.md) | security/AI safety | nothing | ~3 min |
| [02 — Real Claude Blocked, Then Approved](02-real-claude-blocked-then-approved.md) | demo to anyone | Anthropic API key | ~5 min |
| [03 — Claude Code as Adversarial Agent](03-claude-code-adversarial.md) | architecture/positioning | Claude Code subscription | ~7 min |

The three demos build on each other:

- **Demo 1** runs entirely deterministically with a `FakeLLMClient`,
  proving the architectural property in code with no external
  dependencies. This is the demo that should run in CI.
- **Demo 2** runs the same architectural property against a real
  Anthropic model, demonstrating that the structural denial holds for
  an actual production LLM and that the model produces sensible
  policy-aware refusals.
- **Demo 3** flips the architecture: CapableDeputy as MCP server,
  external Claude Code as the agent. This is the strongest "security
  wrapper for any MCP-speaking agent" pitch.

## What's verified by each

| Property | 01 | 02 | 03 |
|---|---|---|---|
| Health-context session blocked from email egress | ✓ | ✓ | ✓ |
| Approval workflow spawns purpose-limited session | ✓ | ✓ | ✓ |
| Audit log captures every step | ✓ | ✓ | ✓ |
| Real LLM correctly identifies fired rule | — | ✓ | partial |
| External MCP host can drive the policy | — | — | ✓ |
| Schema-validated dual-LLM extraction | implicit | — | — |

(Demo 1 also runs `tests/test_quarantined_extractor.py` for the dual-LLM
property.)
