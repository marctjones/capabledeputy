# CapableDeputy

> A capable deputy, never a confused one.

CapableDeputy is a **structurally secure runtime for personal AI
agents.** It propagates capabilities and information-flow labels
through every action an agent takes, escalates to programmatic
execution when stakes warrant, and forces every cross-compartment
data flow through deterministic, human-auditable approval gates.

It answers the question — *where are the practical prompt-injection-
resistant agents?* — by building one: multi-provider, proprietary,
terminal-operated, and designed for individuals who want capable AI
assistance without surrendering health records, financial data, or
third-party communications to the LLM's word-completion machinery.

## What it gives you

The architectural guarantee: **no LLM session can hold the lethal
trifecta** — sensitive data, untrusted content, and outbound
communication — at the same time without an explicit, human-approved
declassification gate.

Even if every classifier fails and the LLM is fully compromised by an
injection, the policy violation cannot occur because the harness — not
the LLM — controls capability dispatch.

## Where to go next

- **[Governance scope](governance-scope.md)** — what CapableDeputy is
  expected to do and not: the intersection-control thesis, AI-gov
  coverage, deliberate non-goals, and the three bounding contingencies.
- **[Design](../DESIGN.md)** — threat model, theoretical foundations,
  the three execution modes, the session graph, labels and
  capabilities, and component specs.
- **[Architecture](architecture.md)** — current implementation seams for
  policy pipelines, tool descriptors, runtime manifests, and named hooks.
- **[Roadmap](../ROADMAP.md)** — release-era implementation history and
  longer-term plan.
- **[Workflow index](workflow-index.md)** — categorized map of the
  executable workflow demos, scenario catalogue, and enforcement suites.
- **[Demos](demos/README.md)** — 21 walkthrough demos; the executable
  narrated demo suite lives in `demos/scenarios/`.
- **[Container deployment](deployment/container.md)** — Containerfile,
  Podman quadlet, and the rootless deployment story.
- **[Local-model planner](local-model-planner.md)** — local-first model
  planning; Apple Silicon uses the MLX backend by default.
- **[First-run onboarding research](first-run-onboarding-research.md)** —
  peer-agent setup patterns and the v0.34 daemon-owned onboarding contract.
- **[TLA+ spec](../spec/CapableDeputy.tla)** — formal model of the session
  graph operations and policy decision function, with safety properties
  checked by TLC.

## Quick start

```bash
# Install
uv sync

# Set an API key (or skip for local model)
export ANTHROPIC_API_KEY=sk-ant-...

# Start the daemon and create a session
capdep daemon start &
capdep session new --intent "test session"

# Send a message
capdep send <session-id> "What's in my labs memory?"
```

See the [README](../README.md)
for the full quick start.
