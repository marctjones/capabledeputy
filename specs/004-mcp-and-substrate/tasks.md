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

## Phase 8: Polish + DefenseClaw integration

- [ ] **U058** [P] **DefenseClaw plugin (CD as deterministic policy oracle)**: `src/capabledeputy/integrations/defenseclaw_plugin/` — a thin wrapper that exposes CD's `engine.decide()` as a DefenseClaw policy backend, replacing DefenseClaw's regex+optional-LLM-judge path with CD's deterministic engine. See `defenseclaw-integration.md` for the architectural assessment.
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
- Phase 4 substrate providers (U034, U035, U036) all parallel.
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
