---
description: "Spec 004 — MCP + substrate integration, container runtime, observability, identity"
---

# Spec 004: MCP + Substrate Integration

## Goal

Close the production-substrate gap identified in `research.md`: ship the
integrations that make CapableDeputy a deployable policy oracle alongside
real MCP servers, real container substrates, real observability targets,
and real authentication providers — without forking the upstream MCP servers
and without weakening the v0.9 security model.

## Non-goals

- **Becoming OpenClaw**. We do not compete for the personal-assistant slot.
  We compete for the policy-oracle slot.
- **Forking upstream MCP servers**. Every integration goes through a generic
  adapter that brings CD's four-axis labels + capability narrowing + bounded-
  relax composition to the existing tool, not through a CD-specific fork.
- **Implementing every MCP server's tool API ourselves**. We adapt; we do not
  re-implement.
- **Weakening fail-closed defaults**. New integrations preserve FR-023, FR-031,
  FR-011, etc. Empty configs mean "feature inactive," never "feature open."

## Scope

### A. Generic MCP adapter (the central technical bet)

A single adapter component that wraps any MCP server (stdio or HTTP) and
exposes it as CD-native ToolDefinitions with T012-full declarations derived
from an operator-curated **mapping file**. The mapping file declares for each
MCP tool:

- `effect_class` (axis C)
- `default_reversibility` (FR-019 reversibility-weighted gating)
- `default_mutability_target_facets` (FR-039)
- `social_commitment` flag (FR-019 hard rule)
- `tool_provenance` (`curated-mcp` for vetted servers)
- `surfaces_destination_id` (FR-048 canonical id)
- `risk_ids` (FR-015 risk register citations)
- `accepts_handles` + `handle_arg_names` (Pattern (3))

This adapter is the substrate that makes spec-004 integrations a config
exercise rather than a code exercise.

### B. Tier-1 MCP server integrations

For each named server below: ship a mapping file + integration test against
the upstream MCP server (or a recorded fixture) verifying CD's policy
promises hold end-to-end. **No modifications to the upstream MCP server.**

1. **GitHub MCP** (vendor-maintained)
2. **Google Workspace MCP** (Gmail, Calendar, Drive, Docs, Sheets, Slides,
   Forms, Tasks, Contacts, Chat)
3. **Microsoft 365 MCP** (Outlook + Teams)
4. **Notion MCP**
5. **Slack MCP**
6. **Playwright MCP**
7. **Context7 MCP**
8. **Anthropic 7 reference servers**: filesystem, github, gitlab, postgres,
   sqlite, brave-search, puppeteer

### C. Native tools — local file, web search, code execution

Built-in CD tools that complement MCP servers for the workflows MCP isn't a
fit for:

- **`fs.*`** — local filesystem read/write/create/modify/delete with binding-
  canonicalized paths and write-discipline verification.
- **`web.search`** — read-only web search through Brave Search / DuckDuckGo
  / SearXNG; FR-023 fail-closed on unbound search providers.
- **`code.execute`** — code execution in a container substrate (see D).

### D. Container substrate providers (the spec-004 SandboxActuator)

Implementations of the `SandboxActuator` port that satisfy
EXECUTE.sandbox effects without the demo stub:

1. **Podman** provider — rootless containers, `--read-only`, no-net by default.
2. **Modal** provider — hosted code sandbox; cost-bounded.
3. **Firecracker** provider — kernel-level isolation (matches NemoClaw's
   sandbox primitives).

Each provider ships with a real `SandboxActuator` impl + the policy gates
that need it (write-discipline verification, isolation-posture composition).

### E. Observability sinks

- **OTLP** exporter — match DefenseClaw's pipeline target.
- **Splunk** HEC exporter — match DefenseClaw's pipeline target.
- **JSONL + SQLite** persistence (already exists in audit/writer.py — just
  document the alignment with the DefenseClaw targets).

### F. Identity / authentication

- **WebAuthn / Passkey** support for the dual-control override attester
  flow — the attester confirms via a hardware-bound credential, not just a
  string match.
- **Duo** integration for the Axis-D `authentication` field — when a session
  carries `authentication=duo-mfa`, decision rules can trust the initiator
  more.
- **OAuth 2.1 / device authorization grant** for remote-service sign-in to
  MCP servers (GitHub PAT, Google OAuth, Slack OAuth, etc.) — credentials
  live in CD's secrets store, not in tool args.

## Functional Requirements

- **FR-100** MCP adapter MUST consume an operator-curated mapping file and
  produce a `ToolDefinition` for every tool the upstream server exposes.
- **FR-101** MCP adapter MUST refuse to register a tool whose mapping is
  missing required T012 fields (fail-closed; Principle VI).
- **FR-102** MCP adapter MUST tag the produced ToolDefinitions with
  `tool_provenance="curated-mcp"` so the asymmetry invariant (FR-031) can
  treat their declarations as deterministic relax origins.
- **FR-103** Generic adapter MUST work with both stdio and HTTP MCP servers
  without server-side modification.
- **FR-104** Each tier-1 MCP server MUST have an integration test that
  proves a representative tool dispatched through CD obtains the operator-
  declared decision outcome (auto / require_approval / deny per the mapping).
- **FR-105** Container-substrate providers MUST implement the
  `SandboxActuator` port without weakening the port contract (region
  isolation, attestation, discard-on-failure).
- **FR-106** EXECUTE.sandbox dispatches MUST fail-closed when no production
  `SandboxActuator` is wired (preserves SC-017 contract).
- **FR-107** Observability sinks MUST emit every audit event without
  filtering (the policy oracle is the authoritative source; downstream
  pipelines select what to display).
- **FR-108** WebAuthn attestation MUST be the dual-control override
  attester's only acceptable confirmation method when `policy=dual-control`
  is configured for a hard floor.
- **FR-109** OAuth refresh tokens MUST be stored in CD's secrets store with
  the same fail-closed semantics as v0.9 configs (missing or unparseable
  ⇒ refuse to register the tool, not "use a stub").
- **FR-110** Generic adapter MUST be the only path through which an MCP
  tool reaches engine.decide() — no bypass through tool registration
  outside the adapter.

## Success Criteria

- **SC-100** All 8 tier-1 MCP servers + Anthropic's 7 reference servers
  have working mapping files and passing integration tests against either
  a live server or a recorded fixture.
- **SC-101** The Meta-director regression demo (FR-019 reversibility
  prevents autonomous unattended deletes) passes against a live Google
  Workspace MCP server.
- **SC-102** The ToxicSkills regression demo (FR-031 + capability narrowing
  refuse known malicious skill behaviors) passes against 5+ recorded
  payloads from the Snyk ToxicSkills corpus.
- **SC-103** EXECUTE.sandbox effects via the Podman provider compose to
  effective `reversible/system` reversibility per FR-040, and `discard`
  is honored at the end of every execution.
- **SC-104** OTLP exporter emits every CD audit event with the operator's
  declared resource attributes; DefenseClaw-style Splunk dashboards work
  unchanged.
- **SC-105** A dual-control override grant cannot be attested without a
  WebAuthn signature; CLI dry-runs MUST display the same UX as live runs.
- **SC-106** Generic adapter accepts a malformed/unsigned mapping file
  with a clear refusal; no tool registration occurs (Principle VI).

## Out of scope

- A CD-specific MCP server protocol. We consume MCP; we do not redefine it.
- A policy-authoring TUI / IDE. Operators write YAML.
- Replacing OpenClaw as the agent harness. CD is the policy oracle; the
  agent harness is whatever the operator already deploys (OpenClaw, custom,
  or CD's own minimal agent loop).

## Open questions

1. **Should the generic MCP adapter ship a starter mapping pack** for the
   most-installed servers (Google Workspace, GitHub, Notion, Slack), or
   require operators to author every mapping? Lean: ship starter mappings
   under a `mappings/community/` namespace; operators copy and customize.
2. **How does CD's risk-register interact with DefenseClaw's CodeGuard?**
   Both are pre-deployment scanners. Section in plan.md discusses possible
   complementary use.
3. **Should we ship a DefenseClaw plugin** that lets DefenseClaw call CD
   as the deterministic policy engine instead of its own regex/LLM-judge?
   Discussed in `defenseclaw-integration.md`.
