---
description: "Spec 005 task list — 3-week terminal-assistant MVP, 31 tasks across 5 phases"
---

# Tasks: Spec 005 Terminal Assistant MVP

**Input**: Design documents from `/specs/005-terminal-assistant-mvp/`
**Prerequisites**: spec.md, plan.md, v0.9.0-rc.6 baseline (rc.6 closed
the integration sub-phase that this MVP builds on).

## Format: `[ID] [P?] [Week] Description`

- **[P]**: Different file, no incomplete-task dependency — parallelizable.
- Each task has an exact file path.
- ID prefix `V` for "v1.0 terminal-MVP".

---

## Week 1 — Native tools + TUI inspector foundation

### A. `fs.*` native tools (FR-203)

- [ ] **V001** Create `src/capabledeputy/tools/native/fs.py` with `fs.read`, `fs.write`, `fs.create`, `fs.modify`, `fs.delete`. Each declares full T012 fields: `effect_class` = `data.{read,write,create,modify,delete}_file`, `default_reversibility` graded per operation (read/create reversible/system; write reversible-with-friction/human; modify/delete irreversible/external; UI lets operator override). All ops canonicalize paths through the binding registry.
- [ ] **V002** Wire `fs.*` into `app.py::_register_native_tools()`.
- [ ] **V003** [P] Tests `tests/test_tools_native_fs.py` — happy-path read/write under a binding, unbound path refuses, write-discipline verification skipped pre-spec-004 with a clear test annotation.

### B. `web.search` native tool (FR-204, FR-205)

- [ ] **V004** Create `src/capabledeputy/tools/native/web_search.py` with `web.search`. Provider selection from operator config (`configs/search.yaml`: default Brave; DDG/SearXNG configurable). Operator must declare a binding for the search provider URL or the tool refuses (FR-023).
- [ ] **V005** [P] Default `configs/search.yaml` + an example Brave binding committed under `configs/source_bindings.yaml` (commented-in by `capdep init`).
- [ ] **V006** [P] Tests `tests/test_tools_native_web_search.py` — unbound provider refuses; bound provider returns results; returned URLs propagate `external-untrusted` provenance into AxisB.

### C. TUI inspector pane (FR-200, FR-202)

- [ ] **V007** Add tool-call inspector pane in `src/capabledeputy/tui/inspector.py`. Subscribes to audit event stream; renders per-dispatch fields (action, axis snapshots, decision, rule, reason, matched_capability, v2_outcome).
- [ ] **V008** Add status bar in `src/capabledeputy/tui/status_bar.py`. Shows active profile id + `max_tier`, risk_preference dial value, session `purpose_handle`, count of pending override grants.
- [ ] **V009** [P] Snapshot test for inspector + status-bar rendering against a recorded audit event stream.

---

## Week 2 — Generic MCP adapter + 2 reference servers + TUI refusal/override

### D. Generic MCP adapter (FR-206, FR-207)

Pulled forward from spec-004 U001-U012:

- [ ] **V010** Create `src/capabledeputy/mcp_adapter/__init__.py` module skeleton + transport interface.
- [ ] **V011** Implement stdio MCP transport in `src/capabledeputy/mcp_adapter/transport_stdio.py` — child-process stdio reading the MCP protocol.
- [ ] **V012** [P] Implement HTTP MCP transport in `src/capabledeputy/mcp_adapter/transport_http.py` — for servers that expose HTTP.
- [ ] **V013** Define mapping-file schema in `src/capabledeputy/mcp_adapter/mapping.py` — per-MCP-tool T012 declarations.
- [ ] **V014** [P] Mapping-file loader + fail-closed validation: missing T012 field on a mapped tool ⇒ refuse registration (Principle VI).
- [ ] **V015** ToolDefinition factory in `src/capabledeputy/mcp_adapter/factory.py` — consume MCP `tools/list` + mapping → produce CD ToolDefinitions tagged `tool_provenance="curated-mcp"`.
- [ ] **V016** Extend `audit/events.py` with `MCP_SERVER_LOADED`, `MCP_TOOL_REGISTERED`, `MCP_TOOL_REFUSED`, `MCP_REQUEST_SENT`, `MCP_RESPONSE_RECEIVED`.
- [ ] **V017** Wire MCP adapter into App lifecycle: on daemon start, scan `configs/mcp_servers/*.yaml`, instantiate the adapter per server, register the resulting tools.
- [ ] **V018** [P] Tests `tests/test_mcp_adapter_*.py` — stdio transport, HTTP transport, fail-closed refusal.

### E. Reference MCP server integrations

- [ ] **V019** [P] Anthropic filesystem MCP — mapping in `mappings/anthropic-filesystem.yaml`; recorded fixture; integration test against fixture. Operator gets vetted local-fs ops via MCP in addition to native `fs.*`.
- [ ] **V020** [P] Anthropic brave-search MCP — mapping + fixture + test. Vetted search alternative to native `web.search`.

### F. TUI refusal-explainer + override-launch (FR-201)

- [ ] **V021** Refusal-explainer widget in `src/capabledeputy/tui/refusal.py`. On any `PolicyDecision.decision != ALLOW`, renders rule + reason + (if `OVERRIDE_REQUIRED`) an inline "request override" launcher.
- [ ] **V022** Override-launch flow: collects invoker + friction confirmation in the TUI; dispatches through the existing `capdep override request` RPC; surfaces the new grant id so the operator can re-attempt.
- [ ] **V023** [P] Snapshot tests for refusal rendering of each rule category (capability, conflict, reversibility, binding, envelope, control-plane, override).

---

## Week 3 — Rituals + onboarding + polish

### G. Rituals (FR-208, FR-209)

- [ ] **V024** Data model in `src/capabledeputy/rituals/model.py`: `Ritual = (id, name, steps[], operator_ratified_by, ratified_at, risk_ids, default_purpose_handle)`. Each step carries the same T012 fields a ToolDefinition would. Persisted in a new `rituals` SQLite table (additive ALTER on the v6 schema).
- [ ] **V025** [P] CLI in `src/capabledeputy/cli/ritual_cmd.py`: `capdep ritual save / run / list / show / refuse`. Talks to the daemon via the existing IPC (parallel to `override_cmd.py`).
- [ ] **V026** [P] Daemon RPC handlers in `src/capabledeputy/daemon/ritual_handlers.py`: `ritual.save / run / list / show / refuse`.
- [ ] **V027** Ritual execution path: each step flows through the SAME `engine.decide()` chokepoint as ad-hoc dispatches. Saving a ritual does NOT grant new authority; the operator's standing capabilities still gate each step at run time.
- [ ] **V028** [P] TUI ritual launcher in `src/capabledeputy/tui/ritual_launcher.py`: Ctrl-R opens a list; Enter on a ritual launches its steps with a per-step decision render in the inspector pane.
- [ ] **V029** [P] Tests `tests/test_ritual_*.py` — save + ratify + run + replay determinism (SC-203); FR-014 refusal on unratified rituals; FR-031 refusal on AI-authored rituals (no API path exists — structural).

### H. First-run onboarding (FR-210, FR-211)

- [ ] **V030** `capdep init` wizard in `src/capabledeputy/cli/init_cmd.py`. 7-prompt CLI walkthrough: OS detection, LLM provider, Ollama autodetect, default profile, risk-preference dial, opt-in for social.\* tools (default OFF), confirm + write configs/.
- [ ] **V031** [P] Tests `tests/test_init_wizard.py` — refuses to complete without each required field; refuses to enable social.\* without explicit opt-in.

### I. Polish + tag

- [ ] **V032** Final lint + format + typecheck + pytest. Demo screencast captured: fresh Ubuntu VM → `capdep init` → interactive chat exercising memory.\* + fs.\* + web.search + a saved Ritual + an override grant. Screencast committed under `docs/demos/terminal-mvp.mp4` (or a transcript if recording infra is offline).
- [ ] **V033** README quickstart updated for the terminal-MVP path. Highlight what works today, what's deferred (messaging, container sandbox, native-Windows IPC).
- [ ] **V034** Tag `v1.0.0-terminal-mvp`. Push.

---

## Dependencies & Execution Order

- **Week 1** is independent (fs + web.search + inspector + status bar).
- **Week 2** depends on Week 1 inspector (refusal-explainer reuses it).
- **Week 3** depends on Week 1 + Week 2 (rituals leverage existing tool registry; TUI launcher reuses inspector).
- Within each week, the [P] tasks are parallelizable across engineers.

## Out of scope (post-MVP — see spec-004)

- Container substrate providers
- OTLP / Splunk audit sinks
- WebAuthn / Duo / OAuth 2.1
- Tier-1 MCP servers beyond Anthropic filesystem + brave-search
- DefenseClaw plugin
- Messaging channels
- Native-Windows IPC port

## Acceptance criteria

- All SC-200..SC-206 from spec.md verifiable in CI.
- 1142 (rc.6 baseline) → ~1180+ tests after V003/V006/V009/V018/V023/V029/V031.
- ruff + ruff format + pyright clean.
- A non-engineer operator can install + onboard + reach an interactive
  productive chat session in under 10 minutes (SC-200).

## Risk register

- **R1: TUI scope creep.** Hard cap at the named widgets in spec.md
  scope A. Anything more is post-MVP.
- **R2: Ollama on WSL2 networking.** Document the `host.docker.internal`-
  equivalent setup in the README; the wizard surfaces a clear error if
  it can't reach the configured Ollama endpoint.
- **R3: MCP adapter abstraction surprise.** Stdio first (the Anthropic
  filesystem MCP); HTTP only when needed.
- **R4: Ritual asymmetry leak.** R4 is critical for the security model;
  V027 + V029 pin "AI cannot author rituals" structurally — no API
  surface exists to create one outside the operator-driven CLI/wizard.
- **R5: Wizard becomes a product.** Hard-cap at 7 prompts. Text-only.
