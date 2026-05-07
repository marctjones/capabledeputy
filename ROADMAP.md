# CapableDeputy — Implementation Roadmap

This roadmap accompanies DESIGN.md. Phases marked **DONE** are landed
on `main` with the listed commit; **IN PROGRESS** is partial; **PLANNED**
is upcoming. Phases assume the testing strategy described in §12 of
DESIGN.md and the trace/observability model described in §9.

## v0.1 — Core Runtime

### Phase 0 — Foundations  ·  **DONE** (`fa141f3`)
- Repository scaffold, Apache 2.0 license, code-of-conduct, contributing guide.
- CI: lint (ruff), type-check (pyright), test (pytest), coverage tracking.
- Daemon skeleton: Unix socket listener, JSON-RPC plumbing.
- CLI skeleton: `capdep daemon start/stop/status` and `capdep version`.

**Done-when criteria met**: `capdep daemon start` listens on the socket;
`capdep version` round-trips; CI green.

### Phase 1 — Session Graph & Audit  ·  **DONE** (`da09542` 1a, `d3e3f52` 1b, `4d50190` 1c, `aa1ca2a` 1d, `b8bc510` polish)
- `Session`, `SessionGraph` data model.
- Fork / pause / resume operations (no merge yet).
- SQLite persistence + load on startup.
- Audit log writer (JSONL, append-only, fsync per write).
- Full event taxonomy from DESIGN.md §9.2 wired in from day one.
- `capdep session list/new/fork/pause/resume`.
- `capdep audit` and `capdep watch` minimal viable forms.
- Property-based tests for graph invariants (Hypothesis).
- Env-overridable paths (`CAPDEP_SOCKET`, `CAPDEP_STATE_DB`, `CAPDEP_AUDIT_LOG`, `CAPDEP_DATA_DIR`) so container deployment is configuration, not refactor.

**Done-when criteria met**: sessions persist across daemon restarts;
fork/pause/resume work end-to-end; every operation produces audit
events conforming to the trace schema.

### Phase 2 — Labels, Capabilities, Policy  ·  **DONE** (`03db5e3` 2a, `9ad94af` 2b, `67009d1` 2c, `c4a443b` 2d)
- 8-label MVP set; `Capability`, `Action` data types; `Decision` enum.
- 4 Brewer-Nash conflict rules (rule 5 deferred to per-arg dispatch layer).
- `decide(label_set, capabilities, action) → PolicyDecision` as a pure
  function; exhaustive parametrized test matrix.
- Session migration to use real `Label` and `Capability` types.
- `capdep policy show/validate/test` CLI with colored decisions.

**Done-when criteria met**: a CLI command can simulate a decision
deterministically; the policy is exhaustively unit-tested across rule
firings and non-firings.

### Phase 3 — Tool Registry, Dispatcher, Native Tools  ·  **DONE** (`374a46a` 3a, `ba14bcb` 3b, `b87b81c` 3c, `dd1dd85` 3d)
- `ToolDefinition` / `ToolRegistry` / `ToolHandler` with `ToolContext`
  and `ToolResult` carrying labels.
- `LabeledToolClient` — single chokepoint for tool dispatch; checks
  policy, dispatches handler, propagates labels into session, emits
  the full §9.2 event sequence.
- Native tools: `memory.read` / `memory.write` (labeled in-process
  store), `purchase.queue` (Clark-Wilson stub), and (added in 5b)
  `email.send` (stub outbox).
- `tool.list/show/test/call` daemon RPCs; `capdep tool` CLI.
- App composition wires native tools into the registry on startup.

**Note**: real upstream MCP server integration (Filesystem, Fetch,
Gmail, etc. via subprocess) is deferred — the Tool abstraction is
correct shape but subprocess management of upstream `mcp`-SDK servers
is a separable future phase. The existing CapableDeputy MCP server
(see Phase 4.5b) goes the *other* direction (capdep as MCP server).

**Done-when criteria met**: tool calls through the wrapper are
intercepted, gated, labeled; results propagate labels correctly;
CI passes with no network access.

### Phase 4 — Turn-Level Mode + LLM Loop  ·  **DONE** (`a6a3601` 4a, `200abfd` 4b, `58d3c7f` 4c)
- LLM types (Message, ToolCall, LLMResponse), `LLMClient` Protocol,
  `FakeLLMClient` for deterministic tests.
- Turn-level agent loop with label accumulation and gated dispatch.
- `LiteLLMClient` (production); LiteLLM-side tool name sanitization
  for Anthropic API compatibility.
- `capdep send <session> "<message>"` CLI command.
- `session.send` daemon RPC and `session.grant_capability`.
- E2E prescription scenario test (FakeLLMClient driving the
  health-meets-egress denial).

**Done-when criteria met**: a (replayed) LLM drives a session,
accumulates labels from tool results, and gets blocked at the correct
egress attempt — verified end-to-end via `session.send`, the
LabeledToolClient, the policy engine, and the audit log.

### Phase 4.5 — Real-LLM testing & MCP server  ·  **DONE** (`b49ffc3` 4.5a, `d487110` 4.5b, `d4a2379` 4.5c, `61022b5` 4.5d)

Originally not on the roadmap; added during Phase 4 as the natural
follow-on once the agent loop existed.

- `tool.call` daemon RPC for direct dispatch (4.5a).
- **MCP server** (`capdep mcp-server`) exposing the labeled tools to
  external MCP hosts like Claude Code. Stdio transport; tool calls
  forwarded through `tool.call` so policy + audit apply identically
  whether the agent loop is internal or external (4.5b).
- `ClaudeCodeLLMClient` (subprocess to `claude -p --output-format
  json`) for subscription-backed development without API keys (4.5c).
- Real-LLM integration tests against `claude-haiku-4-5` (gated on
  `ANTHROPIC_API_KEY`); fixed two real bugs (tool name sanitization,
  empty parameters_schema) found through real-LLM driving (4.5d).

**Done-when**: real Claude correctly identifies and explains the
fired rule on a structurally denied egress attempt.

### Phase 5 — Approval System  ·  **DONE** (`ac1ad0b` 5a, `14d9841` 5b, `cba4737` 5c)
- `ApprovalRequest` model + `ApprovalQueue` with submit / approve /
  deny / defer lifecycle and full audit emission.
- Cross-session declassification: approving SEND_EMAIL spawns a
  fresh purpose-limited session with a one-shot capability scoped
  exactly to the approved payload + recipient; dispatches via
  `LabeledToolClient`; aborts the purpose session.
- `email.send` native tool stub (outbox records sends for audit).
- `approval.list/show/submit/approve/deny/defer` daemon RPCs.
- `capdep approval` CLI subcommand.
- `capdep trace <session>` CLI with colored policy-decision output.
- E2E prescription-to-wife test verifies the full chain (block →
  submit → approve → execute in C → outbox has exactly one mail to
  the right recipient → original session still labeled).

**Note**: full TUI was deferred to Phase 7d. The CLI surface covers
the operational path completely.

**Done-when criteria met**: prescription-to-wife scenario runs
cleanly through the CLI: blocked, surfaced as approval, approved,
executed in a one-shot session, fully traceable.

### Phase 6 — Dual-LLM Mode  ·  **DONE** (`1f7217f`)
- `quarantined/schemas.py`: Pydantic models for the starter set
  (DoseSummary, FinancialSummary, ContactInfo) with bounded field
  lengths to limit smuggling.
- `quarantined/extractor.py`: extract through a quarantined LLM
  with no tools; reject tool_call attempts; strip markdown fences;
  validate via Pydantic.
- `quarantined.extract` native tool with NO additional_labels and
  NO inherent_labels — schema validation IS the declassification.
- App takes optional `quarantined_llm` parameter (falls back to
  `llm_client`).
- E2E test asserts the planner LLM's recorded conversation context
  provably never contained the raw labeled text.

**Done-when criteria met**: extraction-style requests run through
the dual-LLM path; the planner LLM's recorded context provably
never contains the underlying labeled data.

### Phase 7 — Mode Dispatcher, Visibility, Pattern Rules, TUI, MCP Polish  ·  **DONE** (`10c34e0` 7a, `919ac3d` 7b, `5bc1911` 7c, `4e158e6` 7d, `45c3ddc` + `0e47992` 7e, `721f347` 7f)

Compiled deferred work that turned out to be both achievable and
high-leverage in v0.1.

- **7a — Execution mode dispatcher**: `select_mode(label_set,
  registry)` auto-escalates to dual-LLM when the session carries any
  confidential.* label and a quarantined extractor is registered.
  Logged as `mode.selected` audit event. In dual-LLM mode,
  `build_tool_descriptions` hides raw labeled-data readers
  (memory.read, fs.read, web.fetch).
- **7b — Capability-driven tool visibility**: a tool is visible to
  the LLM only if the calling session holds at least one capability
  whose kind matches the tool's `capability_kind`. Defense-in-depth
  on top of the per-call capability check; also stops leaking
  knowledge of out-of-scope tools into the LLM's prompt.
- **7c — Pattern approval rules**: `ApprovalPatternRule` with strict
  validation (rejects bare `*`, requires domain anchors for globs,
  caps TTL at 30 days). Auto-approves matching future requests but
  still emits the full `approval.requested` + `approval.approved`
  audit pair. Revocable instantly.
- **7d — Textual TUI**: `capdep tui` minimum viable Textual app.
  Three panes (Sessions, Approvals, Events), approval modal with
  verbatim payload rendering (DESIGN.md §8.2 hard rule). Polls daemon
  every 1.5s. Full session-graph view, trace pane, and pattern-rule
  editor moved to v0.2.
- **7e — Pattern rule daemon RPCs and CLI**: `approval_pattern.list/
  create/revoke` RPCs and `capdep approval pattern` CLI subcommand.
- **7f — MCP spec compliance review**: audited mcp_server.py against
  modelcontextprotocol.io 2025-11-25; fixed `inputSchema` bug; added
  `isError`, `structuredContent`, `ToolAnnotations`,
  `_meta` (capability_kind, inherent_labels, decision, rule, labels);
  documented full coverage map in `docs/mcp-spec-review.md`.

### Phase 8 (originally Programmatic Mode / Starlark)  ·  **PLANNED**
- Fork `starlark-py`; extend Value with `labels`.
- Implement label propagation through binary ops, function calls,
  attribute access.
- Tool call resolution and gating in the interpreter.
- Static policy analyzer (rejects unconditional violations).
- `capdep run <prog.star>` and `capdep dry-run <prog.star>`.
- E2E: multi-step labeled-data pipeline executed and audited.

**Status**: not started. Requires substantial Starlark interpreter
work; promotes the third execution mode (DESIGN.md §5.3) from
"reserved" to "implemented". Reasonable as a focused phase after
v0.1 ships.

### Phase 9 (originally Polish)  ·  **PARTIALLY DONE**

- **DONE**: README with canonical use cases (`README.md`),
  CONTRIBUTING.md, CODE_OF_CONDUCT.md, demo walkthroughs in
  `docs/demos/`, MCP spec review in `docs/mcp-spec-review.md`.
- **PARTIAL**: docs site (have `docs/` markdowns; no published site).
- **NOT DONE**: asciicasts / demo videos; v0.1 release tag.

## v0.1 status summary

**Built and verified end-to-end:**
- Phases 0–7 complete (with Phase 8 / Programmatic deferred).
- 317 unit tests + 2 integration tests, all passing.
- 100% coverage on every security-critical module (policy engine,
  conflict rules, capabilities, labels, actions, session graph,
  session model, store, app, paths, all daemon handlers, mode
  dispatcher, approval queue, approval pattern).
- Real-LLM integration test (`claude-haiku-4-5`) demonstrates the
  policy holds against a real model and the model articulates the
  fired rule by name.
- Three demo walkthroughs in `docs/demos/` cover deterministic,
  real-LLM, and external-MCP-host scenarios.

**Architectural properties true today:**
- The LLM cannot author label state; the harness owns it
  (`SessionGraph`).
- The LLM cannot reference tools its session has no capability for —
  visibility-filtered before LLM-reachability.
- Cross-session data flows require explicit human approval and run
  in purpose-limited sessions with one-shot capabilities.
- Schema-validated extraction provides a structural declassification
  alternative to approval where appropriate.
- Mode is auto-selected per turn and logged.
- All decisions are inspectable and replayable from the audit log.

## Beyond v0.1

### v0.2 — MCP surface expansion, container deployment, ecosystem

These are the items that turned out to be the right scope for a
focused follow-up phase, mostly informed by what was built in v0.1.

**MCP surface expansion** (per `docs/mcp-spec-review.md`):

- **Resources for memory entries**: expose `capdep://memory/{key}` as
  resources with labels in `_meta`. Capability-aware listing (a
  session sees only resources it has READ_FS for); resource reads
  dispatch through `LabeledToolClient` so policy + label propagation
  is identical to `memory.read` tool. Surfaces label inventory
  discoverably without invocation.
- **Prompts for canonical workflows**: starter set of parameterized
  prompts (`prescription-review`, `daily-briefing`, `safe-share`,
  `untrusted-research`). Each prompt is a workflow template the
  agent executes step by step; every step still goes through the
  policy engine. Lets MCP hosts surface user-facing menus of policy-
  controlled workflows.
- **Elicitation for in-flow approvals**: when SEND_EMAIL is queued
  for approval, fire `elicitation/create` to the MCP host so the
  user decides inside the host's chat UI rather than switching to a
  separate terminal.
- **Logging notifications**: mirror policy decisions and label
  propagations to the host as `notifications/message` so host UIs
  surface them in real time.
- **`tools/list_changed` notifications**: when session capabilities
  change at runtime, the visible tool set changes; push the
  notification.

**Container deployment**:
- Containerfile + Podman quadlet/systemd unit. Documented volume
  layout for `state.db`, `audit.jsonl`, configurable user-data
  mounts. Default-deny network egress with allowlists for configured
  LLM API endpoints. CI lane runs the test suite inside the
  container.

**Real upstream MCP server integration** (deferred from Phase 3):
- `LabeledMcpClient` that wraps subprocess MCP servers (Filesystem,
  Fetch, Gmail, Google Calendar, Obsidian, GitHub) using the
  upstream `mcp` Python SDK on the *client* side. Vanilla servers,
  no forks — labels and policy live in the wrapper.
- Per-server label declaration in YAML config.
- YAML-driven fakes for upstream servers used in CI to avoid hitting
  real APIs in tests.

**TUI completion**:
- Five-pane layout (Sessions, Conversation, Approvals, Trace,
  Events) per DESIGN.md §10.3.
- Trace pane drill-down per §9.4.
- Session-graph view (parent/child tree).
- Pattern-rule editor.
- Real-time event push (instead of 1.5s polling) — needs a streaming
  RPC variant.

**Ecosystem and privacy**:
- `SKILL.md` adapter for ingesting OpenClaw skills as labeled MCP
  tools.
- Local-model planner option — keep the privileged LLM local
  (Ollama, llama.cpp), only send non-labeled handles to a frontier
  model.
- Approval pattern library — shareable, version-controlled approval
  pattern rules for common workflows.
- Engagement with OpenClaw RFC #39160 — propose CapableDeputy as the
  answer.

### v0.3 — Programmatic Mode, Multi-tenancy, Federation
- **Programmatic mode** (DESIGN.md §5.3): Starlark interpreter with
  label-aware values and static policy analysis.
- Per-tool container isolation: each MCP server in its own container
  with policy-driven network and FS views.
- **Per-session unforgeable tool tokens** (strict ocap): each session
  sees capabilities under fresh random tokens; the LLM cannot
  reference tools outside its compartment because it doesn't know
  their session-specific names.
- Per-user label spaces for household deployments.
- Inter-host federation: phone + laptop with shared session state
  and remote approvals.
- Hardware token integration for high-stakes approvals.

### v0.4+ — Formal Methods
- TLA+ specification of session graph and policy semantics.
- Mechanized proofs of key safety properties (label monotonicity,
  capability unforgeability).
- Independent security audit.
