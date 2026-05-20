---
description: "Spec 005 — 3-week terminal-assistant MVP: ship CapableDeputy as a credible terminal-only personal assistant without forking a microclaw and without weakening the v0.9 security model"
---

# Spec 005: Terminal Assistant MVP

## Goal

Make CapableDeputy a **credible terminal-only personal AI assistant** for
Ubuntu (today) and Windows-via-WSL2 (today) / native-Windows (deferred),
in roughly three weeks of focused work, without:

- Forking a microclaw and inheriting its security debt.
- Adding messaging channels (out of scope per operator decision).
- Weakening any v0.9 security invariant.

The deliverable: a tagged `v1.0.0-terminal-mvp` release that an operator
can install, configure with their own LLM endpoint (cloud or Ollama
local), and use from `capdep chat` / TUI as their daily coding +
admin + research assistant.

## Non-goals

- **Messaging channel adapters.** WhatsApp/Telegram/Slack-bot integration
  is deferred (operator decision — Q1 2027 candidate).
- **Mobile/desktop apps.** Terminal-only.
- **Self-improving / reflective skill creation à la Hermes Agent.**
  Conflicts with FR-031 asymmetry. We allow operator-ratified "rituals"
  (Phase 3) instead.
- **Marketplace / skill discovery UI.** Operator authors mappings; CD
  is not a skill store.
- **Native-Windows IPC port.** WSL2 covers Windows users on day one;
  TCP-loopback transport is a follow-on.
- **Container substrate providers.** `code.execute` stays demo-actuator-
  only at MVP. Real Podman/Modal/Firecracker is spec-004 (post-MVP).

## Scope

### A. TUI polish — chat surface that makes the security story visible

The chat/TUI is functional today but doesn't surface the security
machinery to the operator. Without the visibility, the policy gates
just feel like inscrutable refusals. We need:

- A **tool-call inspector** pane: per dispatch, show the action, the
  v2 axis snapshot, the decision, the rule, the reason.
- A **refusal explainer** widget: when a call denies, surface "why" +
  "how to override" inline, with a one-keystroke launch into the
  `capdep override request` flow.
- A **command palette** (`:` or Ctrl-P): quick actions like switch
  session, request override, view recent audit events, change profile.
- A **status bar**: current profile (clearance_max_tier), risk-
  preference dial, session purpose_handle, pending override grants.
- A **memory/context view**: AxisA/B/D snapshot, reference_handles
  list, last-N audit events.

### B. Native tools — fs.\* + web.search

The two gaps that kill any real terminal workflow:

- **`fs.*`** — read, write, create, modify, delete with binding-
  canonicalized paths. Same shape as memory.\* but operates on the
  real filesystem with `file://` binding resolution.
- **`web.search`** — Brave Search / DuckDuckGo / SearXNG provider
  chosen by operator config. Returned URLs flagged
  `external-untrusted` provenance per FR-004.

### C. Generic MCP adapter (foundation) + 2 reference servers

Pulled forward from spec-004 because the practical value is enormous
and the engineering scope is bounded:

- The generic MCP adapter (stdio + HTTP transports + mapping-file
  loader + ToolDefinition factory + fail-closed registration).
- Two concrete server integrations to prove it: Anthropic's filesystem
  MCP and brave-search MCP. These are the smallest mappings and they
  give the operator additional fs + web-search coverage beyond CD's
  native tools.

Remaining tier-1 MCP servers (GitHub, Google Workspace, Microsoft 365,
Notion, Slack, Playwright, Context7, Anthropic 5 other refs) are
deferred to post-MVP spec-004 phase 2.

### D. Rituals — operator-ratified saved tool sequences

The closest CD-shaped thing to Hermes Agent's reflective skills,
without violating FR-031:

- An operator can save a sequence of approved tool calls as a
  **Ritual** with operator-ratified T012 declarations.
- Rituals fire via `capdep ritual run <name>` or via a TUI launcher.
- A Ritual is authored once, ratified once, executed many times.
  The asymmetry invariant holds: the AI cannot author or modify
  rituals; only the operator can.

This captures most of the user-visible value of "the agent learned
to do this" without giving the AI authority to mutate policy.

### E. Onboarding — first-run wizard

A one-time `capdep init` wizard that:

- Detects the operating system (Ubuntu / WSL2 / macOS).
- Walks the operator through picking an LLM (cloud or local Ollama).
- Sets up the configs/ directory with sensible defaults from rc.6.
- Shows a guided first-call: pick a tool, see the decision flow,
  see how an override is requested.

This is the difference between "installed and confused" and
"installed and productive."

## Functional Requirements

- **FR-200** The TUI MUST display, for every tool dispatch, the
  resulting `PolicyDecision.decision`, `rule`, and `reason` in a
  dedicated inspector pane.
- **FR-201** On a DENY decision, the TUI MUST surface a "why refused"
  panel with the rule's rationale and an inline option to request
  an override (when the floor's policy is single-authorized or
  dual-control).
- **FR-202** The status bar MUST reflect the active profile's
  `max_tier` (clearance), the risk_preference dial value, and the
  session's `purpose_handle`.
- **FR-203** `fs.*` tools MUST canonicalize every target path via
  the `BindingSet` resolver (FR-043). Unbound paths refuse.
- **FR-204** `web.search` MUST refuse to invoke any search provider
  not declared in the operator's binding registry (FR-023).
- **FR-205** Search results MUST be propagated to the session's
  AxisB with `external-untrusted` provenance.
- **FR-206** The MCP adapter MUST register tools with
  `tool_provenance="curated-mcp"` so FR-031 treats their
  declarations as deterministic relax origins.
- **FR-207** Adapter registration MUST refuse any tool whose mapping
  is missing required T012 fields (Principle VI).
- **FR-208** A Ritual MUST carry operator-ratified T012 declarations
  on every step. A Ritual whose declarations are missing, malformed,
  or unratified refuses to register (FR-014).
- **FR-209** A Ritual's execution MUST flow through the same
  `engine.decide()` chokepoint as ad-hoc tool calls. Saving a ritual
  does NOT grant the agent authority; the operator's standing
  capabilities are still required at execution time.
- **FR-210** The first-run wizard MUST refuse to complete without
  the operator declaring (or accepting defaults for): the LLM
  endpoint, the active profile, the risk-preference dial value.
- **FR-211** The onboarding wizard MUST NOT enable any tool whose
  declared `effect_class` is `social.*` without an explicit
  operator opt-in (FR-019 social-commitment defaults stay denied).

## Success Criteria

- **SC-200** A fresh operator running through `capdep init` on a
  clean Ubuntu install reaches an interactive `capdep chat` session
  with at least 5 tools registered (memory.\*, fs.read, fs.write,
  web.search, the Anthropic filesystem MCP) in under 10 minutes.
- **SC-201** A DENY in `capdep chat` surfaces both the rule and the
  reason within a single keypress; the operator can launch the
  override flow without leaving the TUI.
- **SC-202** A search query through `web.search` returns labeled
  results, and a subsequent tool call that uses one of those URLs
  composes the `external-untrusted` provenance into AxisB.
- **SC-203** A 100-step ritual saved by the operator and re-run
  produces byte-identical PolicyDecisions (SC-002 holds across
  ritual playback).
- **SC-204** The MCP adapter loads Anthropic's filesystem MCP
  server, registers its tools with the operator's mapping, and a
  representative call (`list_directory` of a non-sensitive path)
  succeeds end-to-end with the full audit event sequence.
- **SC-205** The MCP adapter, given a tool whose mapping is missing
  any required T012 field, refuses to register that tool and emits
  a `MCP_TOOL_REFUSED` audit event.
- **SC-206** `capdep chat` runs on Ubuntu 24.04 and on Windows 11
  WSL2 (Ubuntu kernel). The TUI renders cleanly in Windows Terminal.

## Out of scope (deferred)

- Native-Windows TCP-loopback IPC transport (post-MVP).
- Container substrate providers (Podman / Modal / Firecracker — spec-004
  phase 4).
- OTLP / Splunk audit sinks (spec-004 phase 5).
- WebAuthn / Duo / OAuth2.1 (spec-004 phase 6).
- Tier-1 MCP servers beyond Anthropic filesystem + brave-search
  (spec-004 phase 2).
- DefenseClaw plugin (spec-004 phase 8).
- Messaging channels.

These are all on the spec-004 task graph; MVP pulls forward only the
adapter foundation + 2 reference servers.

## Open questions

1. **Should `web.search` ship with a starter binding** for the
   chosen provider (Brave / DDG / SearXNG), or require operator
   authoring? Lean: ship starter binding for Brave + a comment
   block showing how to switch.
2. **Should rituals be per-session or per-operator?** Lean:
   per-operator (stored on the daemon), referenced by id from any
   session.
3. **Should the first-run wizard auto-detect Ollama** if it's
   already running? Lean: yes, with explicit confirmation.
