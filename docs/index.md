# CapableDeputy

> A capable deputy, never a confused one.

CapableDeputy is a **structurally secure runtime for personal AI
agents.** It propagates capabilities and information-flow labels
through every action an agent takes, escalates to programmatic
execution when stakes warrant, and forces every cross-compartment
data flow through deterministic, human-auditable approval gates.

It answers the question — *ten months after CaMeL, where are the
production-grade prompt-injection-resistant agents?* — by building
one: multi-provider, proprietary, terminal-operated, designed for
individuals who want capable AI assistance without surrendering
health records, financial data, or third-party communications to the
LLM's word-completion machinery.

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
- **[Design](design.md)** — threat model, theoretical foundations,
  the three execution modes, the session graph, labels and
  capabilities, and component specs.
- **[Roadmap](roadmap.md)** — what's shipped (v0.1 → v0.4) and
  what's next.
- **[Demos](demos/README.md)** — three end-to-end scenarios:
  prescription-to-wife declassification, the real-Claude
  blocked-then-approved flow, and Claude Code as an adversarial host.
- **[Container deployment](deployment/container.md)** — Containerfile,
  Podman quadlet, and the rootless deployment story.
- **[Local-model planner](local-model-planner.md)** — keep PHI on the
  box: Ollama for the planner, Ollama or frontier for the quarantined
  extractor.
- **[TLA+ spec](spec.md)** — formal model of the session graph
  operations and policy decision function, with the safety properties
  TLC checks.

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

See the [README](https://github.com/example/capabledeputy/blob/main/README.md)
for the full quick start.
