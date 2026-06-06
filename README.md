# CapableDeputy

A structurally secure runtime for personal AI agents.

> *A capable deputy, never a confused one.*

CapableDeputy is an AI agent runtime built as a faithful implementation of recognized security models — a reference monitor, an information-flow lattice, and the object-capability model — with the LLM treated as an untrusted component *outside* the trusted computing base. Every action the agent takes flows through one deterministic capability and information-flow chokepoint, escalates to programmatic execution when stakes warrant, and surfaces every cross-compartment data flow through human-auditable approval gates.

**Status:** Alpha — **v0.15.0 released**. The spec-003 label-model redesign is **complete**: the legacy flat `Label` enum is deleted and the clean four-axis `LabelState` model (data category×tier · provenance · effect/operation · context) is the sole label model — no backwards compatibility (`state.db` is wiped on cutover). BLP/Biba/confused-deputy enforcement moved to always-on four-axis engine invariants, proven equivalent to the old rules before removal. See [docs/responsible-ai-frameworks.md](docs/responsible-ai-frameworks.md) and [specs/003-labeling-framework/label-model-redesign.md](specs/003-labeling-framework/label-model-redesign.md). See [CHANGELOG.md](CHANGELOG.md) for what shipped and [ROADMAP.md](ROADMAP.md) for the plan.

## Why

Most defenses for LLM agents are perimeter classifiers — they try to *detect* a bad instruction ("does this look like an attack?"). A classifier can always be fooled. CapableDeputy takes the opposite approach: enforce recognized information-security models at the architecture level so the bad *outcome* has no reachable path — independent of *why* the model misbehaves, whether a crafted injection, a hallucination, a buggy or malicious tool / MCP server, or the user's own mistake. The classifier can fail; the capability check cannot.

Because the guarantees come from the models rather than from per-attack rules, a range of risks is structurally mitigated as a *consequence* of the design, not as individually-coded features. Illustratively (non-exhaustive):

- **Silent data exfiltration** — information-flow taint blocks egress of data a session has read (Denning lattice).
- **Confused-deputy / ambient-authority abuse** — no authority without an explicit, scoped capability (object-capability).
- **Over-broad or escalating delegated authority** — monotonic attenuation across delegation chains.
- **Unauthorized irreversible or committing actions** — human-in-the-loop approval (Clark-Wilson separation of duty).
- **Conflict-of-interest data mixing** — cross-compartment access rules (Brewer-Nash).
- **Unaccountable automated decisions** — deterministic decisions over an append-only provenance record.
- **Purpose-contamination** — sensitive data influencing a decision it has no bearing on (*designed; partially delivered in v0.13.0 via the labeling framework / Purpose Handle, spec 003 — completion in progress*).
- **Prompt-injection-driven misuse** — mitigated as one special case of the above: the model is treated as untrusted regardless of how it was subverted.

This is an illustrative consequence of the enforced models, not a feature checklist or a completeness claim.

The design draws on classical information security models — Bell-LaPadula, Biba, Brewer-Nash, Clark-Wilson, and the object-capability model — synthesized with the dual-LLM and programmatic-execution patterns from CaMeL (Google DeepMind, 2025) and Dromedary (Microsoft). These are tracked frameworks, not loose inspiration: every enforcement mechanism traces to a named model with any deliberate deviation recorded ([docs/security-models.md](docs/security-models.md)), and every way the planner LLM touches labeled data is one of a small set of named flow patterns ([docs/llm-flow-patterns.md](docs/llm-flow-patterns.md)). Those docs are the design yardstick; this README intentionally does not restate the theory.

## Scope: a control at the intersection, not a governance program

CapableDeputy is deliberately narrow. It is a **runtime control at the intersection of InfoSec, Data & Privacy, and AI governance** — it defends the conjunction those three programs structurally cannot — sensitive data, untrusted input, and capable action converging in one agent (the "lethal trifecta," in governance terms) — and intentionally does *not* attempt their breadth. Within AI governance it is deep and faithful on agentic-effect containment, human oversight, and decision accountability, and silent by design on model accuracy, bias/fairness, eval, and content safety. Every in-scope guarantee is bounded by three contingencies: correct labeling, a trustworthy substrate, and purpose-scoping (the labeling framework / spec 003, partially delivered in v0.13.0 and still being completed). See **[docs/governance-scope.md](docs/governance-scope.md)** for the precise in/out-of-scope statement and its alignment with this vision.

Why all three at once: a running agent collapses what were three mostly *design-time* governance disciplines into a single *runtime* problem — the right decision depends on the live context (purpose, recipient, sensitivity, reversibility) of each action. CapableDeputy resolves that context per action at one deterministic, LLM-isolated chokepoint: the *protection strength* adapts to context, the *mechanism deciding it* does not. The decision-layer rationale and its grounding in adaptive-governance / Contextual Integrity theory is in **[docs/trust-model.md](docs/trust-model.md)** (§9).

## Documentation

- [docs/governance-scope.md](docs/governance-scope.md) — **what CapableDeputy is expected to do (and not)** — scope, AI-gov coverage, contingencies
- [docs/security-models.md](docs/security-models.md) — theoretical model lineage, faithful-vs-approximate intent, deliberate deviations (the yardstick)
- [docs/llm-flow-patterns.md](docs/llm-flow-patterns.md) — the named planner↔labeled-data flow patterns and their selector
- [docs/trust-model.md](docs/trust-model.md) — decision layer: who authorizes, and the adaptive-context / Contextual Integrity grounding
- [docs/responsible-ai-frameworks.md](docs/responsible-ai-frameworks.md) — **the actionable core of responsible AI: keeping the human in control of the agent's actions, not policing model correctness** — the eight enforced principles, the human in/on/over-the-loop ladder, and what is deliberately out of scope

- [docs/SURFACES.md](docs/SURFACES.md) — **which command do I use?** (chat vs console vs tui vs demo vs …) — start here
- [DESIGN.md](DESIGN.md) — full design specification
- [ROADMAP.md](ROADMAP.md) — phased implementation plan
- [docs/demos/README.md](docs/demos/README.md) — 21 end-to-end demos
- [demos/scenarios/README.md](demos/scenarios/README.md) — **9 narrated executable demos** (runnable via pytest)
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and contribution guide

## Development

```bash
uv sync --all-groups
uv run pytest
```

### See it in action

The fastest way to understand what the policy engine actually does:

```bash
# Run all 9 narrated demos in order, with operator-facing prose:
uv run pytest demos/scenarios/run_all.py --no-cov -s

# Or a single demo (the marquee one):
uv run pytest demos/scenarios/daily_briefing.py --no-cov -s
```

See [demos/scenarios/README.md](demos/scenarios/README.md) for the
demo lineup and what each one proves.

## License

Proprietary — Copyright (c) 2026 Marc Jones. All rights reserved. No license is granted; see [LICENSE](LICENSE).
