# CapableDeputy — Implementation Roadmap

This roadmap accompanies DESIGN.md. Phases marked **DONE** are landed
on `main` with the listed commit; **IN PROGRESS** is partial; **PLANNED**
is upcoming. Phases assume the testing strategy described in §12 of
DESIGN.md and the trace/observability model described in §9.

## v0.1 — Core Runtime

### Phase 0 — Foundations  ·  **DONE** (`fa141f3`)
- Repository scaffold, proprietary (All Rights Reserved) license, code-of-conduct, contributing guide.
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
  CONTRIBUTING.md, CODE_OF_CONDUCT.md, MCP spec review
  (`docs/mcp-spec-review.md`), nine end-to-end demo walkthroughs
  spanning security and assistant workflows
  (`docs/demos/01-prescription-to-wife.md` through
  `docs/demos/09-accountant.md`), mkdocs site scaffold, v0.1.0 + v0.4.0
  release tags.
- **NOT DONE**: asciicasts / demo videos (require terminal recording).

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
| Programmatic execution mode (DESIGN.md §5.3, §10.5) | DONE | `be1f43e` — interpreter, analyzer, `capdep run`/`dry-run` |
| Programmatic planner loop (LLM emits a program per turn) | DONE | `ca38d3e` — `agent/programmatic_loop.py`; auto-dispatch via session flag; `--mode programmatic` CLI override |
| PROGRAMMATIC selection in mode dispatcher | DONE | `ca38d3e` — prefer + force overrides |
| Per-session unforgeable tool tokens (strict ocap, opt-in) | DONE | `ca38d3e` — deterministic per-session aliases; real-LLM comparison test shows no model-perf delta on the prescription scenario |
| `SKILL.md` adapter for OpenClaw skills | DONE | YAML frontmatter + Markdown body; runs through quarantined LLM; optional schema for structured extraction |
| Local-model planner option | DONE | `docs/local-model-planner.md` + `configs/local-planner.env`; daemon honours `CAPDEP_QUARANTINED_LLM_MODEL` and `CAPDEP_SKILLS_DIR` |
| Approval pattern library | DONE | `configs/approval-patterns.yaml` starter pack + `capdep approval pattern import <path>` CLI |

## v0.4 — Federation, isolation, formal model  ·  **DONE**

| Item | Status | Notes |
|---|---|---|
| Per-tool container isolation | DONE | Strict-default podman wrapping in `capabledeputy.upstream.isolation`; YAML `isolation:` block; quadlet generator; `docs/per-tool-isolation.md` |
| Per-user label spaces (multi-tenant labels, additive) | DONE | `policy.tenancy.Tenant` + `TenantLabel`; `policy.multi_tenant_engine.decide_multi_tenant`; existing single-user code paths unchanged |
| Inter-host federation primitive | DONE | `federation.HostId`, signed session export/import, remote-approval envelopes; full sync still v0.5+ |
| Hardware-token approval signing | DONE (software) / STUB (YubiKey) | `approval.signer` with HMAC software key + canonical payload + queue-level `require_signature`; YubiKey class shape ships, body raises NotImplementedError |
| TLA+ specification | DONE | `spec/CapableDeputy.tla` + `.cfg` covering session lifecycle, policy decision, label monotonicity, no-silent-egress-on-PHI |
| Mechanized proofs (Coq/Lean/Isabelle) | DEFERRED to v0.5+ | TLA+ model-checking covers the same property space; full mechanization is a multi-month research project |
| Independent security audit | OUT OF SCOPE for code work | Process item; engage a firm |

## v0.5+ — Long tail

- Continuous bidirectional federation sync (currently a primitive).
- Asymmetric crypto for cross-host identity (currently HMAC over
  shared keys; fine for a single household, not for a public
  directory).
- YubiKey PIV / FIDO2 backend body.
- Mechanized proofs of label monotonicity and capability
  unforgeability in Coq/Lean.

## v0.7+ — Secure-alternative hardening  ·  **DONE** (WI-1..WI-4)

Project thesis sharpened this period: CapableDeputy is a deliberately
less-capable, **secure OpenClaw alternative** (not an OpenClaw layer,
not a feature-parity agent) — comparison set is NemoClaw / DefenseClaw,
not OpenClaw. Codified as Constitution v1.1.0 Principles VI (Fail-Closed
by Default, NON-NEGOTIABLE) and VII (Secure-by-Reduction; Owned TCB).

| Item | Status | Commit |
|---|---|---|
| WI-1 fail-closed upstream MCP adapter (strict default, granular destructive mapping, `rejected_tools`) | DONE | `f464cf6` |
| WI-2 curated MCP catalog (`configs/curated/`: official + Slack + Google Workspace, all strict/isolated) | DONE | `b017f96` |
| WI-3 tasks/reminders stub + business-workflow scenario pack (incl. flagship injection→exfil block) | DONE | `66ab4c0` |
| WI-4 daemon `--config`/`CAPDEP_CONFIG` upstream wiring; real `mcp-server-fetch` verified e2e | DONE | `2600391` |
| Constitution v1.1.0 (Principles VI, VII) | DONE | `a0a1648` |

Substrate ports (`SandboxActuator`, `AdmissionLabeler`) and a jailed
tiered EXECUTE tool (WI-5) are deferred; OpenShell/CodeGuard are
leveraged only behind those ports (Constitution VII).

## v0.8 — Capability delegation chains  ·  **PARTIAL** (US1 + US3 shipped; US2 deferred)

Spec `specs/002-capability-delegation-chains/`. Adds engine-derived
attenuated capability delegation with monotonic-narrowing enforcement,
bounded depth, and LLM-isolated derivation.

| Item | Status | Commit |
|---|---|---|
| Phases 1–2: audit-event types, `DelegationRequest`/`Refusal`, `parent_audit_id`/`depth` on `Capability`, `pattern_is_subset` (T001–T010) | DONE | `cd0c585` |
| US1 (T011–T019): `derive_delegated_capability` clamp-or-refuse across six dims + FR-016 non-enum fields (`revoked_by`/`expiry`/`origin`); `SessionGraph.delegate`; `session.delegate` RPC + CLI; LLM-isolation invariant test | DONE | `951b4ce` |
| US3 (T029–T030): depth-limit precondition + tests (shipped silently as part of T011's `depth_limit` parameter) | DONE | `951b4ce` |
| US2 (T020–T028): cascade revocation across the live graph; pooled rate fan-out (FR-015); `capability.revoke` RPC/CLI; pending-approval invalidation | DONE | `ca74e35` |
| Polish (T031–T033): e2e quickstart test + determinism test + doc cross-refs | DONE | (this commit) |

**Spec 002 fully implemented** (US1 + US2 + US3 shipped; e2e + determinism
tests green; cascade computed deterministically at decide-time via
O(depth) provenance walk).

## v0.9 — Labeling framework  ·  **IN FLIGHT** (US1–US6 contracts complete; composition wire-in pending)

Spec `specs/003-labeling-framework/`. Four-axis labeling
(category/provenance/effect/decision-context) with deterministic
sensitivity-resolution layer, named Source/Location Label Bindings,
unified Reversibility & Mutability, Risk-Preference + Outcome
Envelopes, Override Policy distinct from approval, Relationship
Groups, Expectation Bindings, EXECUTE tiering, isolation posture, and
Reference Handle (Pattern ③). 117 tasks across 9 phases; US1 (orthogonal
labels + deterministic resolution) is the MVP. **Out of scope** —
deferred to spec 004 substrate track: `SandboxActuator` impl,
`EXECUTE.sandbox` jailed tool, provider source adapters, versioned-write
actuator impls.

| Item | Status | Commit / Tag |
|---|---|---|
| Spec (`/speckit-specify` + two clarify passes + Principle-VIII gap close) | DONE | `10633e3` → `a50272d` |
| Plan + Phase 0 research + Phase 1 data-model/contracts/quickstart | DONE | `d6b60c1` |
| Tasks (117 tasks, 9 phases, US1=MVP) | DONE | `d2190b2` |
| Analyze remediation (6 edits: 2 MEDIUM, 4 LOW) | DONE | `6025fc1` |
| Phase 1 Setup (T001–T005) | DONE | `237b9f7` |
| Phase 2 Foundational (T006–T020, T118–T121) | DONE | `f2ab1e3` → `f00b912` |
| Phase 3 US1 MVP (T021–T035) | DONE | `v0.9.0-us1-mvp` |
| Phase 4 US2 — decision-context + never-auto (T036–T049) | DONE | `v0.9.0-us2-checkpoint` |
| Phase 5 US3 — purpose admissibility (T050–T060) | DONE | `v0.9.0-us3-checkpoint` |
| Phase 6 US6 — practical adoption layer (T061–T086) | CONTRACTS | `v0.9.0-us6-checkpoint` |
| Phase 7 US4 — assurance deltas (T087–T095) | CONTRACTS | `v0.9.0-us4-checkpoint` |
| Phase 8 US5 — clearance / floor / Pattern ③ (T096–T107) | CONTRACTS | `v0.9.0-us5-checkpoint` |
| Phase 9 Polish (T108–T117) | IN FLIGHT | — |

**Composition wire-in pending** (called out in the US4/US5/US6
commit messages): `engine.decide()` composing the envelope dial,
OverrideRequired distinct return, optimistic-execution short-circuit,
write-discipline verification, clearance check, integrity floor,
control-plane reflexivity, reversibility-weighted gating, and the
dispatcher-side bind of ReferenceHandle. Also pending: T012-full
ToolDefinition extension (effect_class, social_commitment,
default_reversibility, default_mutability_target_facets,
tool_provenance, surfaces_destination_id, risk_ids).

**Composition + runtime activation landed** in rc.1 through rc.6
(2026-05). All wire-ins above are done; T012-full additive fields
shipped on every native tool; daemon builds the PolicyContext from
operator configs at startup; CLI ↔ daemon override IPC bridges the
critical state gap; per-session profile derivation activates BLP +
Biba.

| Item | Status | Tag |
|---|---|---|
| Composition sub-phases A-E (foundation/handle binding/override/bindings/reversibility gate) | DONE | `v0.9.0-rc.1` → `v0.9.0-rc.3` |
| Demos #1/#3/#7/#8 wired (envelope dial, approval grouping, control-plane reflexivity, clearance + floor) | DONE | `v0.9.0-rc.3` |
| Policy-language gap closures (multi-category predicates, time-of-day, AssignmentProvenance, raise-only-inspector hook, Pattern (5) demo actuator) | DONE | `v0.9.0-rc.5` |
| Runtime activation (daemon wires PolicyContext from configs; SessionGraph spawn-refusal; CLI ↔ daemon override IPC; profile-derived BLP/Biba) | DONE | `v0.9.0-rc.6` |

## v0.9 → v1.0 (spec 004) — MCP + substrate integration  ·  **FOUNDATIONS LANDED**

Spec `specs/004-mcp-and-substrate/`. Foundation phases (P0/P1/P2/P3
substrate ports + builtins + tests) are now shipped:

| Phase | Items | Status |
|---|---|---|
| **P0** programmatic primitives | RaiseOnlyInspector, DecisionInspector + 2 builtins, DeclassifyingTransformer + 2 builtins + chokepoint wire-in, per-arg payload labels (FR-027/039), 3 new audit events, named HookRegistry (T020) | **DONE** |
| **P1** MCP surfaces | SamplingMediator port + 3 builtins, ElicitationMediator port + 3 builtins, upstream MCP resources/list + resources/read | **DONE** |
| **P2** OSCAL + observability | OSCAL Component Definition emission, OSCAL System Security Plan, audit-evidence bundle (events grouped by NIST control), `capdep compliance-emit-*` CLI | **DONE** |
| **P3** policy authoring | PolicyScriptHost port + SafePythonScriptHost reference, OPA sidecar adapter (OpaConsultingInspector) | **FOUNDATIONS** |

Spec 002 (capability delegation chains) is also **fully implemented**
(US1 + US2 cascade + US3 depth) — see the v0.8 section above.

Operator-visible new surfaces:
- `capdep compliance-emit-oscal --output ./oscal-bundle.json`
- `capdep compliance-emit-ssp --output ./ssp.json`
- `capdep compliance-emit-evidence --audit-log ./audit.jsonl --output ./evidence.json`
- 5 bundled Python MCP servers: `capdep mcp-server-{fs,fetch,search,memory,git}`
- 3 new audit event types: `inspector.applied`, `decision_inspector.applied`, `declassifier.applied`
- Per-purpose `default_capabilities` + `bindings` in `configs/purposes.yaml`
- Operator-published resources via `configs/resources.yaml` + `resources.list`/`resources.read` tools

Generic MCP adapter + 8 tier-1 MCP server mappings + native code.execute +
real SandboxActuator providers (Podman, Modal, Firecracker) + OTLP/Splunk
sinks + WebAuthn/Duo/OAuth identity stack + regression demos + DefenseClaw
integration plugin: **still designed, not implemented**.

See `specs/004-mcp-and-substrate/research.md` for the competitive
landscape research that motivated the integration target list, and
`specs/004-mcp-and-substrate/defenseclaw-integration.md` for the
complementary/competing analysis against Cisco DefenseClaw.

The v0.9 labeling-framework design is captured in
`docs/design-v0.9-labeling.md` (the historical design dump) and is now
fully formalized in the 003 spec set.
