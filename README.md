# CapableDeputy

A structurally secure runtime for personal AI agents.

> *A capable deputy, never a confused one.*

CapableDeputy is an AI agent runtime that prevents prompt-injection-driven misuse by construction: every action the agent takes flows through deterministic capability and information-flow checks, escalates to programmatic execution when stakes warrant, and surfaces every cross-compartment data flow through human-auditable approval gates.

**Status:** Pre-alpha. See [ROADMAP.md](ROADMAP.md) for the implementation plan.

## Why

Modern LLM agents face a structural threat: prompt injection. When an agent has simultaneous access to (a) sensitive data, (b) untrusted content, and (c) outbound communication — Simon Willison's "lethal trifecta" — any LLM can be tricked into misusing its own capabilities to exfiltrate or act on data the user never authorized.

Most current defenses are perimeter classifiers ("does this look like prompt injection?"). CapableDeputy takes a different approach: change the agent's architecture so that bad outcomes are unreachable, regardless of input cleverness. The classifier can fail; the capability check cannot.

The design draws on classical information security models — Bell-LaPadula, Biba, Brewer-Nash, Clark-Wilson, and the object-capability model — and synthesizes them with the dual-LLM and programmatic-execution patterns from CaMeL (Google DeepMind, 2025) and Dromedary (Microsoft).

## Documentation

- [DESIGN.md](DESIGN.md) — full design specification
- [ROADMAP.md](ROADMAP.md) — phased implementation plan
- [CONTRIBUTING.md](CONTRIBUTING.md) — development setup and contribution guide

## Development

```bash
uv sync --all-groups
uv run pytest
```

## License

Apache-2.0. See [LICENSE](LICENSE).
