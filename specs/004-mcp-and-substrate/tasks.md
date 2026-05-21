---
description: "Spec 004 task list — MCP adapter, MCP integrations, native tools, sandbox providers, observability, identity"
---

# Tasks: Spec 004 MCP + Substrate Integration

**Input**: Design documents from `/specs/004-mcp-and-substrate/`
**Prerequisites**: spec.md, plan.md, research.md

## Format: `[ID] [P?] [Phase] Description`

- **[P]**: Different file, no incomplete-task dependency — parallelizable.
- Each task has an exact file path.

---

## Phase 1: Generic MCP adapter (foundation)

Blocks every tier-1 server integration. No demos without this.

- [ ] **U001** Create `src/capabledeputy/mcp_adapter/__init__.py` + module skeleton.
- [ ] **U002** Implement stdio MCP transport in `src/capabledeputy/mcp_adapter/transport_stdio.py`. Reads MCP protocol over child-process stdio.
- [ ] **U003** [P] Implement HTTP MCP transport in `src/capabledeputy/mcp_adapter/transport_http.py`. Reads MCP protocol over HTTP(S) with operator-curated bearer/OAuth credentials from the secrets store.
- [ ] **U004** Define mapping-file schema in `src/capabledeputy/mcp_adapter/mapping.py`: per-MCP-tool T012 declarations (effect_class, default_reversibility, social_commitment, tool_provenance, surfaces_destination_id, risk_ids, accepts_handles, handle_arg_names).
- [ ] **U005** [P] Mapping-file loader with fail-closed validation: missing required field on a mapped tool ⇒ refuse to register that tool (Principle VI).
- [ ] **U006** ToolDefinition factory in `src/capabledeputy/mcp_adapter/factory.py`: consume MCP `tools/list` + mapping → produce CD ToolDefinitions tagged `tool_provenance="curated-mcp"` (so FR-031 treats the declarations as a deterministic relax origin).
- [ ] **U007** Extend `audit/events.py` with new EventTypes: `MCP_SERVER_LOADED`, `MCP_TOOL_REGISTERED`, `MCP_TOOL_REFUSED`, `MCP_REQUEST_SENT`, `MCP_RESPONSE_RECEIVED`.
- [ ] **U008** [P] Test `tests/test_mcp_adapter_stdio.py`: launches a fake stdio MCP server, drives `tools/list`, asserts the factory produces correctly-tagged ToolDefinitions.
- [ ] **U009** [P] Test `tests/test_mcp_adapter_http.py`: same shape, HTTP transport.
- [ ] **U010** [P] Test `tests/test_mcp_adapter_failclosed.py`: malformed mapping file ⇒ tool refused with `MCP_TOOL_REFUSED` audit event.
- [ ] **U011** Wire MCP adapter into App lifecycle: on daemon start, scan `configs/mcp_servers/*.yaml`, instantiate the adapter for each, register the resulting tools.
- [ ] **U012** Document the mapping-file shape in `contracts/mcp_mapping.md`.

---

## Phase 2: Tier-1 MCP server integrations

For each: mapping file + fixture + integration test. No upstream modification.

- [ ] **U013** [P] **Anthropic filesystem MCP** — mapping in `mappings/anthropic-filesystem.yaml`; fixture in `tests/fixtures/mcp/anthropic-filesystem.jsonl`; test `tests/integration/test_mcp_anthropic_filesystem.py`.
- [ ] **U014** [P] **Anthropic github MCP** — mapping + fixture + test (parallel of U013).
- [ ] **U015** [P] **Anthropic gitlab MCP** — likewise.
- [ ] **U016** [P] **Anthropic postgres MCP** — likewise.
- [ ] **U017** [P] **Anthropic sqlite MCP** — likewise.
- [ ] **U018** [P] **Anthropic brave-search MCP** — likewise; this is also the back-end for the `web.search` native tool (Phase 3).
- [ ] **U019** [P] **Anthropic puppeteer MCP** — likewise.
- [ ] **U020** [P] **GitHub MCP** (vendor-maintained) — mapping in `mappings/github.yaml`; integration test against a recorded fixture of code review, PR management, issue tracking.
- [ ] **U021** [P] **Google Workspace MCP** — Gmail + Calendar + Drive + Docs + Sheets + Slides + Forms + Tasks + Contacts + Chat. Largest mapping; bundle into `mappings/google-workspace.yaml`. Integration test against a recorded fixture covering email send + calendar event create + drive write.
- [ ] **U022** [P] **Microsoft 365 MCP** — Outlook + Teams parity for Microsoft customers.
- [ ] **U023** [P] **Notion MCP** — pages, databases, blocks. The mapping must treat `database.write` as `social.commit_record` per FR-019 (knowledge-base records are reputationally hard to retract).
- [ ] **U024** [P] **Slack MCP** — `send_message` declared `social.send_message` per FR-019 (chat sends are irreversible).
- [ ] **U025** [P] **Playwright MCP** — every tool that publishes to the web declared `social.post_public` per FR-019.
- [ ] **U026** [P] **Context7 MCP** — read-only docs lookup; reversible/system.
- [ ] **U027** Composite test `tests/integration/test_tier1_mcp_servers.py` — for every tier-1 server: confirm registration succeeds, confirm at least one representative tool's decision outcome matches the operator-declared expectation.

---

## Phase 3: Native tools

- [ ] **U028** [P] Create `src/capabledeputy/tools/native/fs.py` — fs.read / fs.write / fs.create / fs.modify / fs.delete with binding-canonicalized paths. Each declares full T012 fields. Writes route through `VersionedWritePort` when the binding's `write_discipline=version-preserving`.
- [ ] **U029** [P] Tests `tests/test_tools_native_fs.py` — read, write, create, modify, delete each gated correctly; binding canonicalization on `file://` paths.
- [ ] **U030** [P] Create `src/capabledeputy/tools/native/web_search.py` — `web.search` over Brave / DuckDuckGo / SearXNG. FR-023 binding required; provider chosen via operator config.
- [ ] **U031** [P] Tests `tests/test_tools_native_web_search.py` — unbound search provider ⇒ refuse; bound provider ⇒ allow; returned URLs flagged `external-untrusted` provenance.
- [ ] **U032** Create `src/capabledeputy/tools/native/code_execute.py` — `code.execute` tool that delegates to a `SandboxActuator` provider (depends on Phase 4). Effect class `EXECUTE.sandbox`.
- [ ] **U033** Tests `tests/test_tools_native_code_execute.py` — without actuator wired ⇒ OVERRIDE_REQUIRED; with actuator wired ⇒ region created → executed → discarded; isolation_posture composes to reversible/system.

---

## Phase 4: Container substrate providers

- [ ] **U034** [P] Implement `src/capabledeputy/substrate/podman_sandbox.py` — rootless container, `--read-only`, `--net=none` default; ephemeral volumes only for declared `read_write` paths.
- [ ] **U035** [P] Implement `src/capabledeputy/substrate/modal_sandbox.py` — hosted code sandbox via the Modal API; cost-bounded.
- [ ] **U036** [P] Implement `src/capabledeputy/substrate/firecracker_sandbox.py` — kernel-level isolation; matches NemoClaw's primitives.
- [ ] **U036A** [P] Implement `src/capabledeputy/substrate/nemoclaw_sandbox.py` — wrap NVIDIA's OpenShell runtime as a `SandboxActuator` provider. Operators already running NemoClaw can plug CD in without changing substrate. Includes a one-time migrator that translates NemoClaw's YAML policy (filesystem/network/syscall rules) into CD's binding + envelope configs. Sibling to Podman/Modal/Firecracker at the same layer.
- [ ] **U036B** [P] Tests `tests/test_nemoclaw_sandbox.py` — lifecycle parity with the other providers; policy translation correctness (recorded NemoClaw YAML → expected CD bindings).
- [ ] **U037** Define attestation signed-manifest format in `src/capabledeputy/substrate/attestation.py`: (region_id, image_digest, command, env, exit_code, output_digest) signed with the operator's signing key.
- [ ] **U038** Tests `tests/test_sandbox_providers.py` — lifecycle (create → execute → discard) + attestation verification + isolation-posture composition for each provider.
- [ ] **U039** Operator-config selector for which substrate provider to use; configurable per `EXECUTE.sandbox` invocation OR globally.
- [ ] **U040** CI guard: refuse to deploy with `InProcessSandboxActuator` wired in production (i.e., `is_demo_actuator()` returns True). Lint/admission check in `daemon/lifecycle.py`.

---

## Phase 5: Observability sinks

- [ ] **U041** [P] Add OTLP exporter in `src/capabledeputy/audit/otlp_writer.py`: every audit event becomes an OTLP log + span attribute set. Resource attributes from operator config.
- [ ] **U042** [P] Add Splunk HEC exporter in `src/capabledeputy/audit/splunk_writer.py`.
- [ ] **U043** Multi-sink fan-out: AuditWriter writes to JSONL (existing) + OTLP + Splunk per operator config; failure in any one sink does not block dispatch (parallel writes).
- [ ] **U044** Tests `tests/test_otlp_writer.py` + `tests/test_splunk_writer.py` — mocked endpoints; verify every CD event type produces the expected payload shape.
- [ ] **U045** [P] Document the alignment with DefenseClaw's observability targets in `contracts/observability.md`.

---

## Phase 6: Identity / authentication

- [ ] **U046** WebAuthn registration + attestation surface in `src/capabledeputy/auth/webauthn.py`: register an authenticator, sign an override-attestation payload, verify the signature.
- [ ] **U047** Extend `cli/override_cmd.py` `attest` subcommand: when the policy is `dual-control`, replace `--confirm` boolean with a WebAuthn challenge/response. Bare `--confirm` rejected per FR-108.
- [ ] **U048** [P] Test `tests/test_webauthn_attestation.py` — register; happy-path attestation; replayed signature refused; wrong-credential refused.
- [ ] **U049** [P] Duo Auth API integration in `src/capabledeputy/auth/duo.py` — when the session spawn invokes Duo MFA, AxisD.authentication = `duo-mfa`. Decision rules can predicate on it.
- [ ] **U050** [P] Test `tests/test_duo_authentication_field.py` — verify Axis-D propagation.
- [ ] **U051** OAuth 2.1 device authorization grant for remote-service sign-in in `src/capabledeputy/auth/oauth.py`. Tokens stored in `src/capabledeputy/secrets.py` with the same fail-closed semantics as v0.9 configs.
- [ ] **U052** Wire OAuth tokens into the MCP adapter HTTP transport: a tool whose mapping declares `auth: oauth2:<provider>` gets the token injected from the secrets store at dispatch.
- [ ] **U053** [P] Test `tests/test_oauth_token_routing.py` — missing token ⇒ tool registration refused; present token ⇒ Authorization header populated; rotation ⇒ refresh dance works.

---

## Phase 7: Regression demos against the documented incident corpus

These are the positioning artifacts (research.md "What CD should test itself against").

- [ ] **U054** Reproduce the **Meta director autonomous-deletion scenario** as `demos/scenarios/meta_director_regression.py`: drive a Google Workspace MCP session through context compaction; attempt autonomous bulk delete; assert FR-019 reversibility-irreversible + optimistic-auto carve-out DENY.
- [ ] **U055** [P] Demo write-up `demos/scenarios/meta_director_regression.md` with full audit log evidence.
- [ ] **U056** Reproduce the **ToxicSkills payload survival demo** as `demos/scenarios/toxicskills_regression.py`: take 5 recorded payloads from the Snyk ToxicSkills corpus, install each as a ToolDefinition with the malicious author's T012 declarations, drive the published attack flows; assert all 5 refused via capability narrowing + axis-B taint + FR-031 asymmetry.
- [ ] **U057** [P] Demo write-up `demos/scenarios/toxicskills_regression.md` with audit log evidence.

---

## Phase 8: Polish + DefenseClaw + NemoClaw integration

DefenseClaw and NemoClaw integrate at two distinct layers:

- **DefenseClaw plugin (U058)** — CD-as-policy-backend FOR DefenseClaw.
  CD's engine.decide() replaces DefenseClaw's regex+optional-LLM-judge
  runtime guardrails. DefenseClaw's scanner stack + sandbox + audit
  pipeline + identity mapping stay unchanged. See
  `defenseclaw-integration.md`.

- **DefenseClaw scanner tools (U058A-U058B)** — DefenseClaw's CodeGuard
  AS A SET OF CD TOOLS. Operators can call the scanner from inside
  `capdep chat` before installing new ToolDefinitions or MCP servers.
  This is the inverse direction: CD-calls-DefenseClaw rather than
  DefenseClaw-calls-CD.

- **NemoClaw audit-sink (U058C)** — already covered partially by
  U041/U042 (OTLP/Splunk). NemoClaw's audit pipeline is the same
  shape; this task ensures CD's audit events flow into a deployed
  NemoClaw observability stack without operator-side translation.

- [ ] **U058** [P] **DefenseClaw plugin (CD as deterministic policy oracle)**: `src/capabledeputy/integrations/defenseclaw_plugin/` — a thin wrapper that exposes CD's `engine.decide()` as a DefenseClaw policy backend, replacing DefenseClaw's regex+optional-LLM-judge path with CD's deterministic engine. See `defenseclaw-integration.md` for the architectural assessment.
- [ ] **U058A** [P] **DefenseClaw scanner tools** — `src/capabledeputy/tools/native/security.py` with `security.scan_code`, `security.scan_skill`, `security.scan_mcp` that dispatch to DefenseClaw's CodeGuard REST API. Each declares full T012 fields (`effect_class="introspection.security_scan"`, `default_reversibility=reversible/system`, etc.); CD's policy gates the scan call itself before it hits DefenseClaw. Scanner findings flow back as risk_register annotations that orphan-citation refusal can act on.
- [ ] **U058B** [P] Tests `tests/test_defenseclaw_scanner_tools.py` — mocked CodeGuard endpoint; verify scan results convert to CD risk_ids; verify CD's engine still gates the scanner-tool call itself.
- [ ] **U058C** [P] **NemoClaw audit-sink alignment** — extend the OTLP/Splunk writers from U041/U042 with NemoClaw-event-naming conventions so CD events land in an operator's existing NemoClaw dashboards without operator-side schema translation. Documentation only if the OTLP semantic conventions align; small adapter otherwise.
- [ ] **U058D** [P] **CD as DefenseClaw custom scanner** — expose CD's `engine.decide()` as a scanner endpoint registered with DefenseClaw's plugin workflow. DefenseClaw invokes CD during admission; CD's verdict is composed with the other scanners' findings via Rego. Pairs with U058 for full coverage (admission + runtime).
- [ ] **U058E** [P] **CD as LiteLLM-compatible guardrail proxy** — port-4000 proxy that fronts upstream LLMs; intercepts every model call; axis-B taint composes onto the planner's context; tool calls in the response flow through CD's engine. Re-routes all model traffic; high value for Principle I but operationally invasive.
- [ ] **U058F** [P] **CD consumes DefenseClaw audit fan-out** — inbound webhook/OTLP listener that subscribes to DefenseClaw's audit fan-out; CodeGuard findings become axis-B raises on affected sessions via the FR-025 raise-only-inspector hook. Dedupe keyed by (source, audit_id, timestamp).
- [ ] **U058G** [P] **CD MCP adapter reuses DefenseClaw catalog ingestion** — delegates `clawhub` / `smithery` / `skills.sh` / `git` / `HTTPS YAML` / `file` catalog fetches to DefenseClaw's SSRF-guarded fetcher when DefenseClaw is wired; CD-internal fallback otherwise.
- [ ] **U058H** [P] Tests `tests/test_defenseclaw_directions.py` — one test per direction (U058 / U058A / U058D / U058E / U058F / U058G) against a recorded DefenseClaw fixture.
- [ ] **U059** [P] Update `ROADMAP.md` with spec-004 progress markers.
- [ ] **U060** Final sweep: `uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest`; tag `v1.0.0-rc.1`.

---

## Dependencies & Execution Order

- **Phase 1** blocks Phase 2 (no adapter, no integrations).
- **Phase 4** blocks the `code.execute` part of Phase 3 (U032/U033 wait on U034+).
- **Phase 1 + 4** block Phase 7 (regression demos need real MCP + real sandbox).
- **Phase 5, 6** independent — can land in parallel with anything.
- **Phase 8** runs last.

## Parallel Opportunities

- Phase 2 tasks U013–U026 all parallel (different mapping files).
- Phase 4 substrate providers (U034, U035, U036, U036A) all parallel.
- Phase 5 sinks (U041, U042) parallel.
- Phase 6 auth providers (U046, U049, U051) parallel.

## Notes

- We do NOT modify upstream MCP servers. Every integration is a mapping
  file + a CD-side adapter test. If an upstream server breaks, we update
  the mapping; the adapter stays.
- The integration tests use recorded fixtures by default. A separate `live`
  test marker exists for tests that hit a live upstream — gated behind
  operator opt-in so CI is deterministic.
- T012 declarations on MCP-mapped tools are the operator's truth: a tool
  whose upstream behavior changes should have its mapping reviewed, not
  silently re-tagged.

---

# Addendum: Programmatic Policy Primitives + Hook System (U101-U200)

**Added 2026-05-20** after the design rounds documented in
`mcp-protocol-fit.md`, `mcp-policy-integration.md`, and
`programmatic-policy-primitives.md`.

These tasks extend the policy engine with three programmatic
primitives (RaiseOnlyInspector, DecisionInspector,
DeclassifyingTransformer), the named hook system, and the per-arg
payload labeling mechanism. They are FOUNDATIONAL for the MCP work
above — most of the MCP surfaces require these primitives to be
mediated safely.

**Estimated effort:** ~60 days total across the new tasks below.

## Phase P0 — Foundational primitives (build first)

These are the architectural changes that the rest of the spec
depends on.

### Primitive ports

- [ ] **U101** Create `src/capabledeputy/policy/decision_inspector.py`
  with `DecisionInspector` Protocol + `DecisionRelax` /
  `DecisionTighten` dataclasses + `DecisionInspectorContext`.
- [ ] **U102** Create `src/capabledeputy/policy/declassifier.py`
  with `DeclassifyingTransformer` Protocol + `DeclassifyResult` +
  `DeclassifyContext` dataclasses.
- [ ] **U103** [P] Extend `policy/labels.py` with `lower_category`,
  `lower_tier` helpers for declassifiers.

### Hook execution machinery

- [ ] **U104** Create `src/capabledeputy/hooks/registry.py`:
  named-hook registry; primitives register per hook; engine looks
  up at runtime.
- [ ] **U105** Create `src/capabledeputy/hooks/loader.py`: load
  `configs/hooks.yaml`.
- [ ] **U106** [P] Wire ingress hooks: `on_tool_result`,
  `on_resource_read`, `on_inbox_read`, `on_fs_read`, `on_web_fetch`,
  `on_memory_read`, `on_mcp_incoming`, `on_label_propagation`.
- [ ] **U107** [P] Wire policy-boundary hooks: `pre_chokepoint`,
  `at_chokepoint.decision`, `pre_dispatch`, `pre_approval_queue`.
- [ ] **U108** [P] Wire egress hooks: `pre_email_send`,
  `pre_purchase`, `pre_mcp_outgoing`, `pre_fs_write`,
  `pre_calendar_write`.
- [ ] **U109** [P] Wire session-lifecycle hooks: `on_session_spawn`,
  `on_session_fork`, `on_session_terminal`.
- [ ] **U110** [P] Wire storage hooks: `on_memory_write`,
  `on_audit_emit`.
- [ ] **U111** [P] Wire LLM-boundary hooks: `pre_llm_call.*`,
  `post_llm_call.*`.

### Audit events

- [ ] **U112** Extend `audit/events.py`:
  `LABEL_RAISED_BY_INSPECTOR`, `DECISION_RELAXED`,
  `DECISION_TIGHTENED`, `DECLASSIFICATION_APPLIED`,
  `DECLASSIFICATION_REFUSED_BY_FLOOR`, `HOOK_FIRED`.

### Configuration loaders

- [ ] **U113** YAML DSL parser for inspector rules → callable
  `RaiseOnlyInspector`.
- [ ] **U114** YAML DSL parser for decision rules → callable
  `DecisionInspector`.
- [ ] **U115** YAML DSL parser for declassifier rules → callable
  `DeclassifyingTransformer`.
- [ ] **U116** Python module loader for `configs/inspectors/`,
  `configs/decision_inspectors/`, `configs/declassifiers/`,
  `configs/upstream_policies/`.

### Composition + floors

- [ ] **U117** Update `engine.decide()` to apply DecisionInspectors
  after standard policy; compose with envelope; respect hard floors.
- [ ] **U118** Declassification floors:
  `configs/declassification_floors.yaml` + enforcement.
- [ ] **U119** Shadow mode: per-primitive `shadow: true` flag.

### Per-arg payload labels

- [ ] **U120** Extend `ToolDefinition` with
  `payload_args: tuple[str, ...]`.
- [ ] **U121** Update `engine.decide()` to inspect per-arg labels;
  apply Brewer-Nash + hard-refuse logic per-arg.
- [ ] **U122** [P] Update native tools (email.send, purchase.queue,
  fs.write, etc.) to declare their `payload_args`.
- [ ] **U123** [P] Tests `tests/test_payload_args.py`.

**P0 sum: ~15 days.**

## Phase P1 — MCP surface integration

Depends on P0.

### Trust tier + heuristic

- [ ] **U130** Per-server `trust_tier` config.
- [ ] **U131** Heuristic-disagreement detection + refusal/warning
  + audit events.
- [ ] **U132** `io.joneslaw/capabilitydeputy/*` annotation namespace
  honoring per trust tier.

### MCP surfaces

- [ ] **U133** `resources/list` + `resources/read` adapter wiring
  with ingress-hook firing.
- [ ] **U134** `prompts/list` + `prompts/get` with `safe_to_forward`
  auto-forward path.
- [ ] **U135** Sampling support: declare capability; route to
  configurable LLM; per-server `sampling.tools` enable with
  exposed-tool-subset config.
- [ ] **U136** [P] Elicitation form mode + approval queue
  integration (new `ApprovalAction.ELICITATION_RESPOND`).
- [ ] **U137** [P] Elicitation URL mode (depends on P3 OAuth).
- [ ] **U138** [P] Per-server scoped `roots/list` projected from
  BindingSet.

### Notifications

- [ ] **U140** `notifications/resources/updated` proxy handler:
  synthesize `resources/read`; run through chokepoint; route per
  per-server subscription policy.
- [ ] **U141** Per-server `subscriptions:` config block.
- [ ] **U142** [P] `*/list_changed` handlers (mark stale).
- [ ] **U143** [P] Operational notifications (`progress`,
  `cancelled`, `message`).
- [ ] **U144** Per-server rate limit on incoming notifications.
- [ ] **U145** Bundle-within-window collector for pushes.

### Per-server policy modules

- [ ] **U146** ServerPolicy Protocol + loader.
- [ ] **U147** Adapter mapping resolution order (per-server module
  → YAML override → annotations → heuristic).

**P1 sum: ~15 days** (excluding U137 + U150-U152 which depend on P3).

## Phase P2 — Observability + ecosystem

### Observability

- [ ] **U160** [P] CLI `capdep policy review` (declassification
  history).
- [ ] **U161** [P] CLI `capdep policy decisions` (relax/tighten
  history).
- [ ] **U162** [P] TUI panel: live audit stream.
- [ ] **U163** Replay harness for primitives.
- [ ] **U164** Fixture sessions for primitive unit tests.
- [ ] **U165** [P] Operator-facing YAML DSL documentation.
- [ ] **U166** [P] Example operator-curated primitive modules.

### OSCAL compliance emission

For operators in regulated contexts who need to demonstrate
compliance against NIST 800-53, FedRAMP, SOC 2, ISO 27001, etc.
CapableDeputy doesn't need to change its policy engine for this —
it emits OSCAL-shaped documents derived from existing audit data +
operator config.

Reference: NIST OSCAL (Open Security Controls Assessment Language)
— https://pages.nist.gov/OSCAL/. Schema spec: JSON/XML/YAML formats
for control catalogs, baselines, system security plans (SSP),
assessment plans, and assessment results.

- [ ] **U200** Research: walk the relevant control catalogs (NIST
  800-53 Rev 5 baseline; FedRAMP Moderate; SOC 2 Trust Services
  Criteria; ISO 27001 Annex A) and map each control to the
  CapableDeputy mechanism that implements it. Output: a YAML
  mapping file `compliance/control_implementations.yaml`.

  Examples of the mappings:
  - `AC-3 Access Enforcement` → capability + chokepoint mechanism
    (`engine.decide()` + `policy/capabilities.py`)
  - `AC-4 Information Flow Enforcement` → label propagation,
    Brewer-Nash rules, BLP clearance
  - `AC-6 Least Privilege` → capability granularity + delegation
    attenuation
  - `AU-2 Event Logging` → audit event emission
  - `AU-3 Content of Audit Records` → audit event schema
  - `AU-9 Protection of Audit Information` → append-only audit log
  - `CM-7 Least Functionality` → tool registry + per-tool
    capability bindings
  - `IA-2 Identification and Authentication` → clearance profile
    + initiator field
  - `SC-2 Application Partitioning` → session isolation +
    Pattern ② DUAL_LLM
  - `SC-4 Information in Shared System Resources` → label
    propagation across session fork
  - `SC-8 Transmission Confidentiality` → egress chokepoint +
    per-arg payload labels
  - `SC-28 Protection of Information at Rest` → labeled storage
    + memory store
  - `SI-4 System Monitoring` → audit stream + inspectors

- [ ] **U201** Create `src/capabledeputy/compliance/oscal_emitter.py`
  with OSCAL output functions:
  - `emit_ssp(output_path)` — System Security Plan describing
    the CapableDeputy installation
  - `emit_control_implementations(output_path)` — control
    implementation statements with pointer-to-evidence
  - `emit_assessment_results(audit_log_path, output_path,
    since_date=None)` — extract audit events that demonstrate
    control implementation; emit as OSCAL assessment results

- [ ] **U202** [P] OSCAL JSON schema validation:
  use the official NIST-provided schemas
  (https://github.com/usnistgov/OSCAL) to validate emitted
  documents.

- [ ] **U203** CLI: `capdep audit oscal --output-dir <dir>
  [--since-date <iso>] [--catalog nist-800-53-r5 | fedramp-moderate
  | soc2 | iso-27001-a]` — emit the OSCAL bundle.

- [ ] **U204** [P] Tests `tests/test_oscal_emission.py`:
  - Schema validation on each emitted document
  - Round-trip: emit, load, verify control mappings present
  - Audit-event extraction correctness (events for AU-2 cover the
    right time window, etc.)

- [ ] **U205** [P] Operator documentation: `docs/compliance.md`
  covering which frameworks are supported, how to generate the
  bundle, how to consume it in compliance tools (e.g., GovReady-Q,
  Compliance Trestle).

- [ ] **U206** Continuous-assessment mode: a daemon background
  task that emits a fresh OSCAL assessment-results document on a
  schedule (daily / weekly), suitable for ingestion into a
  compliance dashboard. Output dir is operator-configured.

**P2 sum: ~14 days** (7 observability + 7 OSCAL).

### Starlark host for sandboxed primitive authoring

The current design has YAML DSL (declarative) + Python module
(operator-trusted). After the language survey in
`programmatic-policy-primitives.md` §14, Starlark is the right
host for the middle tier — Python-like syntax, hermetic +
deterministic + bounded (no while loops, no recursion, no classes),
designed for parallelizable config-language use.

- [ ] **U210** Embed Starlark interpreter (evaluate `starlark-go`
  via FFI vs. native Python implementation). Decision: `starlark-go`
  is the reference; via subprocess or FFI.
- [ ] **U211** Wire Starlark hooks to the three primitive Protocols.
  Operator can author `.star` files at the same paths as Python
  modules; loader resolves by extension.
- [ ] **U212** Per-call resource limits (instruction count, memory
  cap) via Starlark's built-in step counter.
- [ ] **U213** Configuration loader for `*.star` files alongside
  the Python module loader.
- [ ] **U214** [P] Test harness + fixtures for Starlark primitives.
- [ ] **U215** [P] Operator documentation: writing CapDep
  primitives in Starlark.

**Starlark host sum: ~15 days.**

## Phase P3 — Streamable HTTP + OAuth (was implied in original)

- [ ] **U170** `transport_http.py` Streamable HTTP per MCP spec.
- [ ] **U171** Origin validation; localhost binding.
- [ ] **U172** Session ID handling.
- [ ] **U173** SSE stream resumability.
- [ ] **U174** OAuth 2.1 client.

### OAuth flow-pattern-session model

- [ ] **U150** Token store per `(server_id, purpose_handle, initiator)`;
  escrow on session-end.
- [ ] **U151** Token lifecycle: issue/refresh/discard/audit.
- [ ] **U152** Operator UI for reuse-from-escrow vs. re-authorize.

**P3 sum: ~14 days.**

## Phase P4 — Optional: community + advanced

- [ ] **U180** Notion policy module.
- [ ] **U181** GitHub policy module.
- [ ] **U182** Slack policy module.
- [ ] **U183** Google Workspace policy module.
- [ ] **U185** Per-tool-kind hook routing optimization.
- [ ] **U186** Memoization layer.
- [ ] **U187** [P] DSL extensions: time-window matching, regex
  extraction in declassifiers.

**P4 sum: ~10 days.**

## Dependency graph (P0 → P4)

```
P0 (primitives + hooks + audit) ──┐
                                  ├─→ P1 (MCP surfaces; uses hooks)
                                  ├─→ P2 (observability; uses audit)
                                  │
P3 (HTTP + OAuth) ────────────────┴─→ P1.elicitation-URL,
                                       P1.K (OAuth-dependent surfaces)

P4 builds on P1, P2, P3.
```

## Acceptance criteria (extended)

In addition to the original Phase 1-7 criteria:

- All P0 primitives unit-tested in `tests/test_primitives_*.py`
- All hooks have integration tests with fixture sessions
- All P1 MCP surfaces tested against a stub MCP server
- Shadow mode validated end-to-end
- Declassification floors validated
- Audit replay deterministic
- Per-arg payload-label gating validated

## Risk register (extended)

- **R10: hook-list scope creep.** Hard-cap at the 25 hooks in
  `programmatic-policy-primitives.md` §5.
- **R11: composition order ambiguity.** Document resolution order
  in chokepoint code; ensure operator-declared order respected.
- **R12: shadow mode misuse.** Document that production-critical
  primitives should not stay in shadow indefinitely.
- **R13: declassifier abuse.** Mitigated by floors + audit + diff
  review + shadow-mode onboarding.
- **R14: per-arg labels under-declared.** Operators must explicitly
  declare `payload_args`. Default empty = no per-arg gating unless
  declared.

## Out of scope (deferred)

- CapableDeputy AS an MCP server (spec-008 — deferred per operator
  decision)
- Operator-defined custom hooks (use predefined list; spec new
  hooks if needed)
- Cross-machine session federation
- Server-side policy in MCP (would require MCP protocol extension)
