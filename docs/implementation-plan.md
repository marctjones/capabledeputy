# Implementation plan & milestones

Living plan that organizes the open GitHub issues into sequenced
milestones with dependencies. Authoritative status is GitHub; this doc is
the *sequencing rationale*. Last refreshed 2026-06-21 after v0.26 client
parity closed and v0.27 practical setup started.

Milestones (GitHub): **v0.27.0** Practical setup + daemon-owned settings ·
**v0.28.0** Onguard clients + daemon coordination ·
**v0.25.0** MCP compatibility and security integration ·
**v0.16** Policy expressiveness & labeling · **v0.17** Gap hardening &
explainability · **v0.5** UX EPIC · **Backlog** Substrate breadth & formal
models.

`ROADMAP.md` is the canonical product roadmap. This file explains sequencing,
dependencies, and why the next pull should focus on one milestone over another.

Three themes currently drive priority:
1. **Practical setup** — CapDepMac must let a user configure real connectors
   without hand-editing YAML. This is the active v0.27 product track.
2. **MCP security integration** — MCP must remain an integration substrate, not
   a second authority path. The v0.25 track is shipped; future work should keep
   that posture.
3. **Decision fatigue** — coarse policy → rubber-stamping → eroded human
   oversight. Fixed by the decision-refinement layer (EPIC #41).
4. **The labeling oracle** — IFC guarantees ride on correct labels. Fixed
   by broadening label coverage (EPIC #42).

The policy themes come from `docs/security-alignment-assessment.md`:

---

## Recently shipped (this cycle)

| # | What | Milestone |
|---|---|---|
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

- **#72** daemon-backed account and OAuth setup workflows for Google and local
  app connectors. Gmail is partially landed; remaining work is the generic
  connector setup/status shape and Calendar/Drive/local-app flows.
- **#69** daemon-owned settings store and settings RPCs for CapDepMac
  (started: `settings.get`, `settings.update`).
- **#70** wire CapDepMac settings controls to daemon settings instead of
  constants.
- **#75** daemon config validation and log-location RPCs for Advanced settings
  (started: `config.validate`, `config.log_locations`).
- **#71** replace empty Setup/Open/Fix buttons with daemon remediation actions.
- **#76** fix task/menu actions that navigate without completing the intended
  action.
- **#73** source bindings and labeling editor is a stretch goal.
- **#74** automation pause, screen-control enablement, and Touch ID policy is a
  stretch goal.
- **Onguard architecture**: adopt headless normal clients for background work,
  with daemon-owned schedule/config/queue/event contracts instead of embedding
  every workflow in daemon core.

### Sequencing

1. Finish daemon-owned settings persistence and Advanced diagnostics.
2. Generalize `google_gmail_setup` into reusable connector setup primitives.
3. Add daemon RPCs for connector status, OAuth client configuration, browser
   login, revoke/clear-auth, reload, and remediation actions.
4. Wire CapDepMac Accounts and Advanced settings to those daemon RPCs.
5. Replace no-op setup/navigation buttons with daemon remediation actions.
6. Add the shared coordination contracts required by onguard clients:
   `schedule.*`, `client.config.*`, `client.queue.*`, `client.events.*`, and
   structured origin metadata for policy/Starlark.

### Done-when

- A user can configure Gmail from CapDepMac without editing YAML.
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

### Sequencing

1. Add structured origin metadata first, because policy/Starlark and audit need
   to distinguish scheduled/queued/headless work from human foreground work.
2. Add daemon-owned registry/config/queue/event/artifact stores before building
   the worker runtime, so clients do not invent private coordination paths.
3. Add schedule leases and run history before any recurring job runs.
4. Add policy/Starlark starter rules before enabling useful background work.
5. Build the reusable onguard runtime and then the daily newspaper client.
6. Add client parity and violation demos before closing the milestone.

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
