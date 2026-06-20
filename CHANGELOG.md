# Changelog

All notable changes to CapableDeputy are documented here. Versions follow
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may carry
breaking changes).

## [Unreleased]

### Daemon memory

- Made the daemon-owned labeled memory store durable in the SQLite state DB
  while preserving the existing policy-aware `memory.*` tool contract.
- `memory.delete` now removes durable entries through the store abstraction
  instead of reaching into an in-memory implementation detail.

## [0.23.0] - 2026-06-20

Native macOS GUI and practical personal-assistant hardening release.

### macOS desktop app

- **Native SwiftUI desktop shell.** Added `apps/macos/CapDep`, a SwiftPM
  macOS app with a menu-bar popover, dashboard window, command palette, task
  panel, settings window, approval detail UI, and integrated macOS command
  menus.
- **Live daemon-backed dashboard.** The app now renders pending approvals,
  sessions, audit events, daemon status, model status, setup checks,
  provenance nodes/edges, relationship groups, and approval patterns from
  daemon RPCs rather than static placeholders.
- **Practical approval surface.** Approval cards expose action, target,
  justification, payload, and input/output labels, and can approve or deny
  through the daemon.
- **Conservative desktop integration.** The app requests notification
  permission, notifies only when pending approvals increase, and captures
  frontmost-app context through a read-only best-effort macOS endpoint that
  fails soft when Automation/TCC permission is absent.

### GUI daemon APIs

- Added GUI-focused RPC handlers: `app.status`, `setup.status`,
  `policy.explain`, `provenance.graph`, and `macos.frontmost_context`.
- Wired GUI handlers into the daemon lifecycle so native clients do not need to
  scrape CLI-oriented endpoints.
- `session.new` now accepts a `purpose_handle`, allowing GUI-created sessions
  to preserve the user's selected purpose.

### Personal-assistant policy usability

- Tightened personal-assistant defaults for Google Workspace and local app
  automation while reducing avoidable approval fatigue.
- Improved relationship-group handling and policy-script coverage for practical
  recurring personal workflows.
- Updated personal-assistant config docs and regression coverage for curated
  defaults, relationship groups, first-use prompts, policy hooks, and tool
  client behavior.

### Documentation

- Added the greenfield GUI product design document covering primary users,
  desktop posture, workflow surfaces, automation model, screen-space rules, and
  integrated menu structure.
- Added the macOS desktop UX strategy document summarizing the design guidance
  behind the native menu-bar, command-palette, approval, dashboard, and setup
  experience.
- Linked the new GUI/UX docs from the README.

### Dependencies

- Bumped `pypdf` from `>=6.11.0` to `>=6.12.0` and refreshed `uv.lock`.

## [0.22.0] - 2026-06-19

macOS and Google Workspace personal-assistant release.

### Personal assistant and MCP integrations

- **Official Google Workspace remote MCP support.** Gmail, Drive, Calendar,
  Chat, and People can now use CapDep's native OAuth2 browser/PKCE login flow
  with refreshable per-server token caches.
- **First-class local Apple app automation.** Added bounded MCP servers and
  curated config coverage for Apple Mail, Keynote, Pages, Numbers, and general
  macOS automation without exposing arbitrary AppleScript execution.
- **Mac-first personal-assistant preset.** The bundled preset now uses macOS
  `/Users/*` paths, official Google Workspace remote MCP servers, local Apple
  app servers, and practical read/draft/edit/export defaults.

### Security and capability model

- **Granular automation capability kinds.** Browser and macOS automation are
  split into narrower read, navigation, interaction, script, file, clipboard,
  notification, draft, present, edit, and export grants so operators no longer
  need coarse app-wide authority.
- **Gmail drafts are first-class.** `GMAIL_DRAFT` separates creating a draft
  from broad filesystem creation and from direct `SEND_EMAIL`; Gmail send
  remains disabled by default in the official Workspace config.
- **Service URI source bindings.** Source binding canonicalization now covers
  Google and Apple app URI schemes used by the personal-assistant policy so
  these sources participate in deterministic label resolution.

### Tests and validation

- Added regression coverage for the personal-assistant preset, OAuth config
  generation, Pages/Numbers bounded AppleScript tools, granular capability
  inference, and Gmail draft policy behavior.

## [0.21.0] - 2026-06-19

Flow-pattern alignment and restricted-source hardening.

### Security and flow architecture

- **Restricted raw memory reads now fail closed.** `memory.read` declares
  source labels before dispatch and refuses restricted/prohibited source data
  before the handler can return raw values to the planner.
- **Pattern ③ memory handles.** New `memory.handle` issues planner-safe
  reference handles for labeled memory values, keeping raw data in the
  runtime-private handle store while preserving source labels for downstream
  handle-aware tools.
- **Sensitive modes hide raw readers.** `DUAL_LLM`, `REFERENCE`, and `SEALED`
  modes remove raw labeled-data readers from the visible tool surface.
- **Visible tools are enforced, not just prompted.** The agent loop now denies
  fabricated calls to tools hidden by the current mode/capability surface before
  dispatch.
- **Mode/tool surface refreshes after taint changes.** When a tool call changes
  session labels mid-turn, CapDep reselects execution mode, rebuilds the visible
  tool list, refreshes the system context, and audits the refresh before the
  next LLM call.
- **Restricted floor beats unsafe mode overrides.** Restricted-tier sessions can
  no longer downgrade to programmatic/turn-level modes through
  `prefer_programmatic` or unsafe forced modes.
- **Programmatic prompts are capability-filtered.** Programmatic mode now lists
  only tools visible to the current session instead of leaking the full registry.

### Tests and demos

- Added regression coverage for raw restricted memory denial, handle-producing
  memory reads, sensitive-mode raw-reader filtering, programmatic prompt
  filtering, and mid-turn mode/tool refresh.
- Updated policy scripts and narrated demos so raw taint-flow examples use
  regulated labels, while restricted data exercises the new handle/denial paths.

## [0.20.0] - 2026-06-18

Local-first model defaults, source-flow hardening, and platform cleanup.

### Local LLM backends

- **MLX is now the default planning backend on Apple Silicon macOS.** With no
  explicit backend/model override, CapDep selects
  `mlx/Qwen/Qwen3-4B-MLX-4bit`, validated through CapDep's structured
  tool-call and quarantined-extraction contract.
- **MLX chat-template adapter.** The local adapter renders model-native chat
  templates, accepts fenced/prefaced JSON, strips `<think>` / `<thinking>`
  blocks before parsing or display, and keeps model-native thinking off by
  default unless `CAPDEP_MLX_ENABLE_THINKING=1` is set.
- **Explicit backend factory.** Operators can choose `CAPDEP_LLM_BACKEND=mlx`,
  `litellm`, or `claude-cli`; hosted/shared deployments should continue to use
  the LiteLLM/API path.
- **Claude CLI backend integrated.** The optional Claude CLI path shells out to
  a locally logged-in `claude -p` session for the subscriber's own local use,
  with Claude Code built-in tools disabled so CapDep remains the policy gate.

### Security and flow architecture

- **Canonical policy context.** Tool execution now uses
  `capabledeputy.policy.context.PolicyContext`; legacy compatibility re-exports
  and private hook/source-flow wrappers were removed.
- **Policy hooks and source-flow split.** Tool policy lifecycle handling moved
  into `ToolPolicyHooks`, while source-label/reference-handle enforcement moved
  into `ToolSourceFlow`, simplifying the tool client and making extension
  points explicit.
- **Reference-handle enforcement hardened.** Pattern 2 declassification now
  denies restricted/prohibited source data unless the flow uses an authorized
  reference handle or sealed pattern, including nested argument binding.
- **Quarantined extraction safer by default.** Extractor errors no longer echo
  raw model output/schema details back through exception messages.

### Platform and maintenance

- **Python baseline raised to 3.14.** Project metadata, lockfile, and container
  examples now target Python 3.14.
- **macOS daemon reliability.** Socket-path tests use shorter temporary paths,
  and pidfile liveness handles BSD/macOS zombie process state.
- **Legacy cleanup.** Demo scenarios, docs, tests, and examples now use the
  canonical policy imports and the simplified policy/source-flow architecture.

## [0.19.0] — 2026-06-08

A greenfield **inline console** (TUI redesign) — and a security model for the
UI itself. Modeled on the Claude-Code conversational-REPL feel: an inline,
streaming console where tool calls and policy decisions render *as they
happen*, the terminal pausing only when a human is needed. Built security-first,
because for a policy agent the presentation layer is a real attack surface.
Launch it with **`capdep ui`**. Sole feature change of this release; built
greenfield alongside the old `tui`/`console` commands (now deprecated).

### The inline console (`src/capabledeputy/tui/inline/`)

- **Streaming conversational REPL** (Textual inline mode): a fixed engine-sourced
  status line (purpose / clearance / live taint / advisories), a streaming
  conversation, and an input. Decisions render inline as chips and cards.
- **Security built into the presentation layer** (see `docs/tui-redesign.md`):
  - *Untrusted content can't impersonate chrome.* `quarantine()` strips every
    terminal escape vector (CSI color/cursor, OSC hyperlinks + title +
    iTerm/kitty images, DCS/sixel, controls) and renders untrusted blocks as
    gutter plaintext — an ANSI-styled fake approval card collapses to inert
    text.
  - *Decision cards are drawn from a typed `PolicyDecision`, never a model
    string* — a type-level guarantee that no model prose reaches a decision
    surface (FR-036 / Principle V).
  - *Armed interaction:* a keypress (`a`/`d`/`o`) resolves only the one decision
    the app has armed; keys are inert otherwise, so a painted fake card approves
    nothing.
  - *Per-session anti-spoof marker* on every real card + the status line.
  - *Fail-safe:* a `ctrl+k` kill switch resolves a pending decision toward
    **deny, never allow**; unknown status fields render `—`, never blank.
  - *Grave-action escalation:* `OVERRIDE_REQUIRED` opens a focused confirm that
    requires typing the engine-provided target.
- **`/flow` data-lineage screen** — the session's tool calls as a provenance
  chain with per-step decision glyphs; makes the IFC/declassification story
  *visible*.
- **Automation harness** (`harness.py`) — a no-terminal `HeadlessConsole` records
  a structured, assertable transcript and auto-answers prompts via a pluggable
  decider (`approve_all` / `deny_all` / `by_rule`), so scenario scripts are
  trivial. The driver is view-agnostic (`ConsoleView`), so the same script runs
  against the real UI (Textual `Pilot`) or the recorder, and — once the live
  daemon driver lands — the full server+UI stack unchanged.

39 inline-console tests (adversarial quarantine, typed-decision rendering, the
armed interaction via Pilot, the scriptable harness). Live daemon wiring and the
remaining surfaces (`/audit`, `/sessions`, theme polish, `textual serve`) are
tracked in `docs/tui-redesign.md` / `docs/usability-hardening-plan.md`.

## [0.18.0] — 2026-06-08

Accurate-by-default labeling, assurance hardening, and a green CI. The headline
behavior change is that the **content-scan labeling oracle now ships on by
default** — a fresh deployment auto-labels genuinely-sensitive reads, which
both raises safety (the foundation under every IFC/BLP/Brewer-Nash guarantee)
and *reduces* approval fatigue (the engine gates the right things instead of
the all-or-nothing binary). Plus two adversarial assurance slices, a full
type-check cleanup, and three design/assessment docs.

### Labeling oracle on by default (usability U1a)

- **Feature: `configs/fs_label_rules.yaml` + `email_label_rules.yaml` ship
  ACTIVE**, curated high-precision (financial/tax paths, credential dirs+globs,
  health dirs; financial-institution senders + universal subject cues; health =
  clinic-domain AND clinical-body-cue). Precision over recall so benign reads
  stay unlabeled — no new friction. Labelers are raise-only (escalate, never
  lower).
- **Fix: added a `credentials` category** (restricted/fixed-high) to
  `labels.yaml` — without it, `confidential.credentials` resolved to the
  unknown-default `regulated` and under-classified secrets (caught by the
  raise-only adversarial test).

### Assurance (adversarial, on the security models)

- **Pattern ③ reference-handle redirection-resistance** (slice #3): forged
  handle binds nothing, cross-session theft discloses nothing end-to-end,
  value frozen at issue, planner data-blind.
- **Pattern ② dual-LLM declassification** (slice #4): a prompt injection in the
  confidential content can't escalate (tool-call refused), can't add exfil
  fields (schema-stripped), can't bulk-smuggle (length-capped); planner never
  sees raw.

### Quality / CI

- **All 189 pre-existing pyright errors fixed** (incl. two latent bugs: async
  `DaemonClient.call` invoked without `anyio.run`; `str` passed where the MCP
  SDK wants `AnyUrl`). Repo `ruff format`-clean. Two env-dependent tests made
  CI-robust. **CI now passes all four gates** (ruff check, ruff format, pyright,
  pytest) on Python 3.12 + 3.13 — green for the first time.

### Docs

- Rewrote `security-alignment-assessment.md` to the current (v0.18) state with
  an Impl-vs-Default-policy split and an intersection analysis.
- `usability-hardening-plan.md` — anti-fatigue remediation as phased slices
  (U1–U7) with a "no slice may loosen a floor" invariant.
- `tui-redesign.md` — greenfield TUI design (inline conversational REPL on the
  same stack) + a safety-alignment review of the design itself.

## [0.17.0] — 2026-06-08

Human-in-control & assurance. Two threads: a **trust-profile** model that lets
a self-configured operator be the root of trust (override anything they own,
with friction) without ever letting untrusted content trigger or redirect a
flow; and a **second-generation workflow-assurance** suite that pressures the
security models adversarially on the real operator config.

### Trust profile — operator as root of trust (FR-049)

- **Feature: `trust_profile` switch** (`policy/overrides.py`,
  `configs/override_policy.yaml`) ∈ `{managed | personal}`, default `managed`.
  `managed` is the historical fail-closed enterprise posture, **unchanged**.
  `personal` makes the operator the root of trust: a floor with no explicit
  Override Policy defaults to `single-authorized` (solo override + friction,
  no second attester). Requires `operator_principal`; refused at load without
  one. Expands only the **human's** reach — never the model's (FR-011 holds).
- **Feature: structural conflict floors are override-targetable.** The four
  always-on conflict invariants (untrusted/health/financial co-presence with
  egress) became mintable Override floors (string-identical to their engine
  rule ids). A `personal` operator can solo-override them; `managed` keeps
  them hard. The grant short-circuit is floor-agnostic, so no engine change
  was needed — the gap was only mintability.
- **Feature: standing rules may cross floors over the operator's OWN data.**
  Under `personal`, a human-ratified Decision Rule may name `crosses_floor`
  (health/financial) to auto-cross it — cutting approval fatigue. Crossing is
  explicit (a relaxing rule that doesn't name the floor can't cross it),
  profile-gated (inert in `managed`), and ratified-only (FR-014).
- **Feature: grouped override.** One friction confirmation mints a grant over
  a SET of (action_kind, target) members (FR-035 grouping applied to
  Override); each member single-use, the grant ACTIVE until all are consumed.
- **Security (the hard line — operator autonomy ≠ adversary autonomy):**
  untrusted content can at most raise an override **request** — it can never
  auto-trigger or redirect a flow, in either profile. `untrusted-meets-egress`
  is **never** rule-crossable (refused at load AND re-guarded at compose), and
  every override (single or group) is pinned to an exact destination, so an
  injected redirect to a new target is never authorized.

### Certified declassification — the trust hinge (slice #2)

- **Fix (F9): the certified declassifier now lowers propagated taint.** It was
  removing taint only from a tool's `inherent_tags`, leaving the propagated
  `additional_tags` tainted — so a declassified external read still tainted
  the session and egress was still denied (the hinge was silently inert).
  Routing a read through a certified `SchemaProjector` now lowers the untrusted
  taint so a previously-denied egress proceeds; an uncertified taint-removal
  is still refused (Constitution VI).

### Egress policy (FR-019, amended)

- **Change: irreversible communication egress → human approval by default.**
  Sending email/messages routes to `REQUIRE_APPROVAL` (approve-at-the-moment)
  rather than a hard DENY; operator-configured super-sensitive data escalates
  to `OVERRIDE_REQUIRED` (`policy/egress_escalation.py`,
  `configs/egress_escalation.example.yaml`). **Purchases/commitments keep the
  stricter DENY→override.** Structural floors (BLP/Biba/conflict invariants)
  still DENY health/financial/untrusted egress regardless.

### Workflow assurance

- **Tests: second-generation workflow-pressure suite** — model-derived,
  multi-step, adversarial scenarios on the **real v2 config**
  (`tests/test_workflow_pressure.py`), the layer the bulk catalogue never
  touches. Plus a 1126-scenario personal-assistant catalogue, narrated demos
  with a CI anti-rot guard, all 25 demos migrated to the four-axis model, and
  end-to-end Starlark decision-inspector scenarios.
- **Docs: workflow assurance plan** — coverage matrix + scorecard + two gates
  (`docs/workflow-plan.md`), registry, and categorized index. Findings F1–F9
  logged (incl. F9 above and several "green-but-lying" demos/tests fixed).

## [0.16.0] — 2026-06-07

Policy expressiveness & labeling. The dormant decision-refinement layer is
now **live**, the labeling oracle covers **local files and email**, every
decision is **explainable**, and the documented model gaps are hardened —
plus a credential vault and the v0.15.2-dev substrate work. Folds in the
unreleased 0.15.2 items.

### Decision-refinement layer (EPIC #41 — now active)

- **Feature (#46): the refinement layer is ON.** A daemon-config
  `decision_inspectors:` block (`policy/decision_inspector_loader.py`) is
  loaded into `PolicyContext`: builtins (`self_egress_relaxer`,
  `after_hours_purchase_tightener`) + operator scripts compiled via
  `get_script_host(runtime)` and wrapped in an async `ScriptDecisionInspector`.
  The chokepoint awaits async inspectors. Fail-closed at load; eval-time
  errors caught + audited as abstain.
- **Security (review fix): bounded relax.** A DecisionInspector `relax`
  can only soften a `REQUIRE_APPROVAL` base to `ALLOW`; `DENY` /
  `OVERRIDE_REQUIRED` are structural floors and non-relaxable (FR-026).
  Attempts are refused + audited — closes a hole where a script could
  relax a structural DENY → ALLOW.
- **Feature (#48): frequency policy.** Inspectors receive a bounded,
  read-only `session["history"]` (per-kind cumulative counts) so scripts
  can express "N sends this session → require approval."
- **Feature (#47): starter library.** Five reviewed Starlark scripts
  (`sensitive_egress_confirm`, `purpose_scoped_relax`, `frequency_cap`,
  `relationship_relax`) + the two builtins; `configs/policies/` +
  `configs/decision-inspectors.example.yaml`. Relationship-aware relax
  resolves the target's RelationshipGroups into `action["relationship_groups"]`.
- **Feature (#49): `capdep why`.** Explains a decision — base rule +
  reason, v2 outcome + matched rule ids, the correlated inspector
  adjustment, and any relaxation refusal.

### Labeling oracle (EPIC #42 — core)

- **Feature (#50): catalog-aware tiers.** The flat-string label path
  resolves each category's tier from `labels.yaml` instead of flattening
  to `REGULATED` (health/financial are `restricted`), restoring BLP
  clearance strength on that path.
- **Feature (#5): dynamic filesystem labeling.** `policy/fs_labeling.py`
  attaches raise-only Axis-A category labels to `fs.read`/`fs.read_pdf`
  (path-prefix / filename-glob / content-regex tiers), so local-file data
  participates in IFC. `configs/fs_label_rules.example.yaml` + RFC.
- **Feature (#34): email labeling.** `policy/email_labeling.py` — a
  raise-only per-message labeler (from_domain / from_address / subject /
  body) wired through a generic `result_labeler` hook on the upstream
  adapter; design in `docs/email-labeling-design.md`.
- **Docs (#33):** `docs/google-workspace-capability-mapping.md`.

### Gap hardening (EPIC #43) & reliability

- **Fix (#52): restricted-tier mode floor.** `select_mode` now routes a
  `restricted`-tier turn to Pattern ③ REFERENCE / ⑤ SEALED (or fails
  closed), instead of silently de-escalating to Pattern ②/①.
- **Feature (#53): loud Biba gap.** `capdep policy models` prints each
  model's honest scope and flags Biba's one-direction limit.
- **Fix (#2): agent-loop auditability.** `AGENT_LOOP_EXCEEDED` /
  `AGENT_LOOP_THRASHING` audit events with the last-N tool calls; a thrash
  guard stops repeated identical calls early; `agent_max_iterations`
  configurable; `capdep audit --filter`.

### Credential vault

- **Feature (#13, spawn-time): credential vault.**
  `upstream/credential_vault.py` — a mode-0600 vault holds upstream
  secrets out of the daemon config, the daemon's broad env, the audit log,
  and the LLM context; injected into each server's spawn env, audited by
  ref (`credential.injected`). Per-call echo-resistance deferred to
  container isolation (#15/#16).

### Docs

- `docs/security-alignment-assessment.md` (grounded model/pattern/principle
  scorecard), `docs/implementation-plan.md` (milestones + dependency
  graph), README "How it works — and its honest limits" + DESIGN §3/§15.x
  honest-limitations sections.

### Substrate (was 0.15.2-dev)

- **Feature: sandboxed Starlark policy host.** `StarlarkScriptHost`
  (`substrate/policy_script_host.py`) is the real `PolicyScriptHost`
  sandbox, backed by starlark-rust via the `starlark-pyo3` binding —
  shipped as the optional extra `capabledeputy[starlark]`. Unlike the
  best-effort AST-filtered `SafePythonScriptHost`, Starlark gives
  *language-level* isolation: a policy script has no imports, no Python
  builtins, and no I/O — only the injected action/session/proposed_outcome
  dicts and the `relax`/`tighten`/`abstain` helpers (same contract as the
  reference host). New `get_script_host(runtime_kind)` registry/factory
  (fail-closed on unknown). The runtime is lazily imported and raises a
  typed `PolicyScriptHostUnavailableError` when the extra is absent.
  Threat model + residual risks (no hard step/CPU budget yet) documented
  in `specs/004-mcp-and-substrate/starlark-policy-host-threat-model.md`.
  (WebAssembly/wasmtime host dropped — Starlark covers the need.)
- **Feature (#12): git-backed substrate providers.** First concrete
  implementations behind the source/version-write ports:
  `GitVersionedWritePort` (`substrate/git_versioned_write.py`) commits
  each write to a git repo and surfaces a `<commit>:<path>`
  `prior_version_handle` that `read_prior_version_hash` resolves with
  `git show` — so `verify_write_discipline` earns `reversible/system`
  (FR-044). `GitSourcePort` (`substrate/git_source.py`) canonicalizes
  targets to stable `git:<repo-relative>` ids, fail-closed on path
  escapes (FR-048). Selected via `get_source_port`/
  `get_versioned_write_port` registries (modular — new backends plug in
  behind the same ports). (Clarification: the Podman `SandboxActuator`
  was already implemented; it is the ephemeral `EXECUTE.sandbox` runtime,
  complementary to `PodmanDevbox`'s persistent `EXECUTE.devbox`, not a
  replacement. Modal/Firecracker actuators remain deferred to v1.1+.)
- **Docs**: `specs/004-mcp-and-substrate/substrate-provider-candidates.md`
  — candidate providers behind each substrate port (Gmail/Drive/SharePoint/
  S3 source + versioned-write, gVisor/Firecracker/Modal actuators, …),
  with what each does, why, and the value to a typical user's workflow,
  plus the autonomy (`reversible/system`) and anti-confused-deputy
  mechanics that make each worth building.
- **Test**: fixed the flaky `test_run_status_stop_lifecycle` (socket-wait
  timeout was 2s vs the daemon's ~8s startup); suite is fully green.

## [0.15.1] — 2026-06-06

Post-0.15.0 cleanup of deferred redesign debt (no behavior change).

- **R4b.4**: collapsed `Session.axis_a`/`axis_b` into a single
  `label_state: LabelState` field (store schema → v8) and **deleted** the
  now-internal `AxisA`/`AxisB` wrapper classes, the
  `most_restrictive_inherit_axis_a/_b` legacy functions, and the
  `LabelState.from_axes`/`to_axis_a`/`to_axis_b` converters. `decide()`,
  `decision_rules.evaluate`/`RulePredicate.matches`,
  `assurance.control_plane_admissible`, the raise-only `inspector_port`,
  and `PolicyDecision` (now `labels_snapshot`) all type directly on
  `LabelState`. Also removed the vestigial `ProvenanceTag.integrity_floor`
  flag (the integrity floor is an `Operation.required_floor`). Pure
  internal-representation tidy-up; enforcement unchanged, suite green.
- **Fix**: `ToolCallRecord.{arg,inherent}_labels` are flat category/level
  strings post-R7 (bundle wire format), not `Label`; corrected a latent
  `label.value` serialization in `programmatic_handlers`.
- **Chore**: repo-wide `ruff check` now clean (was 69 pre-existing) —
  auto-fixes, collapsible-ifs, `contextlib.suppress`, `ClassVar`, and
  `noqa`-with-reason for intentional cases (descriptive domain exceptions,
  cycle-avoidance late imports, MCP tool-annotation field names).
- **Docs**: refreshed stale audit docstrings — cross-rotation chain
  verification (`verify_audit_chain(..., include_rotated=True)`, CLI
  `capdep audit verify --include-rotated`) is already implemented + tested.

## [0.15.0] — 2026-06-06

Completes the spec-003 label-model redesign: the four-axis `LabelState`
model is now the **sole** label model and the legacy flat `Label` enum is
**deleted** (no backwards compatibility; `state.db` wiped on cutover at
schema v7). BLP/Biba/confused-deputy enforcement is unchanged — it moved
to always-on four-axis engine invariants, proven equivalent to the flat
rules before the flat path was removed. The only remaining vestige is the
internal `AxisA`/`AxisB` representation behind `LabelState` (collapsing
those into a single stored field is deferred polish, tracked for 0.15.x).
See `specs/003-labeling-framework/label-model-redesign.md`.

### Label-model redesign (continued — no backwards compatibility)
- **R4c**: the four always-on conflict invariants are ported off the flat
  `Label` set onto the propagating axes as engine invariants
  (`engine._conflict_invariant_outcome`): Axis-B `external-untrusted`
  provenance + egress ⇒ DENY (integrity / confused-deputy); Axis-A
  `health`/`financial` category + egress ⇒ DENY / REQUIRE_APPROVAL
  (confidentiality confinement). Computed from `LabelState`, composed
  most-restrictively, and proven to agree outcome-for-outcome with the
  legacy `CONFLICT_RULES` (`tests/policy/test_conflict_invariant_four_axis.py`).
  Additive — both legs enforce until R4d removes the flat one. (These are
  information-flow invariants, not Brewer-Nash/Chinese-Wall COI rules
  despite the legacy naming.)
- **R5**: wired apply-source #2 (operation/tool inherent declaration →
  session four-axis state). The dispatch chokepoint now raises the
  equivalent `LabelState` taint via the new `SessionGraph.add_tags`
  (monotone `most_restrictive_inherit`) from the same declaration set it
  feeds to the flat `add_labels`, using the canonical `labels.tags_for_labels`
  forward map (confidential.* → Axis A category, untrusted/trusted.* →
  Axis B provenance; `egress.*` un-fused to nothing — effects are not
  propagating tags). The session's `label_state` now accumulates
  equivalently to the flat `label_set`, so the R4c four-axis invariants
  enforce in the real daemon — not just under direct `labels=` test
  inputs — which is the precondition for deleting the flat leg (R4d).
  Removal stays declassifier-only (the existing `TagTransfer` /
  `apply_transfer` structural rule). Tests: four-axis taint propagation
  through the chokepoint + the forward-map un-fusing.
- **R6**: session store moves to schema **v7** with **no migration**. The
  v1–v5 upgrade ladder and the legacy `label_set → axis_a/axis_b/axis_d`
  backfill (`_convert_legacy_label_set`, `_LEGACY_TO_AXIS_*`, the Axis-D
  trust-prefix defaults, `_apply_v6_idempotent_alters`, `SchemaVersionError`)
  are deleted. A db at any other schema version is **wiped and recreated
  clean** (`_needs_wipe`), per the single-operator no-backwards-compat
  mandate. `clearance_profile_id` is added to the base `CREATE TABLE` so
  wiped/fresh dbs match the full column shape. The four-axis state
  (`axis_a`/`axis_b`/`axis_d`, i.e. `LabelState` + context) is the
  authoritative persisted form; the flat `label_set` column remains only
  until the enum is deleted (R7).
- **R7 prep** (additive, no behavior change): native tools now declare
  four-axis `inherent_tags` alongside the legacy flat `inherent_labels`
  (inert until the flip). The authoritative, file-by-file R7 atomic-flip
  spec is `specs/003-labeling-framework/r7-flip-plan.md`.
- **R7 (the flip): the flat `Label` enum is DELETED.** The four-axis
  `LabelState` is now the *only* label model — no backwards compatibility.
  Removed across ~15 src subsystems + the test suite: the `Label` enum,
  `ConflictRule`/`CONFLICT_RULES` (the four conflict invariants live only
  in the engine gate now), `Session.label_set` (field + column +
  serialization), `SessionGraph.add_labels`, `PolicyDecision.effective_labels`,
  `decide()`'s flat `label_set`/`rules` params, `ToolResult.additional_labels`
  → `additional_tags: LabelState`, `ToolContext.label_set` →
  `label_state`, `ToolDefinition.inherent_labels`/`arg_inherent_labels` →
  `inherent_tags`/`arg_inherent_tags`, `kind_add_labels` → `kind_add_tags`,
  and the flat-carrying fields on `LabeledValue`, `Resource`,
  `ApprovalRequest`, plus the four-axis rewrites of `select_mode` and the
  agent-context conflict heuristics. The (test-only) `tenancy`/
  multi-tenant flat-label engine was dropped. `decide()`'s `labels=`
  bridge for legacy label *strings* survives as `tags_for_labels_strings`
  for the daemon RPC wire only. **No enforcement behavior changed** — the
  four-axis path already enforced equivalently (R4c/R5). Grep-gate:
  `frozenset[Label]` has zero occurrences. Suite green. Executed via
  parallel migration workflows (core → leaves → tests) with manual
  reconciliation; the redesign is complete.

## [0.14.0] — 2026-06-06

Ships the responsible-AI / CORE-PRO governance work, the agentic risk-register
import, and the **first phases of the spec-003 label-model redesign (R1–R4b.3)**.
The label-model redesign is **in progress** — it is green and behavior-preserving
at every step, but the four-axis `LabelState` model still coexists transitionally
with the legacy `AxisA`/`AxisB` pair (the `decide()` re-type + `AxisA`/`AxisB`
deletion land in R4b.4). BLP (FR-008) and Biba (FR-004) enforcement verified.
See `specs/003-labeling-framework/label-model-redesign.md` "▶ Resume here".

### Governance & responsible-AI
- New docs: `responsible-ai-frameworks.md` (the eight enforceable core
  principles + the human in/on/over-the-loop ladder; control-not-correctness
  scope), `policy-rule-structure.md` (rules attach to Operations/effect
  classes, not tools; the PRO-over-CORE lens + CapableDeputy-vs-CORE
  analysis), `source-bindings.md` (the labeling layer as CORE Resources +
  the raise-only-inspector LLM-labeler pattern).
- Imported the agentic-risk subset of the Model Monster / Process Mechanics
  CORE/PRO registry into `configs/risk_register.json` (excessive agency,
  injection, exfil-via-tools, tool poisoning, privilege escalation, memory
  poisoning, unsafe code exec, purpose-contamination), cross-referenced to
  OWASP/MITRE/NIST/EU-AI-Act.
- Archived CORE/PRO reference pages as cleaned PDFs under
  `docs/vendor/process-mechanics/` (used with permission).

### Label-model redesign (in progress — no backwards compatibility)
- Design note `specs/003-labeling-framework/label-model-redesign.md`: clean
  four-axis model (Axis A+B propagate; C = Operation; D = context), apply via
  3 sources / remove only via certified declassifiers, `EffectClass` enum +
  optional subtype (resolves T012), integrity floor as an Operation
  `required_floor`. Flat `Label` enum + all migration to be deleted;
  `state.db` wiped on cutover.
- **R1**: landed clean types (`policy/effect_class.py`, `policy/label_state.py`)
  + Hypothesis property tests (composition determinism, monotone-raising,
  declassifier-only removal, Biba floor). Tag `v0.14.0-R1-label-types`.
- **R2**: populated the stable-core Axis A category catalog in
  `configs/labels.yaml`.
- **R3a**: new structured `ToolDefinition` shape (`operations`,
  `inherent_tags`) + fail-closed `validate_tool_definition` (the
  contracts/tool_definition.md registry-load rules) + invariant tests.
  Validation is wired into `register()` in R3b once native tools declare
  the new fields.
- **R3b (native)**: migrated all 14 native tool modules to declare
  `operations` (canonical `EffectClass` + subtype) + `risk_ids` (+
  `surfaces_destination_id` for writes/egress). Additive — `inherent_labels`
  kept for the engine until R4.
- **R3c (adapters)**: the upstream MCP + skills adapters now derive
  `operations`/`risk_ids`/`surfaces` from each tool's capability kind
  (`default_operation_for_kind`), so every tool creator declares the new
  shape.
- **R3d (enforce)**: `ToolRegistry.register()` now calls
  `validate_tool_definition` fail-closed — a tool missing required fields
  is refused, never registered (Constitution VI). Migrated the ~12
  unit-test tool factories to declare `operations`/`risk_ids`. **R3
  complete**: the registry is fail-closed on malformed tools. (Engine
  `decide()` re-typing onto `LabelState` + `inherent_tags` population is
  R4; flat `Label` enum deletion is R7.)
- **R4a (leaf consolidation)**: chose option (a) — the new types win.
  Renamed `AxisACategory`→`CategoryTag` and `AxisBEntry`→`ProvenanceTag`
  across the repo (~140 sites), consolidated `LabelState`/`TagTransfer`/
  composition into `policy/labels.py`, and deleted the duplicate
  `policy/label_state.py`. Pure rename + consolidation; suite green
  (2065). Containers `AxisA`/`AxisB`→`LabelState` and the `decide()`
  re-type follow in R4b–d.
- **R4b.1 (converters)**: added `LabelState.from_axes`/`to_axis_a`/
  `to_axis_b` + a `Session.label_state` accessor — transitional bridges
  so `decide()` and call sites can migrate to the bundled `LabelState`
  in R4b.2–4 before `AxisA`/`AxisB` are deleted. Green (2066).
- **R4b.2 (decide accepts LabelState)**: `decide()` now takes an optional
  `labels: LabelState`; when given it derives the transitional
  `axis_a`/`axis_b` internally (equivalence test added). Engine-local, no
  call-site churn yet. Green (2067).
- **R4 audit follow-up**: added `test_tool_risk_ids_in_register` (every
  tool `risk_ids` must cite a real register entry — guards the rule-5 gap
  that `register()` doesn't enforce) and recorded the R4c verification
  points (run-both-and-assert-agreement; fix mis-declared test fixtures)
  in the redesign note. Audit found no critical bugs in R3–R4b.2.
- **R4b.3 (safety net)**: the run-both-assert check found the legacy
  `most_restrictive_inherit_axis_a` (directional, parent-authoritative
  provenance) and the new `most_restrictive_inherit` (symmetric) are
  *distinct operations*, not a bug. Added directional `labels.inherit`
  (preserves the Provenance-security "derivation cannot launder
  provenance" property, FR-022), proven equivalent to the legacy axis
  inherit (`test_directional_inherit_matches_legacy`). The engine's
  delegation/fork path will use `inherit`; session accumulation uses
  `most_restrictive_inherit`. Green (2069).
  Then routed the one composition call site (the FR-025 inspector
  taint-raise in `tools/client.py`) through `labels.inherit` — behavior-
  preserving — leaving `most_restrictive_inherit_axis_a/_b` with **no
  callers** (deletable at R4b.4/R7).

## [0.13.1] — 2026-06-05

### Security (dependency patches)

Bumped transitive dependencies to clear three medium Dependabot/GHSA alerts.
Both packages are transitive and not imported directly; capdep exposes no
HTTP/TCP endpoint (daemon IPC is a Unix domain socket, MCP uses stdio):

- `starlette` 1.0.0 → 1.2.1 — GHSA-86qp-5c8j-p5mr (Host-header path
  poisoning). Not reachable here (capdep never runs a starlette server),
  patched regardless.
- `aiohttp` 3.13.4 → 3.14.0 — GHSA-hg6j-4rv6-33pg (cross-origin redirect
  cookie leak) and GHSA-jg22-mg44-37j8 (untrusted deserialization).
  Client-side, used by litellm for outbound LLM API calls.
- `litellm` 1.83.14 → 1.87.1 — required to lift the `aiohttp < 3.14` cap.

Full test suite green (2041 passed). No source changes.

## [0.13.0] — 2026-06-05

First release promoted to `main`. Consolidates the development line previously
tracked only by milestone tags (`v0.9.0`–`v0.12.0-cookbook-shipped`) into a
released, version-stamped baseline. Package metadata (`pyproject.toml`,
`capabledeputy.version`) now tracks the release version (previously pinned at
`0.0.1`).

### Highlights

- **Deterministic capability + information-flow chokepoint** — every agent
  action flows through one LLM-isolated decision point (Constitution
  Principle I: zero LLM participation in decisions).
- **Dual-LLM quarantined extractor** — labeled data is processed by a
  quarantined model behind a defense-in-depth constraint pass; the planner LLM
  is treated as untrusted.
- **Tamper-evident audit** — append-only JSONL audit log with a hash chain and
  `capdep audit verify`, including cross-file chain verification over rotated
  logs (`--include-rotated`).
- **Approval economy** — sibling-group approvals, default-decline-after-N for
  stale cards, rate-limit-as-friction escalation, and per-rule SHADOW outcomes
  for safe A/B testing.
- **Relationships** — relationship groups with auto-narrowing and
  per-counterparty reputation tiers.
- **Devbox substrate** — persistent per-session containers for multi-turn
  software work, an idle reaper, and teardown of live containers on daemon
  shutdown.
- **Chat REPL** — terminal-capability-aware markdown rendering, inline progress
  region, per-upstream MCP server status, and session / month-to-date token
  spend in the toolbar.
- **Labeling framework (spec 003) — partial.** Orthogonal label axes,
  deterministic sensitivity resolution, the structured Purpose Handle, the
  per-purpose risk-preference dial, scoped/time-boxed Override Grants,
  ratification authorization, and the decision-latency SLO are in. Remaining
  003 user stories (full purpose-scoped admissibility, robustness/assurance
  deltas, clearance / integrity-floor / sealed-effect fidelity targets, and
  Phase 9 polish) are tracked for the next release.

### Other

- `secrets`: API-key loader now falls back to `~/.config/anthropic/api.key`
  after the cwd-local `CLAUDEAPI.KEY`.
- `scripts/gemma4_quarantine_bench.py`: benchmark a local ollama model as the
  quarantined extractor using the real production extraction path.

[0.23.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.23.0
[0.22.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.22.0
[0.21.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.21.0
[0.20.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.20.0
[0.19.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.19.0
[0.18.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.18.0
[0.17.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.17.0
[0.16.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.16.0
[0.15.1]: https://github.com/marctjones/capabledeputy/releases/tag/v0.15.1
[0.15.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.15.0
[0.14.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.14.0
[0.13.1]: https://github.com/marctjones/capabledeputy/releases/tag/v0.13.1
[0.13.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.13.0
