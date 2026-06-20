# CapableDeputy

A structurally secure runtime for personal AI agents.

> *A capable deputy, never a confused one.*

CapableDeputy is an AI agent runtime built as a faithful implementation of recognized security models — a reference monitor, an information-flow lattice, and the object-capability model — with the LLM treated as an untrusted component *outside* the trusted computing base. Every action the agent takes flows through one deterministic capability and information-flow chokepoint, escalates to programmatic execution when stakes warrant, and surfaces every cross-compartment data flow through human-auditable approval gates.

**Status:** Alpha — **v0.23.0 released**. v0.23.0 adds the first native macOS desktop shell: a menu-bar assistant, command palette, approval queue, session dashboard, setup status, provenance view, and trust controls backed by daemon GUI RPCs. v0.22.0 made CapDep practical as a supervised macOS and Google Workspace personal assistant with official Google Workspace MCP servers, bounded Apple app automation, granular automation capability kinds, and macOS-first personal-assistant defaults. v0.21.0 aligned CapDep's flow-pattern enforcement with its security model. v0.20.0 made local Apple Silicon inference a first-class default with MLX and `Qwen/Qwen3-4B-MLX-4bit`. See [CHANGELOG.md](CHANGELOG.md) for released changes and [ROADMAP.md](ROADMAP.md) for longer-term planning.

## Why

Most defenses for LLM agents are perimeter classifiers — they try to *detect* a bad instruction ("does this look like an attack?"). A classifier can always be fooled. CapableDeputy takes the opposite approach: enforce recognized information-security models at the architecture level so the bad *outcome* has no reachable path — independent of *why* the model misbehaves, whether a crafted injection, a hallucination, a buggy or malicious tool / MCP server, or the user's own mistake. The classifier can fail; the capability check cannot.

Because the guarantees come from the models rather than from per-attack rules, a range of risks is structurally mitigated as a *consequence* of the design, not as individually-coded features. Illustratively (non-exhaustive):

- **Silent data exfiltration** — information-flow taint blocks egress of data a session has read (Denning lattice).
- **Confused-deputy / ambient-authority abuse** — no authority without an explicit, scoped capability (object-capability).
- **Over-broad or escalating delegated authority** — monotonic attenuation across delegation chains.
- **Unauthorized irreversible or committing actions** — human-in-the-loop approval (Clark-Wilson separation of duty).
- **Conflict-of-interest data mixing** — cross-compartment access rules (Brewer-Nash).
- **Unaccountable automated decisions** — deterministic decisions over an append-only provenance record.
- **Purpose limitation / purpose-contamination boundary** — inadmissible data categories are refused before entering a purpose-scoped workflow; the narrower question of whether admissible data inappropriately influenced model cognition is explicitly out of scope.
- **Prompt-injection-driven misuse** — mitigated as one special case of the above: the model is treated as untrusted regardless of how it was subverted.

This is an illustrative consequence of the enforced models, not a feature checklist or a completeness claim.

The design draws on classical information security models — Bell-LaPadula, Biba, Brewer-Nash, Clark-Wilson, and the object-capability model — synthesized with the dual-LLM and programmatic-execution patterns from CaMeL (Google DeepMind, 2025) and Dromedary (Microsoft). These are tracked frameworks, not loose inspiration: every enforcement mechanism traces to a named model with any deliberate deviation recorded ([docs/security-models.md](docs/security-models.md)), and every way the planner LLM touches labeled data is one of a small set of named flow patterns ([docs/llm-flow-patterns.md](docs/llm-flow-patterns.md)). Those docs are the design yardstick; this README intentionally does not restate the theory.

## Scope: a control at the intersection, not a governance program

CapableDeputy is deliberately narrow. It is a **runtime control at the intersection of InfoSec, Data & Privacy, and AI governance** — it defends the conjunction those three programs structurally cannot — sensitive data, untrusted input, and capable action converging in one agent (the "lethal trifecta," in governance terms) — and intentionally does *not* attempt their breadth. Within AI governance it is deep and faithful on agentic-effect containment, human oversight, and decision accountability, and silent by design on model accuracy, bias/fairness, eval, and content safety. Every in-scope guarantee is bounded by three contingencies: correct labeling, a trustworthy substrate, and the fact that purpose-scoping is enforced as observable read-admissibility rather than model-interpretability. See **[docs/governance-scope.md](docs/governance-scope.md)** for the precise in/out-of-scope statement and its alignment with this vision.

Why all three at once: a running agent collapses what were three mostly *design-time* governance disciplines into a single *runtime* problem — the right decision depends on the live context (purpose, recipient, sensitivity, reversibility) of each action. CapableDeputy resolves that context per action at one deterministic, LLM-isolated chokepoint: the *protection strength* adapts to context, the *mechanism deciding it* does not. The decision-layer rationale and its grounding in adaptive-governance / Contextual Integrity theory is in **[docs/trust-model.md](docs/trust-model.md)** (§9).

## How it works — and its honest limits

**The mechanism, in one paragraph.** Every action the agent proposes is
stopped at one deterministic chokepoint *outside* the LLM. There the engine
reads four orthogonal axes — the data's **category × sensitivity tier**, its
**provenance** (principal-direct ▸ system-internal ▸ external-untrusted), the
**effect** the action would have (read / reversible-write / irreversible /
egress …), and the **decision context** (recipient, purpose, time) — and
returns one of *allow · require-approval · deny*. Confidentiality (Bell-LaPadula),
integrity (Biba), conflict-of-interest (Brewer-Nash), and confused-deputy
defense (object-capability) are **always-on engine invariants**, not
per-attack rules: an action with no policy-allowed path is denied regardless
of *why* the model proposed it. The default is **fail-closed**. When stakes or
ambiguity rise, the runtime escalates from turn-level inheritance to a
quarantined dual-LLM split or to programmatic execution, and surfaces every
cross-compartment flow to a human approval gate.

**What this does *not* claim.** The guarantees are about **control of effects,
not correctness of content** — CapableDeputy will not tell you whether the
agent's answer is *right*, *unbiased*, or *well-reasoned*; that is out of scope
by design. Several of the classical models are deliberately implemented in
**scoped/approximate form** (read-up-only BLP, one-direction Biba,
*session-scoped* Brewer-Nash, *intransitive* noninterference) because the
faithful forms are either undecidable or incompatible with a useful agent —
these are documented as deviations, not hidden. And three limits are
**inherent**: transitive noninterference and the general safety question of an
access-control system are not decidable; model interpretability is not
pursued; and containment is **not** declassification — contained data stays
contained, it is never silently *cleansed*.

**The gaps we actively watch** (so the design doesn't make them worse, and
ideally makes them better):

- **Decision fatigue** — coarse policy pushes real workflows toward
  "always-approve," and rubber-stamped approvals are the practical way human
  oversight erodes. The fix is *more expressive policy* (the Starlark
  decision-inspector layer), so the human is asked less often and more
  meaningfully — not more often.
- **The labeling oracle** — every information-flow guarantee rides on data
  being labeled correctly; mislabeled data means the defense is *silently
  absent*. Broadening label coverage matters more for real safety than any
  new model.
- **A default-inert refinement layer** — the `DecisionInspector` chokepoint
  and sandboxed Starlark policy host are wired through daemon config, but no
  inspectors are enabled by default. Shipping conservative starter inspectors
  is now higher leverage than adding more approval prompts.
- **Model-reasoning purpose contamination** — CapDep now blocks
  inadmissible categories from entering purpose-scoped workflows, but it
  deliberately does not claim to prove how admissible data influenced the
  model's private reasoning.

The full, grounded model-by-model / pattern-by-pattern / principle-by-principle
read — strengths, weaknesses, and prioritized fixes — is in
**[docs/security-alignment-assessment.md](docs/security-alignment-assessment.md)**.

## Documentation

- [docs/governance-scope.md](docs/governance-scope.md) — **what CapableDeputy is expected to do (and not)** — scope, AI-gov coverage, contingencies
- [docs/security-models.md](docs/security-models.md) — theoretical model lineage, faithful-vs-approximate intent, deliberate deviations (the yardstick)
- [docs/llm-flow-patterns.md](docs/llm-flow-patterns.md) — the named planner↔labeled-data flow patterns and their selector
- [docs/trust-model.md](docs/trust-model.md) — decision layer: who authorizes, and the adaptive-context / Contextual Integrity grounding
- [docs/responsible-ai-frameworks.md](docs/responsible-ai-frameworks.md) — **the actionable core of responsible AI: keeping the human in control of the agent's actions, not policing model correctness** — the eight enforced principles, the human in/on/over-the-loop ladder, and what is deliberately out of scope
- [docs/security-alignment-assessment.md](docs/security-alignment-assessment.md) — **grounded alignment scorecard** — how the code actually lines up with each security model, flow pattern, and AI-safety principle, with strengths, live gaps, and prioritized fixes
- [docs/gui-greenfield-design.md](docs/gui-greenfield-design.md) — **greenfield GUI product design** — primary users, desktop posture, workflows, automation model, screen-space rules, and integrated menu system
- [docs/macos-desktop-ux-strategy.md](docs/macos-desktop-ux-strategy.md) — **macOS desktop UX strategy** — native menu-bar/command-palette/approval/dashboard design guidance for CapDep's supervised desktop assistant shell

- [docs/SURFACES.md](docs/SURFACES.md) — **which command do I use?** (chat vs console vs tui vs demo vs …) — start here
- [DESIGN.md](DESIGN.md) — full design specification
- [ROADMAP.md](ROADMAP.md) — phased implementation plan
- [docs/workflow-index.md](docs/workflow-index.md) — **categorized index of every workflow** — 26 narrated demos, the 1126-scenario allow/deny catalogue, and the enforcement suites, grouped by use case + security mechanism
- [docs/workflow-plan.md](docs/workflow-plan.md) — the **executable assurance plan**: a coverage matrix (mechanisms × pressured?), a per-workflow scorecard, and two gates — the spec we execute against
- [docs/workflow-registry.md](docs/workflow-registry.md) — the same workflows with **status + results** (implemented? tested? regression-guard vs. finding), prior gap closure, standing boundaries, and a findings log
- [docs/demos/README.md](docs/demos/README.md) — 21 historical walkthrough demos
- [demos/scenarios/README.md](demos/scenarios/README.md) — **26 narrated executable demos** (runnable via pytest)
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and contribution guide

## Development

```bash
uv sync --all-groups
uv run pytest
```

### See it in action

The fastest way to understand what the policy engine actually does:

```bash
# Run all 26 narrated executable demos in order, with operator-facing prose:
uv run pytest demos/scenarios/run_all.py --no-cov -s

# Or a single demo (the marquee one):
uv run pytest demos/scenarios/daily_briefing.py --no-cov -s
```

See [demos/scenarios/README.md](demos/scenarios/README.md) for the
demo lineup and what each one proves.

## License

Proprietary — Copyright (c) 2026 Marc Jones. All rights reserved. No license is granted; see [LICENSE](LICENSE).
