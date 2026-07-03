# Implementation plan & milestones

Living plan that organizes the open GitHub issues into sequenced milestones
with dependencies. Authoritative status is GitHub; this doc is the *sequencing
rationale*. Last refreshed 2026-07-03 — active work is **v0.42 local
media/model operations reliability** after the v0.41.1 stable patch release,
with **v0.43 CommonMark rendering across client surfaces** open as the next
planned product milestone. The live GitHub tracker has no unmilestoned open
issues, and every GitHub milestone title now carries an ordered prefix.

Active milestone (GitHub): **12 Product — v0.42.0 — Local media and model
operations reliability**.

Next planned milestone (GitHub): **13 Product — v0.43.0 — CommonMark rendering
across client surfaces**.

Recently completed product milestones: **11 Product — v0.41.0 — CapDepMac
reliability and safe scripting UX** · **06 Product — v0.40.0 — Safe practical
scripting assistant** · **05 Product — v0.39.0 — Background automation and
onguard UX** · **04 Product — v0.38.0 — Memory, retention, compaction, and
async media reliability** · **03 Product — v0.37.0 — Execution substrate,
isolation, and compliance evidence** · **02 Product — v0.36.0 — MCP admission,
provider mappings, and workflow templates** · **01 Product — v0.35.0 —
Desktop context, SourcePorts, and visual review**.
Recently closed:
**00.11 Done — v0.34.0 — First-run, connectors, and rich chat readiness** ·
**00.10 Done — v0.33.0 — Streaming turn lifecycle and liveness** ·
**00.09 Done — v0.32.0 — Interactive workstream coordination** ·
**00.08 Done — v0.31.0 — Multi-session security context observability** ·
**00.07 Done — v0.30.0 — Client integration test parity** · **00.06 Done —
v0.29.0 — MCP security conformance and external server labeling** ·
**00.05 Done — v0.28.0 — Onguard clients and daemon coordination** ·
**00.04 Done — v0.27.0 — Practical setup and daemon-owned settings** ·
**00.03 Done — v0.26.0 — Client parity over daemon RPC** · **00.02 Done —
v0.25.0 — MCP compatibility and security integration** · **00.01 Done —
v0.17 — Gap hardening and explainability**.

`ROADMAP.md` is the canonical product roadmap. This file explains sequencing,
dependencies, and why the next pull should focus on one milestone over another.

Themes currently driving priority:
1. **v0.42 local media/model operations reliability** — profile selection,
   model/account readiness, benchmark-informed defaults, real progress/status,
   cancellation/recovery, and setup/release docs (#202-#208).
2. **v0.43 CommonMark client rendering** — shared CommonMark contract,
   parser/sanitizer fixtures, CapDepMac rich rendering, terminal-safe CLI/TUI
   rendering, MCP-control fallback behavior, and release parity evidence
   (#209-#215).
3. **MCP security integration** — MCP must remain an integration substrate, not
   a second authority path. v0.29 turns the current targeted tests into a
   security conformance suite before CapDep relies heavily on external MCP
   servers and headless clients.
4. **Client proof, not just parity claims** — v0.30 replaces source/manifest
   checks with live daemon integration coverage for CLI, TUI, Swift GUI, and
   MCP-control.
   Coverage is ratcheted independently for daemon files, clients, MCP
   surfaces, bundled MCP servers, and tools; the near-term target is 85% per
   group and the stretch target is 90%, but CI first enforces non-regression
   from the checked-in baseline.
5. **Multi-session explainability** — v0.31 made labels, flow patterns,
   external actors, approvals, policy rules, provenance, and audit inspectable
   across turns and clients.
6. **Source identity and labeling correctness** — IFC guarantees ride on
   correct labels. The old v0.16 track is now narrowed to canonical source
   identity and per-message email labeling.
7. **Terminal UX and approval polish** — remaining terminal work is useful,
   but it is not the primary desktop-agent path and should not duplicate daemon
   authority.
8. **Research/non-goals** — keep remote/mobile control, always-on autonomy,
   community sharing, and web/cross-platform alternatives explicit without
   promoting them to immediate implementation.
9. **Decision fatigue** — coarse policy leads to rubber-stamping and eroded
   human oversight. The decision-refinement layer is live; future work should
   add concrete inspectors or policy scripts, not revive the old epic.

The policy themes come from `docs/security-alignment-assessment.md`:

---

## Recently shipped (this cycle)

| # | What | Milestone |
|---|---|---|
| #137/#136/#134/#133/#138 | Daemon-enforced workstream ownership, daemon state views, client parity, MCP-control workstream tools, and multi-client tests | v0.32.0 |
| #31/#32/#22/#13 | Daemon-owned turn lifecycle, heartbeat/disconnect cancellation, CLI Rich Live turn streaming, and stdio upstream no-broad-env hardening | v0.33.0 |
| #140-#145/#182/#183 | First-run setup plan/check/status/actions, OAuth recovery, morning-briefing smoke, peer onboarding research, rich chat media, and local model routing | v0.34.0 |
| #11/#54 | Quarantined forwardable/public-facts schemas and purpose-contamination residual audit | v0.17 |
| #28 | Shared CLI semantic style palette | v0.5 |
| #113-#132 | New tracker plan for MCP conformance, client integration test parity, and multi-session security context observability | v0.29.0-v0.31.0 |
| #77-#83 | MCP compatibility/security milestone: output schemas, daemon approval elicitation, upstream resource label propagation, admin separation, and ARD scope docs | v0.25.0 |
| #84/#85/#88-#91 | Client parity over daemon RPC: CLI/TUI/Swift/MCP-control parity contract, tests, and surface implementations | v0.26.0 |
| #72 | Gmail MCP OAuth setup slice: daemon-owned client storage, generated server config, browser OAuth RPC, CapDepMac Accounts UI | v0.24.0 |
| — | Native CapDepMac shell, daemon supervision, single-instance guard, local launcher hardening | v0.24.0 |
| — | Daemon idle shutdown and durable daemon memory store | v0.24.0 |
| #2 | Agent-loop cap-fire auditability + thrash guard | — (P0 bug) |
| #50 | Catalog-aware tier resolution | v0.16 / #42 |
| #52 | Restricted-tier Pattern ③/⑤ floor in per-turn select_mode | v0.17 / #43 |
| #53 | `capdep policy models` — loud Biba gap | v0.17 / #43 |
| #46 | DecisionInspector / Starlark loader (layer is now LIVE) | v0.16 / #41 |
| #48 | Read-only session-history summary → inspectors (frequency policy) | v0.16 / #41 |
| #49 | `capdep why` — explain the rule/floor/inspector that fired | v0.17 / #43 |
| #47 | Starlark starter library (4 scripts + 2 builtins; 2 scripts blocked) | v0.16 / #41 |
| #5 | Dynamic filesystem labeling | v0.16 / #42 |
| #33 | Design: Workspace capability mapping | v0.16 / #42 |
| #34 | Email labeling — design + content-rule impl (raise-only labeler) | v0.16 / #42 |
| #13 | Credential vault and stdio upstream no-broad-env hardening | v0.33.0 |

## v0.42.0 — Local media and model operations reliability

Open on 2026-07-03. v0.41 made CapDepMac and safe scripting resilient enough
for normal use; v0.42 focuses that reliability lens on local media/model work,
especially image generation on Apple Silicon.

### Scope

| Issue | Work | Status |
|---|---|---|
| #202 | EPIC: local media and model operations reliability | Open |
| #206 | Daemon image profile selection and persisted defaults | Open |
| #207 | Model and account readiness checks for local media backends | Open |
| #203 | Benchmark-informed image generation defaults | Open |
| #204 | Live progress and status for long-running local model work | Open |
| #205 | Cancellation, queue recovery, and failure handling for image jobs | Open |
| #208 | Local media/model setup documentation and release tests | Open |

### Sequencing

1. Add daemon-owned image profile metadata, selection, validation, and
   persisted defaults (#206).
2. Add daemon readiness checks for local models, account gates, backend imports,
   image venv state, and local checkpoint paths (#207).
3. Convert benchmark results into documented fast/balanced/high-quality defaults
   and keep slow profiles explicit (#203).
4. Emit real queued/loading/running/finalizing/completed/failed/canceled states
   for long-running local model work (#204).
5. Harden cancellation, timeouts, lock release, backend crash recovery, and
   retry-safe failure paths (#205).
6. Close with setup docs and a release gate that includes uv lock/sync, daemon
   readiness checks, focused Python tests, and CapDepMac Swift tests (#208).

### Done-when

- Local image/model work is configurable through daemon-owned profile settings,
  not hidden environment tweaks.
- Readiness failures are actionable in CLI and CapDepMac without clients reading
  secrets or duplicating daemon authority.
- Long-running jobs show real daemon/model-runner state and can be canceled or
  recovered without corrupting chat history.
- Documentation and release tests cover the supported Apple Silicon MLX/MFLUX
  path and clearly state platform boundaries.

## v0.43.0 — CommonMark rendering across client surfaces

Planned after v0.42. CapDep responses increasingly include structured prose,
links, code, media references, and tables. v0.43 makes CommonMark a deliberate
client contract instead of an accidental formatting side effect, while still
respecting each interface's natural rendering limits.

### Scope

| Issue | Work | Status |
|---|---|---|
| #210 | EPIC: CommonMark rendering across client interfaces | Open |
| #209 | Define CommonMark client capability matrix and rendering contract | Open |
| #211 | Add shared CommonMark parser, sanitizer, and fixture corpus | Open |
| #212 | Implement CapDepMac CommonMark rendering and regression tests | Open |
| #213 | Implement terminal-safe CommonMark rendering for CLI and TUI clients | Open |
| #214 | Handle CommonMark in MCP-control and lower-capability client surfaces | Open |
| #215 | Document and smoke-test CommonMark parity across clients | Open |

### Sequencing

1. Define the CommonMark capability matrix and safety contract first (#209).
2. Add shared parser/sanitizer behavior and fixture corpus so client tests use
   the same inputs (#211).
3. Implement CapDepMac rich rendering with async chat/history/media regression
   coverage (#212).
4. Implement terminal-safe CLI/TUI rendering with deterministic plain fallback
   for scripts and logs (#213).
5. Preserve CommonMark/structured content through MCP-control and lower-
   capability clients without unsafe rendering assumptions (#214).
6. Close with README/roadmap docs and release smoke coverage across client
   surfaces (#215).

### Done-when

- Each client declares which CommonMark features it renders, preserves, or
  degrades.
- Raw HTML, unsafe links, terminal escape sequences, huge code blocks, tables,
  and image references have tested fallback behavior.
- CapDepMac, CLI/TUI, and MCP-control pass the shared fixture corpus at their
  supported capability levels.
- Release documentation accurately describes supported CommonMark behavior and
  known limits.

**EPIC #41 is closed** (layer live, frequency policy, `capdep why`).
**EPIC #42 is closed** with source identity and labeling correctness captured
in `docs/support-track-closeout-2026-07-01.md`.
**v0.17 is closed/reorganized**: its concrete hardening issues are done or
moved to the milestone where implementation belongs.

---

## v0.34.0 — First-run, connectors, and rich chat readiness

Closed locally on 2026-07-01. v0.33 made long-running turns observable and
cancellable; v0.34 makes a first install usable and makes the primary chat
surface useful enough to validate setup under real policy.

### Scope

| Issue | Work | Status |
|---|---|---|
| #140 | EPIC: first-run, connector, rich chat, and local-model readiness | **Done** |
| #141 | Research peer onboarding flows | **Done** |
| #142 | `setup.plan` / `setup.check` RPCs | **Done** |
| #143 | OAuth recovery tests + `connector.status` actions | **Done** |
| #144 | Morning-briefing workflow smoke | **Done** |
| #145 | Clients consume daemon setup plan | **Done** |
| #182 | Rich chat media tools: generated/fetched images, Wikipedia lead images, chart artifacts | **Done** |
| #183 | Local model routing and CapDepMac model-mode controls | **Done** |

### Sequencing

1. ~~Add daemon setup/check RPCs~~ (**done** — `daemon/setup_plan.py`).
2. ~~OAuth recovery descriptors~~ (**done** — `test_setup_oauth_recovery.py`).
3. ~~Morning-briefing smoke~~ (**done locally** —
   `test_setup_morning_briefing_smoke.py`).
4. ~~Review and close the rich media/model-routing local work~~ (#182, #183).
5. ~~Close #145 client setup parity gaps and write #141 onboarding note~~
   (`docs/first-run-onboarding-research.md`).
6. Close the GitHub milestone when done-when criteria pass and GitHub issues
   match shipped evidence.

### Done-when

- All clients render the same daemon setup plan instead of duplicating
  readiness logic.
- OAuth/connector failures are classified with actionable recovery steps.
- A new user can move from setup to a useful safe workflow without editing YAML
  directly for the common macOS + Google Workspace case.
- Rich media artifacts and local model routing work through daemon events,
  policy capabilities, and CapDepMac rendering safeguards.
- Setup helpers remain daemon-mediated and do not bypass labels, policy,
  approvals, provenance, or audit.

## v0.35.0 — Desktop context SourcePorts and visual review

**Starts after:** v0.34 close-out. Once setup and chat are usable, the next
desktop-agent table-stakes gap is safe context capture and exact visual review.

### Scope

| Issue | Work | Status |
|---|---|---|
| #146 | EPIC: Desktop context SourcePorts and signed visual review | **Done locally** |
| #147 | Research desktop agent UX for context capture, review cards, and approval fatigue | **Done locally** |
| #148 | Active-context SourcePort contract with labels and canonical IDs | **Done locally** |
| #149 | Browser current-page SourcePort with untrusted-content labeling | **Done locally** |
| #150 | macOS app SourcePorts for Mail, Finder, Pages, Numbers, Keynote, and Calendar | **Done locally** |
| #151 | Typed artifact model for drafts, diffs, calendar mutations, document patches, and research memos | **Done locally** |
| #152 | Signed approval payloads bind exact artifact hash and destination | **Done locally** |

### Sequencing

1. ~~Research UX and approval-fatigue patterns~~ (#147 done locally).
2. ~~Define SourcePort contract first~~ (#148 done locally).
3. ~~Add browser SourcePort~~ (#149 done locally).
4. ~~Finish app-specific capture clients for Mail/Finder/iWork/Calendar~~ (#150 done locally).
5. ~~Add typed artifact model~~ (#151 done locally).
6. ~~Bind signed approvals to artifact hash/destination~~ (#152 done locally).
7. ~~Add Swift visual review cards and close tracker/docs after full validation~~ (done locally).

### Done-when

Active desktop/browser context enters through labeled SourcePorts, proposed
changes are typed artifacts, and approval cards bind the exact artifact and
destination being approved.

## v0.36.0 — MCP admission, provider mappings, and workflow templates

**Starts after:** v0.35 provides typed artifacts and review surfaces for safer
operator review of new tools/templates.

### Scope

| Issue | Work | Status |
|---|---|---|
| #153 | EPIC: MCP adapter, extension admission, and bounded workflow templates | **Done locally** |
| #154 | Research safe extension managers and skill/template systems in peer agents | **Done locally** |
| #155 | Daemon MCP extension admission workflow with classify/test/approve/disable | **Done locally** |
| #156 | Workflow template manifest schema with capabilities, labels, flow pattern, and approval policy | **Done locally** |
| #157 | Starter workflow templates for briefing, inbox triage, meeting prep, and research memo | **Done locally** |
| #158 | Client workflow-template review and launch surfaces | **Done locally** |
| #159 | MCP extension and workflow-template conformance tests | **Done locally** |
| #184 | Generic MCP adapter polish and fail-closed mapping audit | **Done locally** |
| #185 | Tier-1 MCP mappings: GitHub, Google Workspace, Microsoft 365, and Notion | **Done locally** |
| #186 | HTTP MCP OAuth flow-pattern sessions and credential mediation | **Done locally** |

### Sequencing

1. ~~Research safe extension/template systems~~ (#154 done locally).
2. ~~Define workflow template manifest schema~~ (#156 done locally).
3. ~~Continue generic MCP adapter mapping audit from admission preview to
   persisted admission state~~ (#184, #155 done locally).
4. ~~Add tier-1 mappings and fixture tests~~ (#185 done locally).
5. ~~Add HTTP OAuth credential mediation~~ (#186 done locally).
6. ~~Complete starter templates, including meeting prep and research memo
   variants~~ (#157 done locally).
7. ~~Add client review/launch surfaces and conformance tests~~ (#158, #159 done locally).

### Done-when

New upstream MCP tools are admitted only through daemon-owned classification,
mapping, tests, and approval; unmapped tools fail closed; clients can review
and launch workflow templates without gaining setup or policy authority.

## v0.37.0 — Execution substrate, isolation, and compliance evidence

**Starts after:** v0.36 MCP/template admission. This milestone is the
spec-004 production-substrate work that was previously floating between local
roadmap text and backlog.

### Scope

| Issue | Work |
|---|---|
| #44 | EPIC: Substrate isolation, execution, and compliance replay — implemented |
| #9 | Run upstream MCP servers inside Podman by default — implemented |
| #14 | Per-upstream network egress allowlist for stdio upstreams — implemented |
| #55 | Cross-host RemoteApprovalEnvelope structured four-axis wire format — implemented |
| #56 | More VersionedWritePort backends — implemented |
| #57 | Modal + Firecracker SandboxActuators for heavier isolation providers — implemented |
| #187 | `EXECUTE.sandbox` `code.execute` native tool over Podman `SandboxActuator` — implemented |
| #188 | OTLP exporter, OSCAL assessment plan, and compliance audit-replay pipeline — implemented |
| #189 | Meta-director and ToxicSkills regression scenarios for MCP/substrate safety — implemented |

### Sequencing

1. Wire Podman upstream isolation and allowed-host enforcement (#9, #14).
2. Expose native sandboxed execution over the existing actuator port (#187).
3. Add versioned writes and cross-host approval wire format (#56, #55).
4. Add OTLP/OSCAL/audit-replay evidence pipeline (#188).
5. Add heavier sandbox providers and adversarial scenario coverage (#57, #189).

### Done-when

Operators can enable sandboxed execution, upstream isolation, versioned writes,
compliance replay, and heavier isolation providers without a second policy
authority path.

### Completion evidence

- `upstream_isolation_defaults` covers inherited Podman stdio isolation; bridge
  mode requires `allowed_hosts` and emits DNS-disabled host pinning.
- `code.execute` uses the same `EXECUTE.sandbox` capability and
  `SandboxActuator` lifecycle/audit path as `sandbox.run`.
- S3 Object Lock and Google Drive revisions are registered
  `VersionedWritePort` providers.
- `RemoteApprovalEnvelope` signs the v1 schema, destination, structured label
  state, Axis C effect class, Axis D decision context, and nonce.
- Compliance commands emit OSCAL assessment plans, OTLP trace JSON, and audit
  replay reports.
- Modal and Firecracker command-runner actuators extend the substrate port.
- Meta-director and ToxicSkills tests verify malicious MCP descriptions/metadata
  cannot bypass strict classification or operator capability overrides.

## v0.38.0 — Memory, retention, and compaction

**Scope:** #160-#165. Starts after the substrate baseline because retained
memory and compaction artifacts need stable labeled artifact/audit behavior.

## v0.39.0 — Background automation and onguard UX

**Scope:** #166-#171. Background clients remain normal daemon clients and
surface notifications, queued approvals, summaries, and result handoff without
privileged sidecar authority.

## v0.40.0 — Safe practical scripting assistant

**Scope:** #172-#177. Safe practical scripting workflows for non-programmers
produce daemon-governed plans, script artifacts, sandbox evidence, and exact
export approvals rather than a generic coding-agent clone.

## v0.33.0 — Streaming turn lifecycle and liveness

This milestone is complete. v0.32 proved daemon-owned workstream ownership,
and v0.33 added the real turn lifecycle and event stream required for
disconnect/heartbeat cancellation and Rich Live streaming.

### Completed scope

- **#31** cancel in-flight turn when a client surface disconnects.
- **#32** heartbeat/liveness cancellation when a connected client stops
  responding.
- **#22** inline streaming agent output via Rich Live, backed by daemon turn
  events rather than client-local streaming hacks.
- **#13** residual credential-vault hardening: stdio upstreams no longer
  inherit the daemon's broad process environment; they receive only the
  supervisor allowlist plus explicit per-server/vault env.

### Completion notes

- A client can disconnect or stop heartbeating during an active turn and the
  daemon cancels/retires that turn without relying on client honesty.
- Multiple clients can observe the same streamed turn without gaining control.
- A reconnecting owner can resume from a stream cursor where policy permits.
- Rich Live output is a renderer over daemon events, not a separate execution
  path.
- Stdio upstream servers are still long-lived processes, so secrets explicitly
  granted to a server are spawn-time for that server. The completed hardening
  prevents unrelated daemon environment secrets from being inherited; true
  per-dispatch stdio secrets require per-call isolation or a server-specific
  auth channel.

## 07 Support — Source identity and labeling correctness

The old v0.16 milestone has been redesigned around the remaining correctness
gap: accurate labels at source boundaries. The decision-refinement/Starlark
layer is already live, so new work here should not be generic policy plumbing.

### Scope

- **#42** EPIC: Strengthen the labeling oracle.
- **#51** Gmail / Drive / Calendar SourcePort canonical-id providers.
- **#139** Email labeler implementation: rule file plus per-message hook.

### Done-when

- Google Workspace objects have stable source identities for label decisions
  and provenance.
- Email message labels can be raised by configurable rules at ingestion or
  per-message fetch time.
- Tests prove labels fail closed when source identity or message metadata is
  ambiguous.

---

## 08 Support — Terminal UX and approval polish

The old v0.5 milestone is now terminal-client quality work. It should improve
practical terminal use without duplicating daemon safety enforcement. Streaming
moved to v0.33 because the correct implementation depends on daemon turn
events.

### Scope

- **#16** REPL feature parity with Claude Code: markdown, multiline input, and
  expandable tool detail; streaming depends on #22/v0.33.
- **#17** split-pane / tabbed viewer for significant content alongside chat.
- **#19** inline graphics via sixel / kitty graphics protocol.
- **#27** Inline approval as a non-blocking banner that does not steal focus.
- **#29** Unicode width safety in the bottom toolbar plus 80x24 minimum-size
  behavior.

### Done-when

- The terminal remains usable during pending approvals and long-running turns.
- Width-sensitive chrome is tested against narrow terminals and wide glyphs.
- UX polish does not become a second policy or approval implementation.

---

## 09 Research — Non-goals and safe alternatives

These issues keep product-pressure topics visible without committing CapDep to
unsafe or strategically wrong directions.

- **#178** alternatives to remote/mobile daemon control without opening network
  listeners.
- **#179** alternatives to broad always-on autonomous action modes.
- **#180** safe community template/extension sharing without marketplace trust
  collapse.
- **#181** web UI or cross-platform GUI alternatives without duplicating daemon
  functionality.

---

## 10 Backlog — Formal models and deferred breadth

Backlog remains valid but explicitly lower priority than the v0.35-v0.40
product ladder and source-identity/labeling track:

- **Formal-model completeness:** #45, #58, #59.
- Any remaining provider or federation breadth not explicitly pulled into
  v0.36/v0.37.

---

## v0.25.0 — MCP compatibility + security integration

MCP is now treated as a daemon-mediated integration substrate. The milestone
locks down the session-bound MCP surface, upstream MCP adapter, admin MCP
surface, and ARD scope.

### Scope

- **#77** compatibility matrix and protocol tests.
- **#78** `outputSchema` propagation.
- **#79** elicitation through existing daemon approval objects.
- **#80** upstream resource label propagation.
- **#81** explicit posture for tools, resources, prompts, elicitation,
  sampling, roots, and notifications.
- **#82** admin MCP separation metadata and tests.
- **#83** refreshed MCP/ARD docs.

### Done-when

- MCP action execution cannot bypass daemon policy.
- Admin setup authority is not exposed through session-bound MCP.
- ARD remains discovery/configuration input only.
- Focused MCP and approval tests pass.

---

## v0.27.0 — Practical setup + daemon-owned settings

This practical-product milestone is active. It does not replace the
policy/labeling roadmap; it pulls forward the setup work required to make the
macOS + Google Workspace assistant usable without CLI/YAML handholding.

### Scope

- **#69** daemon-owned settings store and settings RPCs for CapDepMac — done.
- **#75** daemon config validation and log-location RPCs for Advanced settings
  — done.
- **#70** wire CapDepMac settings controls to daemon settings instead of
  constants — done.
- **#71** replace empty Setup/Open/Fix buttons with daemon remediation actions
  — done with `setup.run_action` and action descriptors.
- **#73** source bindings and labeling editor — done through
  daemon-owned `source_binding.*` RPCs and CapDepMac Trust UI.
- **#76** fix task/menu actions that navigate without completing the intended
  action — done for approval focus, config validation,
  automation pause/resume, and screen-control requests.
- **#72** daemon-backed account and OAuth setup workflows for Google and local
  app connectors — done: Gmail, Calendar, and Drive OAuth have first-class
  daemon status/configure/login/revoke flows across clients; local Apple app
  rows remain user-mediated permission checks by design.
- **#74** automation pause, screen-control enablement, and Touch ID policy —
  done: pause/resume and screen-control requests are
  daemon-visible and audited; high-risk approvals require a daemon-recognized
  strong-auth marker when Touch ID policy is enabled, with CapDepMac performing
  the local device authentication challenge.
- **Onguard architecture**: adopt headless normal clients for background work,
  with daemon-owned schedule/config/queue/event contracts instead of embedding
  every workflow in daemon core.

### Sequencing

1. Continue v0.28 by adding the first useful packaged onguard clients and
   violation demos.
2. Extend client parity for the shared coordination contracts required by
   onguard clients:
   `schedule.*`, `client.config.*`, `client.queue.*`, `client.events.*`, and
   structured origin metadata for policy/Starlark.

### Done-when

- A user can configure Google Workspace OAuth from CapDepMac without editing
  YAML for the supported MCP servers.
- The same daemon API can support Calendar, Drive, GitHub, Kagi/custom HTTP MCP,
  and local app permission checks.
- CapDepMac has no visible no-op buttons for setup/action flows.
- Configuration failures surface daemon validation messages and log locations.
- Tests cover missing credentials, connected status, reauth-needed status, and
  daemon restart persistence.
- Background workflows such as the daily newspaper run as ordinary onguard
  clients and cannot bypass daemon policy, labels, approvals, provenance, or
  audit.

---

## v0.28.0 — Onguard clients + daemon coordination

This milestone turns the documented onguard-client architecture into an
implementation substrate. Onguard clients are ordinary headless daemon clients:
they orchestrate background work, but the daemon owns identity, origin,
schedules, queues, shared config, events/results, labels, provenance, audit,
approval, and tool dispatch.

### Scope

- **#92** EPIC: Onguard clients and daemon coordination substrate.
- **#93** structured origin metadata for sessions, policy, audit, and
  Starlark.
- **#94** daemon client registry for onguard client identity and admission.
- **#95** daemon-owned client config store with proposal and approval states.
- **#96** daemon client command queue with leases, labels, and provenance.
- **#97** daemon client events/results and artifact store for background work.
- **#98** daemon scheduler contracts with recurrence, leases, and run history.
- **#99** onguard policy and Starlark starter rules.
- **#100** onguard client runtime and CLI runner.
- **#101** daily newspaper digest onguard client and interest profile.
- **#102** client parity for schedules, queues, config, events, and artifacts.
- **#103** onguard security demos and violation tests.
- **#104** inbox triage onguard client.
- **#105** meeting prep onguard client.
- **#106** watch-folder and downloads processor onguard client.
- **#107** personal knowledge update onguard client.
- **#108** task follow-up onguard client.
- **#109** research monitor onguard client.
- **#110** local desktop automation monitor onguard client.
- **#111** finance document guard onguard client.
- **#112** deterministic onguard approval/denial clients and examples.

### Sequencing

1. Add structured origin metadata first, because policy/Starlark and audit need
   to distinguish scheduled/queued/headless work from human foreground work.
2. Add daemon-owned registry/config/queue/event/artifact stores before building
   the worker runtime, so clients do not invent private coordination paths.
3. Add schedule leases and run history before any recurring job runs.
4. Add policy/Starlark starter rules before enabling useful background work.
5. Build the reusable onguard runtime and then the daily newspaper client.
6. Add client parity and violation demos before closing the milestone.

### Current implementation status

- Structured session origin metadata is persisted and included in session
  creation audit payloads.
- The daemon now exposes onguard registry, config, command queue,
  events/results, artifacts, schedules, schedule leases, and schedule history
  RPCs backed by the shared SQLite state DB.
- MCP-control exposes the new onguard coordination RPCs for external control
  clients; TUI and Swift GUI expose read-only operator views over onguard
  clients, queues, schedules, configs, artifacts, and events.
- Policy/Starlark inputs include session origin metadata, and the shipped
  personal-assistant policy bundle includes onguard starter rules for declared
  workflows, sensitive background publication, and low-integrity write review.
- A reusable onguard runtime and `capdep onguard run` CLI runner can claim
  schedules or queued commands and report completion/failure through daemon RPC.
- Packaged deterministic handlers cover daily digest, inbox triage, meeting
  prep, watch folders, knowledge updates, task follow-up, research monitoring,
  desktop monitoring, finance quarantine, and deny-only deterministic approval
  sweeps.
- Remaining product work is fully productized data-source adapters for the
  packaged workflows; the coordination substrate and client review surfaces are
  done.

### Done-when

- Onguard clients can run scheduled or queued work without direct tool,
  credential, or trusted-state access.
- AI-proposed schedules/config changes are drafts until approved.
- Queue items, events, artifacts, and digest outputs carry labels,
  provenance, actor identity, timestamps, and audit ids.
- Policy/Starlark can express general onguard rules and client-specific rules.
- The daily newspaper demo succeeds for allowed sources and blocks prompt
  injection, profile poisoning, sensitive egress, and unauthorized publication.

---

## v0.29.0 — MCP security conformance + external server labeling

This milestone hardens the MCP boundary before CapDep depends on many external
MCP servers and background clients. The current implementation has useful
targeted tests; this milestone adds adversarial conformance coverage.

### Scope

- **#113** EPIC: MCP security conformance and external server labeling.
- **#114** reusable fake-server fixture harness for deterministic conformance
  tests.
- **#115** session-bound MCP multi-turn labels, approvals, provenance, and
  audit.
- **#116** upstream MCP classification, target extraction, disabled-kind, and
  fail-closed labeling tests.
- **#117** upstream MCP resources and prompts as labeled inputs.
- **#118** MCP-control/admin separation, authority boundaries, and audit.
- **#119** opt-in real MCP server smoke matrix for common external servers.

### Sequencing

1. Build the reusable harness first; all later tests should reuse it.
2. Lock down session-bound MCP, because it is the direct LLM-host surface.
3. Lock down upstream tool classification and label floors before resources and
   prompts.
4. Prove admin/control surfaces are not alternate authority paths.
5. Add opt-in real-server smoke tests only after deterministic coverage exists.

### Current implementation status

- `tests/mcp_conformance.py` provides a reusable in-memory MCP conformance
  harness for deterministic fake-server tests.
- `tests/test_mcp_security_conformance.py` covers ambiguous-tool fail-closed
  behavior, disabled-kind enforcement for renamed tools, explicit override
  requirements, tool/resource label propagation, and admin/control surface
  separation.
- Existing MCP adapter, resource, prompt, session-bound, and MCP-control tests
  continue to cover the daemon-mediated paths.
- Real external MCP server smoke tests are implemented as a skipped-by-default
  matrix in `tests/test_external_mcp_smoke_matrix.py`, configured with
  `CAPDEP_REAL_MCP_SMOKE_CONFIG`, because they depend on local tools,
  credentials, and network state.

### Done-when

- A fake malicious MCP server cannot bypass registration, labels, policy,
  approvals, provenance, or audit.
- External MCP tool/resource/prompt content raises session labels before later
  egress decisions.
- Real-server smoke tests are useful locally but not required for deterministic
  CI.

---

## v0.30.0 — Client integration test parity

This milestone converts client parity from a manifest/source assertion into
live daemon integration coverage. The daemon remains the safety owner; clients
only present or invoke daemon contracts.

### Scope

- **#120** EPIC: Client integration test parity across CLI, TUI, Swift GUI, and
  MCP-control — done.
- **#121** shared daemon integration fixtures — done.
- **#122** CLI live-daemon integration tests — done for onguard
  read paths.
- **#123** TUI live-daemon integration and regression tests — done for console
  and spectator live-daemon smoke.
- **#124** Swift GUI daemon-contract and UI action tests — done:
  SwiftPM daemon-contract model tests cover CapDepMac parsing for security
  context and onguard coordination; launch/window smoke remains in the
  macOS-sensitive tier.
- **#125** MCP-control live-daemon integration tests — done for
  onguard control paths.
- **#126** documented and enforced CI test tiers — documented locally in
  `docs/testing.md`.

### Sequencing

1. Build shared daemon fixtures before adding per-client tests.
2. Add CLI and MCP-control integration tests first because they are easiest to
   automate and cover broad daemon surface area.
3. Add TUI and Swift model/action tests with limited brittle UI automation.
4. Document deterministic, integration, macOS-sensitive, and external-server
   test tiers before closing.

### Current implementation status

- `tests/daemon_integration.py` provides the shared live-daemon fixture and
  mirrors the production handler surface for client-contract tests.
- `tests/test_client_integration_parity.py` starts a real daemon and proves
  MCP-control, CLI onguard paths, and TUI console/spectator surfaces call
  daemon RPCs.
- `apps/macos/CapDep/Tests/DaemonContractModelTests.swift` covers CapDepMac
  daemon response models for session security context and onguard coordination
  state under `swift test`.
- `capdep onguard clients`, `queue`, `schedules`, and `artifacts` expose
  read-only daemon-owned coordination state for operators.
- MCP-control's schedule-create schema now matches the daemon RPC contract
  (`schedule_id`, `client_id`, `command`, `recurrence`), preventing the mock
  tests from accepting invalid daemon calls.
- The TUI spectator refresh path now tolerates teardown races after awaited
  daemon RPCs, avoiding false failures when the UI exits while a refresh is in
  flight.
- `docs/testing.md` defines deterministic default CI, live-daemon
  integration, macOS GUI-sensitive, external MCP smoke, and coverage-ratchet
  tiers.

### Done-when

- Every implemented client path has an automated test proving it calls the
  daemon contract.
- Each intentional omission remains explicit in `docs/client-parity.json`.
- CI failures identify whether a regression is daemon contract, client routing,
  GUI-sensitive, or external-server related.

---

## v0.31.0 — Multi-session security context observability

This milestone makes CapDep's safety state inspectable across multi-turn,
multi-client workflows. It is the answer to: which security model, flow
pattern, labels, tools, external MCP servers, onguard clients, approvals,
policy rules, and provenance are active in this session?

### Scope

- **#127** EPIC: Multi-session security context and external actor
  observability.
- **#128** daemon `session.security_context` model and RPCs.
- **#129** session security event ledger and provenance index across turns.
- **#130** policy/Starlark context with actor, flow, and external-tool
  metadata.
- **#131** client exposure for CLI, TUI, Swift GUI, and MCP-control.
- **#132** multi-session external-actor regression tests.

### Sequencing

1. Define the daemon security-context JSON model and RPC. **Done locally** via
   `session.security_context`.
2. Add the ledger/provenance index needed to populate it without client-side
   inference. **Done locally** as a deterministic projection over session
   state, audit, approvals, provenance, onguard store, and upstream/tool
   metadata.
3. Extend policy/Starlark context so decisions can use the same structured
   actor and flow metadata shown to users. **Done locally**: script-backed
   decision inspectors receive session origin plus action tool/effect,
   external-tool, and flow metadata.
4. Expose the daemon view across clients. **Done locally**: CLI, TUI, Swift
   GUI inspector, and MCP-control consume `session.security_context`. Full
   Swift UI automation remains in the macOS-sensitive test tier.
5. Add multi-session regression tests that compare security context,
   provenance, audit, and final policy decisions. **Done locally** for direct
   daemon projection plus live CLI/MCP-control parity and TUI rendering
   compatibility.

### Done-when

- A user can inspect why a session is allowed, blocked, or waiting for
  approval.
- The answer includes labels, flow pattern, policy/Starlark rules, external
  MCP servers/tools/resources, onguard origins, approvals, provenance, and
  audit evidence.
- Clients render daemon state rather than reconstructing safety context
  independently.

Current implementation notes:

- `session.security_context` returns a versioned JSON document containing
  session, labels, capabilities, origin, actors, approvals, policy decisions,
  provenance, security-model evidence, flow-pattern evidence, audit evidence,
  and explicit limitations.
- The projection is read-only and daemon-owned; clients should render this
  view instead of joining lower-level RPCs themselves.
- The handler intentionally reports limitations when no provenance or upstream
  MCP actor evidence exists, rather than implying unobserved controls are
  active.
- Script-backed decision inspectors see the same actor/flow posture through
  `session["origin"]`, `action["tool"]`, `action["effect_class"]`,
  `action["external_tool"]`, and `action["flow"]`, so Starlark/Python-reference
  policy can tighten decisions without reconstructing daemon state.

---

## 07 Support — Source identity and labeling correctness

The highest-leverage supporting track: the refinement layer is on, and the
remaining work makes source identity and labels reliable.

### EPIC #41 — Activate the decision-refinement layer
- ✅ **#46** wire the loader (done — layer is live)
- ✅ **#48** read-only session-history summary is threaded into inspector
  inputs and covered by a real-chokepoint frequency/aggregation workflow.
- ✅ **#47** starter library core shipped: sensitive-egress confirm,
  purpose-scoped relax, bounded-relax floor guard, frequency policy, and
  builtins. Relationship/identity-aware relaxes still depend on #51.
- ✅ **#49** `capdep why <decision>` is shipped; keep extending it when new
  decision origins are added.

### EPIC #42 — Strengthen the labeling oracle
- ✅ **#50** catalog-aware tiers · ✅ **#5** fs labeling
- ✅ **#33, #34** design docs (mapping + email labeling) — *design closed;
  implementation tracked under #51 and the email labeler*
- ▶ **email labeler** (impl of #34) — declarative `email_label_rules.yaml`
  + per-message hook, reusing the #5 labeler shape. **Next labeling impl.**
- → **#51** Gmail/Drive/Calendar SourcePort canonical ids (v0.17) — the
  identity layer both #33 and #34 depend on for external-recipient and
  message-id binding.

**Sequencing within this track:** email labeler (uses #5 shape), then #51
unlocks the identity-dependent parts of #33/#34.

---

## v0.17 — Gap hardening & explainability

Close/guard the documented gaps; improve operator trust.

### EPIC #43 — Harden documented model/principle gaps
- ✅ **#52** restricted floor · ✅ **#53** loud Biba
- ✅ **#49** `capdep why <decision>` surfaces the rule/floor/inspector that
  fired.
- ✅ **#54** purpose-limitation boundary reframed and pressure-tested:
  purpose-scoped spawn/grant/delegation refuse inadmissible categories.
  The remaining model-reasoning contamination case is an explicit non-goal,
  not a hidden implementation gap.
- → **#55** cross-host RemoteApprovalEnvelope four-axis wire format — moved
  into v0.37 with the substrate/compliance replay milestone.

### Standalone v0.17
- **#13** credential vault (P1) — inject secrets at the chokepoint, never
  in LLM context. Highest standalone P1; independent of the epics.
- **#51** SourcePort canonical ids (P2) — also serves #42; schedule here.
- **#11** quarantined-extract schema library (P2) — EmailForwardable,
  WebPagePublicFacts; complements the email labeler.

**Sequencing:** #13 (independent, high value) ‖ #51 (serves both
milestones) plus the labeling-oracle / real-substrate assurance work tracked
in `workflow-plan.md`. #55 now rides with v0.37 substrate/compliance.

---

## 08 Support — Terminal UX and approval polish

This milestone is now terminal-client quality work, not the whole product UX
strategy. CapDep has multiple clients: CLI/chat for terminal work, TUI for
live supervision, Swift GUI for the primary macOS desktop workspace, and
MCP-control for automation. Rich desktop inspection and coordination should be
daemon-backed and surfaced through the appropriate client instead of forcing
all UX into the REPL.

- Active terminal UX: **#16** REPL polish, **#17** split-pane/tabbed viewer,
  **#19** terminal graphics, **#27** inline approval banner, **#28** semantic
  CLI styles, and **#29** unicode/minimum-size safety.
- Moved to v0.32: **#31** disconnect cancellation and **#32** heartbeat
  cancellation, because these are daemon/client coordination guarantees.
- These remain secondary to the Swift GUI desktop workspace, but they are
  planned terminal polish rather than generic backlog.

---

## 09 Research — Non-goals and safe alternatives

Research tracks intentionally deferred product pressures without turning them
into implementation commitments: remote/mobile daemon control (#178), broad
always-on action modes (#179), community sharing (#180), and web/cross-platform
GUI alternatives (#181).

---

## 10 Backlog — Formal models and deferred breadth

Deferred formal work and any provider breadth not explicitly scheduled in
v0.36/v0.37. Pull forward on demand.
- Formal: **#58** lattice join/dominance operator, **#59** ocap
  cascade-revocation eager teardown, **#45** formal-model completeness.

---

## Dependency graph (the load-bearing edges)

```
#5 (fs labeler shape) ──▶ email labeler (#34 impl)
#51 (canonical ids) ──▶ external-recipient gates (#33), message-id bind (#34)
#13 (credential vault) ── independent, high value
workstream coordination (#31/#32/#133/#134/#136/#138) ── coordinate with daemon
  state, session coordinator, client liveness, and agent-loop cancellation
```

## Recommended next 3 (refreshed)

1. **#136** finish the parity contract for `daemon.state` and `workstream.*`
   and keep the manifest ratchet green.
2. **#133/#138** harden and test owner-only send/cancel, release, expiry,
   reclaim, disconnect, heartbeat, and multi-client monitoring behavior.
3. **#134** expose workstream ownership and daemon state in the clients that
   need first-class UX, especially Swift GUI and TUI operator views.

After v0.32, return to **#13** credential vault as the highest-value security
hardening item, then pull forward **#9/#14** if upstream MCP isolation becomes
the main operational risk.
