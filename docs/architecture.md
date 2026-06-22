# Architecture

CapableDeputy is organized around one invariant: every tool call crosses a
runtime-owned policy chokepoint before any side effect happens. The LLM proposes
actions; CapDep classifies the tool, target, labels, flow pattern, and
capability, then decides whether to allow, deny, require approval, or require an
override.

## Client Boundary Rule

All core functionality and all safety functionality belong in the daemon.
Every UI surface is a client of the daemon, not a parallel implementation of
CapDep.

This is a release-blocking architecture rule:

- **Daemon owns enforcement.** Policy decisions, capability checks,
  information-flow labels, provenance, approval semantics, tool dispatch,
  MCP/upstream admission, LLM mode selection, audit logging, configuration
  validation, and recovery rules must remain daemon-side.
- **Clients render and relay.** CLI, TUI, Swift macOS GUI, future Windows GUI,
  future Linux GUI, browser extensions, Shortcuts actions, and MCP host shells
  may render daemon state, collect user intent, and relay explicit operator
  actions. They must not decide policy, reinterpret labels, run tools directly,
  write trusted state directly, or create private safety shortcuts.
- **Feature parity starts at RPC.** If one surface needs a capability, add or
  improve a daemon RPC/event first, then make each client consume that shared
  contract. Do not implement user-visible workflow logic exclusively inside one
  GUI unless it is pure presentation.
- **Safety parity is mandatory.** A workflow that is safe in one client must be
  safe in every client because the daemon enforces the same allow/deny/approval
  semantics. UI-specific confirmation prompts are allowed only as extra
  friction, never as the sole safety mechanism.
- **Presentation can differ.** Clients may have different layouts, shortcuts,
  accessibility affordances, notification integrations, and platform-native
  shell behavior. Those differences must sit above the daemon contract.
- **Daemon RPC is local IPC only.** CapDep trusts same-user local clients that
  can connect to its owner-only Unix-domain socket. The daemon must not expose
  JSON-RPC, client sessions, approvals, configuration, or control APIs over TCP,
  UDP, WebSocket, HTTP, gRPC, or any other network listener. Network activity is
  allowed only for explicit tools/connectors or short-lived OAuth loopback flows,
  never as an alternate daemon client transport.

Practical review checklist for any client change:

1. Is any new policy, labeling, approval, tool execution, or trust mutation
   logic implemented in the client? If yes, move it to the daemon.
2. Is the client reading or writing daemon state by bypassing JSON-RPC? If yes,
   add a daemon RPC or event stream.
3. Would the same workflow be unavailable or unsafe in CLI/TUI because the
   Swift app owns the logic? If yes, move the workflow primitive into the
   daemon.
4. Are exact payloads, labels, policy reasons, and provenance preserved from
   daemon output to UI? If no, fix the RPC or renderer before shipping.

The intended product shape is one daemon with many thin, parity-preserving
clients: CLI for scripting, terminal UI for SSH and demos, native macOS for
desktop flow, and eventually native or cross-platform Windows/Linux shells.

## Onguard Client Boundary

Headless background automation should be implemented as **onguard clients**:
normal daemon clients that run deterministic schedules, queues, or approved
configuration without a human actively driving each step. Onguard clients are
not privileged and must not become a second authority path.

- The daemon owns policy, labels, capabilities, approvals, provenance, audit,
  connector credentials, schedules, shared client configuration, and
  coordination queues/events.
- Onguard clients own orchestration: schedule wakeups, polling, retry/backoff,
  workflow templates, digest assembly loops, and queue processing.
- Every onguard action must open or use a daemon session and call daemon
  RPC/tool paths so normal flow patterns and security models apply.
- Policy and Starlark inspectors must receive structured origin metadata for
  onguard work: client id, schedule id, queued command id, proposer, approver,
  and whether the run is scheduled or human-triggered.
- Durable user/domain `memory.*` is not the client message bus. Client
  coordination needs daemon-owned `client.config`, `client.queue`,
  `client.events`, and `schedule` contracts with labels, provenance, and audit.

See [onguard-clients.md](onguard-clients.md) for the detailed architecture.

## Current Runtime Seams

- `ToolDefinition` is the compatibility runtime object. It still carries the
  handler, but it now exposes split descriptors for runtime shape, policy
  classification, and information-flow behavior.
- `DecisionRequest` and `PolicyPipeline` are the policy decision boundary. The
  default pipeline delegates to the existing engine, but dispatcher code no
  longer needs to pass a long argument list directly into the monolithic engine.
- `RuntimeManifest` is the normalized, side-effect-free view of configured
  tools, upstream MCP servers, and policy hooks. Daemon startup reports a compact
  manifest summary and fails on manifest errors.
- `HookRegistry` is the canonical extension surface. Legacy tuple fields on
  `PolicyContext` still work, but hook execution consumes named lifecycle hooks.

## Configuration Direction

Configuration should compile in this order:

1. Load curated presets, personal-assistant overrides, `servers.d`, Starlark
   scripts, and local policy files.
2. Normalize them into descriptors and a `RuntimeManifest`.
3. Validate every tool has a capability kind, effect operation, policy target,
   risk citation, and flow metadata.
4. Start runtime services from the already-validated manifest.

This keeps user-facing YAML flexible while keeping daemon startup deterministic
and testable.

## macOS and AppleScript

AppleScript support should stay app-specific by default. Apple Mail, Keynote,
Pages, Numbers, clipboard, notifications, and app control should expose narrow
tool kinds with stable targets such as `applemail://`, `keynote://frontmost`,
`pages://frontmost`, `numbers://frontmost`, and `macos://clipboard`.

Generic AppleScript is useful as an expert escape hatch and MCP-server building
substrate, but it should not be the default assistant authority. Keep first-use
approval, local-app Starlark tightening, source bindings, and explicit TCC
permissions enabled for practical personal-assistant use.
