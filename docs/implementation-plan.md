# Implementation plan & milestones

Living plan that organizes the open GitHub issues into sequenced
milestones with dependencies. Authoritative status is GitHub; this doc is
the *sequencing rationale*. Last refreshed 2026-06-21 after v0.26 client
parity closed, v0.27 practical setup started, and the MCP/client
test/security-context hardening milestones were opened.

Milestones (GitHub): **v0.27.0** Practical setup + daemon-owned settings ·
**v0.28.0** Onguard clients + daemon coordination ·
**v0.29.0** MCP security conformance + external server labeling ·
**v0.30.0** Client integration test parity ·
**v0.31.0** Multi-session security context observability ·
**v0.25.0** MCP compatibility and security integration ·
**v0.16** Policy expressiveness & labeling · **v0.17** Gap hardening &
explainability · **v0.5** UX EPIC · **Backlog** Substrate breadth & formal
models.

`ROADMAP.md` is the canonical product roadmap. This file explains sequencing,
dependencies, and why the next pull should focus on one milestone over another.

Three themes currently drive priority:
1. **Practical setup** — CapDepMac must let a user configure real connectors
   without hand-editing YAML. This is the active v0.27 product track.
2. **Onguard extensibility** — background clients should be normal daemon
   clients, not privileged sidecars or daemon-embedded product workflows.
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
5. **Multi-session explainability** — v0.31 makes labels, flow patterns,
   external actors, approvals, policy rules, provenance, and audit inspectable
   across turns and clients.
6. **Decision fatigue** — coarse policy → rubber-stamping → eroded human
   oversight. Fixed by the decision-refinement layer (EPIC #41).
7. **The labeling oracle** — IFC guarantees ride on correct labels. Fixed
   by broadening label coverage (EPIC #42).

The policy themes come from `docs/security-alignment-assessment.md`:

---

## Recently shipped (this cycle)

| # | What | Milestone |
|---|---|---|
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
| #13 | Credential vault (spawn-time; per-call needs #15/#16) | v0.17 |

**EPIC #41 essentially complete** (layer live, frequency policy, `capdep why`).
**EPIC #42 core shipped** (catalog tiers, fs + email labelers); remaining
is #51 (canonical ids) + the identity-dependent email layers.

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
  constants — implemented locally.
- **#71** replace empty Setup/Open/Fix buttons with daemon remediation actions
  — implemented locally with `setup.run_action` and action descriptors.
- **#73** source bindings and labeling editor — implemented locally through
  daemon-owned `source_binding.*` RPCs and CapDepMac Trust UI.
- **#76** fix task/menu actions that navigate without completing the intended
  action — implemented locally for approval focus, config validation,
  automation pause/resume, and screen-control requests.
- **#72** daemon-backed account and OAuth setup workflows for Google and local
  app connectors — partial: Gmail OAuth is first-class; connector status now
  covers Gmail, Calendar, Drive, and local apps, but Calendar/Drive do not yet
  have first-class OAuth forms.
- **#74** automation pause, screen-control enablement, and Touch ID policy —
  implemented locally: pause/resume and screen-control requests are
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
  clients; Swift/TUI review ergonomics remain follow-up surface work.
- Policy/Starlark inputs include session origin metadata, and the shipped
  personal-assistant policy bundle includes onguard starter rules for declared
  workflows, sensitive background publication, and low-integrity write review.
- A reusable onguard runtime and `capdep onguard run` CLI runner can claim
  schedules or queued commands and report completion/failure through daemon RPC.
- Packaged deterministic handlers cover daily digest, inbox triage, meeting
  prep, watch folders, knowledge updates, task follow-up, research monitoring,
  desktop monitoring, finance quarantine, and deny-only deterministic approval
  sweeps.
- Remaining gaps are richer Swift/TUI review surfaces and fully productized
  data-source adapters for the packaged workflows.

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
- Real external MCP server smoke tests remain opt-in follow-up work because
  they depend on local tools, credentials, and network state.

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
  MCP-control — in progress.
- **#121** shared daemon integration fixtures — implemented locally.
- **#122** CLI live-daemon integration tests — implemented locally for onguard
  read paths.
- **#123** TUI live-daemon integration and regression tests — implemented
  locally for console and spectator live-daemon smoke.
- **#124** Swift GUI daemon-contract and UI action tests — partial:
  daemon-backed model/action coverage exists; macOS UI-sensitive tier remains.
- **#125** MCP-control live-daemon integration tests — implemented locally for
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

## v0.16 — Policy expressiveness & labeling

The highest-leverage milestone: turn the refinement layer on (done) and
make labels real.

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

**Sequencing within v0.16:** #48 → finish #47 ; email labeler (uses #5
shape) ; then #51 unlocks the identity-dependent parts of #33/#34.

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
- ▶ **#55** cross-host RemoteApprovalEnvelope four-axis wire format (P2) —
  signed-protocol versioning for federation.

### Standalone v0.17
- **#13** credential vault (P1) — inject secrets at the chokepoint, never
  in LLM context. Highest standalone P1; independent of the epics.
- **#51** SourcePort canonical ids (P2) — also serves #42; schedule here.
- **#11** quarantined-extract schema library (P2) — EmailForwardable,
  WebPagePublicFacts; complements the email labeler.

**Sequencing:** #13 (independent, high value) ‖ #51 (serves both
milestones) → #55 (federation) plus the labeling-oracle / real-substrate
assurance work tracked in `workflow-plan.md`.

---

## v0.5 — UX EPIC (in flight, parallel track)

Surface convergence + the agent-cancellation papercuts. Independent of the
policy/labeling work, so it can proceed in parallel by whoever owns UX.
- P1 cluster: **#16** REPL parity, **#22** Rich Live streaming, **#23**
  Ctrl-C cancel, **#27** inline approval banner, **#31** cancel on
  disconnect, **#17** split-pane viewer.
- P2/P3: **#32** UI heartbeat, **#19** sixel/kitty, **#28** color palette,
  **#29** unicode width safety.

Note: #23/#31/#32 (turn cancellation) share machinery with the agent loop
already touched by #2 — coordinate so the cancel paths stay consistent.

---

## Backlog — Substrate breadth & formal models (v1.x / on-demand)

Deferred provider backends + formal work. Pull forward on demand.
- Isolation: **#9** Podman-by-default for upstream MCP, **#14** per-upstream
  egress allowlist (stdio path).
- Providers: **#56** more VersionedWritePort backends (Drive/S3),
  **#57** Modal/Firecracker actuators, **#51** also lands providers here.
- Formal: **#58** lattice join/dominance operator, **#59** ocap
  cascade-revocation eager teardown, **#45** formal-model completeness.

---

## Dependency graph (the load-bearing edges)

```
#5 (fs labeler shape) ──▶ email labeler (#34 impl)
#51 (canonical ids) ──▶ external-recipient gates (#33), message-id bind (#34)
#13 (credential vault) ── independent, high value
agent-loop cancel (#23/#31/#32) ── coordinate with #2's loop changes
```

## Recommended next 3 (refreshed)

1. **#72** finish daemon-backed connector setup beyond the Gmail slice. This is
   the shortest path to a usable macOS/Google Workspace assistant.
2. **#69/#70/#75** daemon-owned settings + validation/log RPCs. This keeps the
   GUI thin and makes configuration supportable.
3. **#71/#76** remove no-op setup/action UI and replace it with daemon-backed
   remediation.

After v0.24.0, return to **#51** SourcePort canonical ids and the
labeling-oracle completeness work. Those remain important, but they depend on
real connector setup being practical enough to exercise.

Container-per-call isolation (#15/#16) is the prerequisite for the
*remaining* halves of #13 (echo-resistance) — pull it forward if
credential echo-resistance becomes a priority.
