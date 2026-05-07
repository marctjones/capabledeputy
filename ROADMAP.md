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

### Phase 8 (Programmatic Mode)  ·  **IN v0.3** (see below)
- Originally specified as a forked `starlark-py`; revised to a
  Python-AST-subset interpreter (LLMs already write Python natively;
  the AST subset is statically analyzable; ~10× less code than a
  full Starlark fork while preserving security properties). See
  v0.3 entry below for shipped commit.

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

## v0.2 — MCP surface expansion, container, upstream MCP  ·  **DONE**

| Item | Status | Commit |
|---|---|---|
| MCP Resources for memory | DONE | `3368557` |
| MCP Prompts (4 canonical workflows) | DONE | `3368557` |
| MCP Elicitation for in-flow approvals | DONE | `d6df6ee` |
| MCP Logging notifications | DONE | `d6df6ee` |
| Container deployment (Containerfile + quadlet + docs) | DONE | `1155d81` |
| TUI five-pane layout (Sessions/Approvals/Conversation/Trace/Events) | DONE | `8bad123` |
| Upstream MCP wrapping foundation (`LabeledMcpAdapter`, `UpstreamManager`) | DONE | `e70e7f9` |

## v0.3 — Programmatic mode + observability primitives

| Item | Status | Notes |
|---|---|---|
| Daemon subscription primitive (publish/subscribe over JSON-RPC) | DONE | `4919fc4` |
| MCP `tools/list_changed` on capability changes | DONE | `29d0b64` |
| Real-time TUI event push via subscription | DONE | `29d0b64` |
| Programmatic execution mode (DESIGN.md §5.3, §10.5) | NEXT | Python-AST-subset interpreter; label-aware values; static policy analyzer; `capdep run` / `capdep dry-run` |
| Per-session unforgeable tool tokens (strict ocap) | PLANNED | DESIGN.md §15 — defense-in-depth on top of 7b visibility filter |
| `SKILL.md` adapter for OpenClaw skills | PLANNED | Ecosystem |
| Local-model planner option | PARTIALLY DONE | LiteLLM already supports Ollama; needs a doc note + sample config |
| Approval pattern library | PARTIALLY DONE | Pattern infrastructure shipped in 7c; pre-baked patterns are content not code |

## v0.4+ — Federation and formal methods

- Per-tool container isolation: each MCP server in its own container
  with policy-driven network and FS views.
- Per-user label spaces for household deployments.
- Inter-host federation: phone + laptop with shared session state
  and remote approvals.
- Hardware token integration for high-stakes approvals.
- TLA+ specification of session graph and policy semantics.
- Mechanized proofs of key safety properties (label monotonicity,
  capability unforgeability).
- Independent security audit.
