# CapableDeputy

A structurally secure runtime for personal AI agents.

> *A capable deputy, never a confused one.*

CapableDeputy is an AI agent runtime that prevents prompt-injection-driven misuse by construction: every action the agent takes flows through deterministic capability and information-flow checks, escalates to programmatic execution when stakes warrant, and surfaces every cross-compartment data flow through human-auditable approval gates.

**Status:** Pre-alpha. See [ROADMAP.md](ROADMAP.md) for the implementation plan.

## Why

Modern LLM agents face a structural threat: prompt injection. When an agent has simultaneous access to (a) sensitive data, (b) untrusted content, and (c) outbound communication — Simon Willison's "lethal trifecta" — any LLM can be tricked into misusing its own capabilities to exfiltrate or act on data the user never authorized.

Most current defenses are perimeter classifiers ("does this look like prompt injection?"). CapableDeputy takes a different approach: change the agent's architecture so that bad outcomes are unreachable, regardless of input cleverness. The classifier can fail; the capability check cannot.

The design draws on classical information security models — Bell-LaPadula, Biba, Brewer-Nash, Clark-Wilson, and the object-capability model — and synthesizes them with the dual-LLM and programmatic-execution patterns from CaMeL (Google DeepMind, 2025) and Dromedary (Microsoft).

These are tracked frameworks, not loose inspiration: every enforcement mechanism traces to a named model with any deliberate deviation recorded ([docs/security-models.md](docs/security-models.md)), and every way the planner LLM touches labeled data is one of a small set of named flow patterns ([docs/llm-flow-patterns.md](docs/llm-flow-patterns.md)). Those docs are the design yardstick; this README intentionally does not restate the theory.

## Scope: a control at the intersection, not a governance program

CapableDeputy is deliberately narrow. It is a **runtime control at the intersection of InfoSec, Data & Privacy, and AI governance** — it defends the conjunction those three programs structurally cannot (the lethal trifecta, restated in governance terms), and intentionally does *not* attempt their breadth. Within AI governance it is deep and faithful on agentic-effect containment, human oversight, and decision accountability, and silent by design on model accuracy, bias/fairness, eval, and content safety. Every in-scope guarantee is bounded by three contingencies: correct labeling, a trustworthy substrate, and the still-unbuilt v0.9 purpose-scoping. See **[docs/governance-scope.md](docs/governance-scope.md)** for the precise in/out-of-scope statement and its alignment with this vision.

Why all three at once: a running agent collapses what were three mostly *design-time* governance disciplines into a single *runtime* problem — the right decision depends on the live context (purpose, recipient, sensitivity, reversibility) of each action. CapableDeputy resolves that context per action at one deterministic, LLM-isolated chokepoint: the *protection strength* adapts to context, the *mechanism deciding it* does not. The decision-layer rationale and its grounding in adaptive-governance / Contextual Integrity theory is in **[docs/trust-model.md](docs/trust-model.md)** (§9).

## Documentation

- [docs/governance-scope.md](docs/governance-scope.md) — **what CapableDeputy is expected to do (and not)** — scope, AI-gov coverage, contingencies
- [docs/security-models.md](docs/security-models.md) — theoretical model lineage, faithful-vs-approximate intent, deliberate deviations (the yardstick)
- [docs/llm-flow-patterns.md](docs/llm-flow-patterns.md) — the named planner↔labeled-data flow patterns and their selector
- [docs/trust-model.md](docs/trust-model.md) — decision layer: who authorizes, and the adaptive-context / Contextual Integrity grounding

- [docs/SURFACES.md](docs/SURFACES.md) — **which command do I use?** (chat vs console vs tui vs demo vs …) — start here
- [DESIGN.md](DESIGN.md) — full design specification
- [ROADMAP.md](ROADMAP.md) — phased implementation plan
- [docs/demos/README.md](docs/demos/README.md) — 21 end-to-end demos
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and contribution guide

## Development

```bash
uv sync --all-groups
uv run pytest
```

## License

Proprietary — Copyright (c) 2026 Marc Jones. All rights reserved. No license is granted; see [LICENSE](LICENSE).
