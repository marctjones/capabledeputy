---
description: "Spec 004 implementation plan — high-level approach + sequencing"
---

# Plan: Spec 004 MCP + Substrate Integration

## Technical context

- Language: Python 3.12 (matches 003)
- Container substrates: Podman (default), Modal (hosted), Firecracker (kernel-
  level). Firecracker is most NemoClaw-parity; Podman is most-portable.
- MCP protocol: stdio + HTTP transports.
- Identity: WebAuthn via `python-webauthn`, OAuth 2.1 via `authlib`, Duo via
  Duo Auth API.
- Observability: OpenTelemetry SDK for OTLP; Splunk HEC for the Splunk
  pathway.

## Sequencing

The implementation sequence is staged so each phase produces a demoable
artifact rather than dead-end internal plumbing.

### Phase 1 — Generic MCP adapter (foundation)

The keystone for everything else. No tier-1 server integration can land
without this.

Deliverables:
- `src/capabledeputy/mcp_adapter/` module with stdio + HTTP transports.
- Mapping-file schema in YAML: per-MCP-tool T012 declarations.
- ToolDefinition factory: consume MCP `tools/list` + mapping file → produce
  CD-native ToolDefinitions.
- Fail-closed registration: missing required mapping fields ⇒ refuse.
- Audit events for MCP-related operations (`mcp.server_loaded`,
  `mcp.tool_registered`, `mcp.tool_refused`, `mcp.request_sent`,
  `mcp.response_received`).

### Phase 2 — Tier-1 MCP server mappings

8 servers + Anthropic's 7 references. Each ships:
- A mapping file under `mappings/<server-id>.yaml`.
- A fixture: a recorded `tools/list` response + 3-5 representative tool
  call/response pairs.
- An integration test that drives the generic adapter against the fixture
  AND asserts CD's policy promises hold (decision outcome + audit event
  sequence).

Order by integration leverage:
1. **Anthropic 7 references** — vendor-trusted; simplest mappings.
2. **GitHub MCP** — high-leverage for dev workflows.
3. **Google Workspace MCP** — highest personal-assistant impact.
4. **Microsoft 365 MCP** — parity for Outlook users.
5. **Notion MCP** — knowledge-worker workflows.
6. **Slack MCP** — communication channel.
7. **Playwright MCP** — web automation (touches FR-019 hard rule for
   public-post effect class).
8. **Context7 MCP** — live-docs lookup (similar shape to Anthropic refs).

### Phase 3 — Native tools (fs, web.search, code.execute)

The non-MCP integrations operators need:
- `fs.*` — fs.read / fs.write / fs.create / fs.modify / fs.delete with
  binding-canonicalized paths and write-discipline verification against
  a chosen container snapshot if relevant.
- `web.search` — Brave / DuckDuckGo / SearXNG via HTTPS; FR-023 binding
  required.
- `code.execute` — delegates to a `SandboxActuator` provider (Phase 4).

### Phase 4 — Container substrate providers

Three real `SandboxActuator` implementations:
- **Podman** — rootless container, `--read-only`, `--net=none` by default,
  ephemeral volumes mapped only to declared `read_write` paths (parity with
  NemoClaw's Landlock).
- **Modal** — hosted sandbox; cost-bounded; for code-heavy workflows.
- **Firecracker** — kernel-level isolation; matches NemoClaw's primitives.

Each provider includes:
- `create_region`, `execute`, `discard_region` per the port contract.
- Attestation: signed manifest of (region_id, image_digest, command,
  env, exit_code, output_digest).
- Audit events for region lifecycle.

### Phase 5 — Observability

- OTLP exporter wired into `audit/writer.py` as a parallel sink.
- Splunk HEC exporter likewise.
- Resource attributes (service.name, deployment.environment) from operator
  config.
- Documentation showing how DefenseClaw-targeted dashboards work unchanged.

### Phase 6 — Identity / authentication

- WebAuthn integration for `capdep override attest` — attester confirms
  via a registered authenticator. Replace today's `--confirm` boolean with
  a cryptographic signature step.
- Duo integration in the authentication adapter that derives Axis-D's
  `authentication` field at session spawn.
- OAuth 2.1 device flow for remote-service tokens; tokens persisted in CD's
  secrets store.

### Phase 7 — Regression demos against the documented incident corpus

Two write-ups with audit logs:
- **"Re-run the Meta director scenario"** — Google Workspace MCP integration
  + FR-019 reversibility-irreversible + optimistic-auto carve-out path.
- **"Survive a ToxicSkills payload"** — 5 recorded skill payloads + capability
  narrowing + axis-B taint + FR-031 asymmetry. Audit trail evidence.

These become the public demo / positioning artifacts.

## Dependencies between phases

- Phase 1 blocks Phase 2 (no adapter, no integrations).
- Phase 4 blocks the `code.execute` part of Phase 3.
- Phase 1 + 4 together block Phase 7 (regression demos need both real MCP
  servers AND real sandbox to make the threat-model story credible).
- Phase 5, 6 are independent and can land in parallel.

## Risk register

- **R1: Upstream MCP server breaking changes.** Mitigation: pin server
  versions in fixture; the integration test is recorded, the generic
  adapter is what's tested against live servers.
- **R2: WebAuthn UX friction.** Mitigation: keep the `--confirm` boolean
  path for non-dual-control flows; WebAuthn is required only when the
  policy says dual-control.
- **R3: Container substrate divergence.** Three providers means three
  attestation formats. Mitigation: provider-agnostic verification interface
  in policy/reversibility.py; providers translate.
- **R4: Operator never authors mappings.** Mitigation: ship a starter
  mapping pack under `mappings/community/` for the most-used servers.
- **R5: The "various claws" market consolidates around DefenseClaw.**
  Mitigation: ship the DefenseClaw plugin (defenseclaw-integration.md)
  so CD becomes a deterministic policy engine swap-in for the regex+LLM
  path inside DefenseClaw.
