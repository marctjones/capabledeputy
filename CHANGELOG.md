# Changelog

All notable changes to CapableDeputy are documented here. Versions follow
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may carry
breaking changes).

## [Unreleased]

### Changed

- **#416 + #428 — CapDep no longer content-filters image generation, prompt OR
  output.** CapDep governs effects and information flow structurally and is
  silent by design on content (docs/governance-scope.md). Both the
  `CAPDEP_IMAGE_PROMPT_FILTER` (prompt-rejection) and `CAPDEP_IMAGE_SAFETY`
  (output NSFW/safety checker) dials are no longer posture-forced and default
  **off**: prompts pass to the image model unmodified and the model's real
  output comes through unfiltered. CapDep reports the model's actual result,
  including the model's own refusal (#417). This reverses the v0.58 `#330`
  image-safety default. Both dials remain off-by-default, opt-in operator
  controls. The now-vestigial `#330` posture scaffolding
  (`Posture.image_filters`, `_apply_image_safety_floor`) is retained inert
  pending removal.

## [0.58.0] - 2026-07-18

Real assistant capabilities and a safe default surface (milestone #28), shipped
**scoped**: the de-stub and safe-surface work that is real and verified in-repo.
Integrations whose acceptance requires live credentials/accounts are deferred to
v0.59 rather than faked; Microsoft 365 / Notion are marked not-supported-for-v1.0.
This mirrors the v0.54–v0.57 pattern of shipping honest, verified increments.

### Shipped and verified

- **#330 image-safety default** — `Posture.image_filters`
  (`forced_on` | `default_on_optout_ok`, no "off"); strict/high-security force
  the safety floor on at image-subprocess spawn, low-friction defaults it on
  with opt-out; enforced in `policy/image_safety.py` +
  `daemon/lifecycle._apply_image_safety_floor`. Detector fails open (limitation
  on record).
- **#327 zero-config safe surface** — `capdep init` auto-wires the default
  assistant surface (fs/memory/git/fetch/search + sandbox-if-podman) into
  `~/.config/capabledeputy/daemon.yaml`; `--no-assistant-surface` opts out.
- **#326 native web.fetch** — de-stubbed to a real bounded, SSRF-guarded
  HTTP(S) fetch behind the unchanged contract (`tools/native/web.py`); WebMock
  kept as the offline test override. Egress floor survives (keys on
  kind + target + labels).
- **#324 email SEND** — real SMTP delivery (`tools/native/email_delivery.py`)
  behind the unchanged `email.send` contract; inert record-only without
  `CAPDEP_SMTP_*` config, honest `delivered` flag.
- **#325 tasks slice** — native task list de-stubbed to a SQLite-backed
  persistent store on the shared `state.db` (additive table, lazy connect;
  `TaskStore()` still `:memory:` for back-compat). Survives daemon restart.

### Marked "not supported yet" for v1.0 (#329, closed)

- **Microsoft 365 / Notion** curated configs carry a `⚠️ NOT CONNECTABLE AS
  SHIPPED` marker (placeholder `example.*` endpoints) and, for M365, a
  `disabled_kinds: [SEND_EMAIL]` send guard matching Gmail's. Sourcing +
  live-verifying real Graph/Notion MCP endpoints is resource-gated and out of
  scope for v0.58.

### Deferred to v0.59 (credential/resource-gated, not faked)

- **#325 calendar + inbox** — real Google upstream infra exists; acceptance
  ("read today's *real* calendar/inbox") needs live Google OAuth.
- **#328 promote Google Workspace + GitHub to first-class** — guided-connect UX
  is in-repo; acceptance ("connect *real* accounts") needs a Google account and
  a GitHub token.
- **#319 Swift/CapDepMac reconnect tail** — CLI/TUI reconnect shipped in v0.57;
  the Swift client third needs an Xcode build to verify.

## [0.57.0] - 2026-07-17

Daemon reliability, supervision, and data safety (milestone #27). Makes the
daemon a dependable background service.

- **#318 supervised auto-restart** — `capdep service`, launchd
  `KeepAlive={SuccessfulExit:false}` + systemd `Restart=on-failure`. On restart,
  `App.startup()` → `graph.load()` rehydrates sessions for real continuity.
- **#319 graceful mid-session reconnect** — reconnect-aware `_call`/`_rpc` with
  AMBIENT-vs-SEND budgets (`ipc/reconnect.py`) for CLI and TUI. The
  Swift/CapDepMac client reconnect remains open (needs Xcode).
- **#320 timeouts + circuit-breaking** on hung tool/LLM calls.
- **#321 non-destructive state-DB lifecycle** — snapshot / quarantine / migrate
  replaces the silent wipe-on-mismatch.
- **#322 `capdep doctor`** — unified end-to-end health check.
- **#323 live telemetry** — dependency-free structured JSON logging
  (`observability/structured_log.py`, `log_event`), gauges
  (`approval.queue_depth`, `upstream.servers_healthy/total`), and file-export
  OTLP metrics (`observability/otlp_metrics.py`). Live OTLP network push is
  deliberately deferred to avoid adding a network dependency to the TCB.

Research spikes resolved (docs/spike-31{2,4,5,6,7}-*.md): #312 de-stub
build-vs-wire strategy, #314 daemon supervision mechanism, #315 DB
migration/backup, #316 Podman opportunistic sandbox, #317 image-safety posture.

## [0.56.0] - 2026-07-17

Security posture profiles, policy-conformance harness, and the unified
policy-authoring stack (milestone #26 + the policy-authoring epic #377).

- **Posture profiles (#304/#305)** — named security-posture manifests (strict /
  high-security-useful / low-friction-practical) composed from the existing
  dials (clearance, envelope, risk-preference, flow-pattern default,
  decision-inspectors, retention), with the three shipped presets.
- **Conformance harness (#306/#307)** — floor-invariance fuzz harness proving
  every profile and inspector preserves the structural DENY floors, plus an
  operator requirement DSL (`policy/requirements.py`) enforced at daemon start.
- **Policy authoring, Phases 1–3 (#378–#389)** — one unified when→outcome
  grammar and schema-driven compiler (`policy/authoring.py`), a single
  precedence lattice (`policy/precedence.py`, purpose may only tighten posture),
  format-agnostic loaders, layered defaults, and operator ergonomics:
  `capdep policy check`, `capdep policy why` (offline explain), and the mutation
  CLI (`capdep posture use` / `rule add` / `label add`). Legacy per-file loaders
  are kept as the incremental adapter path; a unified `configs/capdep.yaml`
  overlays at daemon start when present.

## [0.55.0] - 2026-07-14

Reachable safe-handling flow patterns (milestone #25). Makes Patterns 3
(reference/handle routing) and 5 (sealed sandbox) reachable in the default
config so restricted-tier data (financial/health) can be *used* without being
exposed to the planner or refusing the turn — fixing the usefulness cliff while
shrinking the exfil surface. Includes CaMeL dual-LLM quarantine (#302),
projection-only quarantine (#359), Podman sealed sandbox (#299), and sandbox
first-run handling (#361).

## [0.54.0] - 2026-07-13

Egress-complete chokepoint (milestone #24). Structural exfiltration closure:
routes every outbound effect through the single information-flow chokepoint so
information-flow taint blocks egress of data a session has read, with no
remaining bypass path.

## [0.53.0] - 2026-07-08

Stable release for natural web search, daily-driver workflow validation and
policy defaults, security assurance proofing, measured model quality planning,
and updated MLX/MFLUX model candidates.

### Natural web search and GUI recovery

- Made read-only web/news search behave as a natural daily-driver workflow with
  standing search capability coverage, Kagi configuration guidance, provider
  readiness checks, and regression tests for streamed search tool calls.
- Hardened CapDepMac/foreground GUI sessions so model-authored slash-command
  recovery text is repaired into GUI-mediated recovery language instead of
  telling users to type terminal commands into chat.
- Added configured-MCP diagnostics to setup planning so failed upstream servers
  are surfaced directly with validation/log actions.

### Setup and development environment

- Standardized local Python development on uv and the repo-local `.venv`, added
  `scripts/bootstrap-dev-env.sh`, and made local daemon launch helpers put
  `.venv/bin` first on `PATH`.
- Resolved `uvx` upstream server commands through the project venv when
  available, so Kagi and other uvx-launched MCP servers work reliably from
  tmux, launchd, and GUI-owned daemon starts.
- Moved image setup onto uv-based commands while keeping `.venv-images` as an
  isolated runtime environment for large image dependencies.

### Model strategy

- Added `planner.quality.challenger` for
  `mlx-community/Qwen3.6-27B-OptiQ-4bit` as a candidate-only quality planner.
- Added `quality-flux2` and `quality-qwen` image benchmark profiles, backed by
  MFLUX `flux2-klein-4b` and `OsaurusAI/Qwen-Image-mflux-4bit`, while keeping
  Z-Image-Turbo as the interactive default.
- Updated model setup inventory, download planning, benchmark docs, image
  readiness metadata, and Hugging Face token-source reporting. SDXL/Pony remain
  explicit Diffusers fallback profiles, not silently promoted to MLX defaults.

### Security assurance and flow-pattern proof

- Implemented `20 Product — v0.50.0 — Security assurance and flow-pattern
  proof` with a side-effect-free assurance inventory covering
  reference-monitor totality, flow-pattern composition, label/source coverage,
  fake/dry-run substrate contracts, advisory warnings, and model-sidecar
  authority boundaries.
- Added audited `Decision.WARN` support as a non-blocking advisory outcome that
  renders in client warning styles and cannot weaken `DENY`,
  `OVERRIDE_REQUIRED`, or `REQUIRE_APPROVAL`.
- Added tests for policy-before-dispatch ordering, native tool policy metadata,
  model-sidecar non-authority, declassification scope, and WARN dispatch/audit
  behavior.

### Measured model runtime and retrieval quality

- Implemented `19 Product — v0.49.0 — Measured local model runtime and
  retrieval quality` with a side-effect-free model-quality plan, explicit
  reranker runtime status, deterministic retrieval fixtures, role benchmark
  cases, advisory guard annotations, and benchmark-backed promotion gates.
- Added `scripts/benchmark_model_quality.py` and surfaced measured-quality
  summaries through `capdep-setup models` so setup/client paths can distinguish
  available candidates from promoted defaults.
- Registered `reranker.default` as a separate cross-encoder runtime and
  `guard.sidecar` as an advisory model profile, preserving the rule that
  CapDep policy and approval engines remain authoritative.

## [0.48.0] - 2026-07-06

Stable release for Google account connection, consolidated setup automation,
native office automation skills, and native MLX/MFLUX model asset planning.

### Google account connection

- Added preset-first Gmail/Calendar/Drive setup flows with daemon-owned OAuth
  state and scoped permissions.
- Added live reload/unload where possible, redacted diagnostics, and CLI plus
  CapDepMac parity for Google setup state.
- Preserved advanced bring-your-own-client setup without moving OAuth authority
  into clients.

### Consolidated setup automation

- Consolidated one-time setup domains under `capdep-setup`, including assistant
  surfaces, IMAP/Workspace bootstrap, image/model setup, macOS daemon launch
  parity checks, sandbox prerequisites, and compatibility aliases.
- Added non-destructive setup tests that run against temp homes, fake runners,
  fake model caches, and dry-run plans.
- Kept daemon and client runtime paths focused on authority, readiness, policy,
  OAuth state, and user workflows.

### Native office automation skills

- Added bounded SKILL.md workflows and app-specific native automation adapters
  for Apple Mail, Pages, Numbers, Keynote, Microsoft Outlook, Word, and
  PowerPoint.
- Added office automation capabilities, labels, approval gates, setup
  diagnostics, config wiring, and fake-runner tests.
- Preserved the security boundary: no arbitrary AppleScript, VBA, macros, shell,
  or UI scripting is exposed as ambient authority.

### Native MLX/MFLUX model asset pipeline

- Added model asset inventory, conversion-aware `capdep-setup models`, model
  provenance manifests, readiness metadata, unsupported fallback handling, and
  documentation for native MLX/MFLUX model selection.
- Added a side-effect-free model experiment harness and recorded local
  experiments for xLAM and Qwen3Guard candidates.
- Confirmed that runtime defaults still require benchmark evidence before they
  change; the best guard-sidecar signal so far is
  `mlx-community/Qwen3Guard-Gen-0.6B-MLX`.

### Roadmap

- Closed and released GitHub milestones `15 Product — v0.45.0`,
  `16 Product — v0.46.0`, `17 Product — v0.47.0`, and
  `18 Product — v0.48.0`.
- Pruned stale remote-tracking branch state after confirming the only leftover
  non-main branch was a closed, obsolete Dependabot PR branch.

## [0.44.0] - 2026-07-04

Stable release for local media/model operations, CommonMark client rendering,
and SKILL.md interoperability.

### Skills interoperability

- Added Codex/Claude-style folder `SKILL.md` package imports alongside the
  existing flat Markdown skill format.
- Added explicit `guidance`, `tool`, and `hybrid` modes so imported skills are
  never treated as implicit operator or system authority.
- Exposed skill registry and diagnostics over daemon RPC and `capdep skill`
  CLI commands.
- Routed declared skill scripts only through `EXECUTE_SANDBOX` execution with
  audited isolation-region lifecycle events; host subprocess execution is
  refused.
- Added resource traversal, symlink escape, sandbox refusal, sandbox-only
  execution, handler audit, and client parity regression tests.

### CommonMark rendering

- Added a shared CommonMark capability contract, parser/sanitizer fixtures, and
  client parity metadata for rendered Markdown surfaces.
- Added terminal-safe CLI/TUI rendering behavior and MCP-control fallback
  metadata for lower-capability clients.
- Hardened CapDepMac rendering sanitizer behavior and documented supported
  CommonMark behavior across client surfaces.

### Local media and model operations

- Promoted daemon-owned local media/model operations for image generation,
  including profile selection, readiness checks, benchmark-informed defaults,
  progress/status reporting, cancellation, and queue recovery.
- Documented local model/image backend setup and deferred model capability
  findings.

### Roadmap

- Closed GitHub milestone `14 Product — v0.44.0 — Skills interoperability and
  sandboxed execution`.
- v0.42 and v0.43 product ladder work is included in this stable tag.

## [0.41.1] - 2026-07-03

Patch release for reproducible local image-generation dependency locking.

### Packaging

- Resolved the uv universal-lock conflict for the Apple Silicon MLX/MFLUX image
  stack by scoping supported uv lock environments to macOS arm64 and using a
  consistent `transformers>=5` requirement for the `images` extra.
- Regenerated `uv.lock` so the `charts` and `images` extras are represented in
  the lockfile and `uv lock --check` passes.

## [0.41.0] - 2026-07-03

CapDepMac reliability and safe scripting UX release.

### CapDepMac reliability

- Added queued/running/completed/failed prompt state handling so users can
  submit another prompt while an earlier turn is still pending.
- Hardened daemon event correlation so delayed, duplicate, malformed, and
  out-of-order events attach to the intended prompt/turn instead of corrupting
  the visible chat state.
- Improved conversation scrollback and recovered-session rendering so prior
  prompts, assistant responses, pending turns, and generated artifacts remain
  available after reconnects or unexpected daemon activity.

### Safe scripting assistant

- Added daemon-owned scripting workflow RPCs for practical plans, exact script
  artifacts, sandbox run evidence, and reviewed export artifacts.
- Added CLI, TUI, and CapDepMac surfaces that consume the shared daemon
  artifacts without moving authority into the clients.
- Added practical automation demos for batch file cleanup, batch photo
  processing, and document/spreadsheet transformations.
- Added regression coverage for sandbox availability, root escapes, restricted
  credential labels, exact approval binding, and async client state while a
  script run is pending.

### Image generation and session artifacts

- Persisted generated-image session artifacts so later turns in the same
  session can refer to generated images instead of losing them after render.
- Added a local MLX/MFLUX image-generation backend path with named profiles,
  LoRA configuration, local checkpoint fallback support, and serialized
  generation so concurrent requests do not compete for local GPU resources.
- Added image model benchmarking helpers and recorded deferred adult-model
  capability notes for future image-generation tuning.

### Roadmap

- Closed GitHub milestone `11 Product — v0.41.0 — CapDepMac reliability and
  safe scripting UX`; no open issues remain in the repository at release time.

## [0.25.0] - 2026-06-26

macOS chat, inline media, MCP control enrichment, and client parity release.

### Security

- Hardened stdio upstream MCP process spawning so long-lived upstream servers
  receive only a minimal process-bootstrap environment plus explicitly
  configured per-server/vault secrets, instead of inheriting the daemon's full
  environment.
- Added quarantined declassification schemas for forwardable email and public
  web facts that refuse validation when executable content, prompt injection,
  or embedded credentials are detected.
- Added a purpose-contamination residual audit event for allowed no-egress
  tool calls when a session already contains labels inadmissible for its
  current purpose.
- Updated the Python dependency lock to patched versions for Dependabot
  alerts covering `aiohttp`, `cryptography`, `pydantic-settings`, `PyJWT`,
  `pypdf`, `pytest`, `python-multipart`, and `starlette`.
- Removed the abandoned default `pytest-textual-snapshot` dev dependency so
  the dev dependency group can move to patched `pytest>=9.0.3`; the single
  visual snapshot test now skips unless a compatible snapshot fixture is
  installed explicitly.
- Updated CI to test the project's supported Python 3.14 baseline and refreshed
  stale workflow action majors so the dependency-security fixes can pass the
  full lint, format, type-check, and test gate.

### MCP compatibility and security integration

- Added `capdep mcp-control-server`, a daemon-control MCP client surface for
  Codex/Claude-style hosts to inspect sessions, approvals, setup status,
  audit/provenance data, and invoke policy-gated daemon tool calls.
- Expanded the MCP-control client to cover the daemon's automation-safe
  session, policy, approval-pattern, override, relationship-group, memory,
  demo, extract, devbox, and programmatic RPC surfaces while preserving daemon
  policy/approval/provenance enforcement.
- Added `outputSchema` propagation for CapDep MCP tools and upstream MCP tool
  wrappers.
- Changed session-bound MCP elicitation to approve existing daemon-queued
  approval objects instead of submitting MCP-specific approval requests.
- Marked admin MCP tools with local setup authority metadata and output
  schemas so hosts can distinguish them from normal session-bound tools.
- Strengthened upstream MCP resource reads to preserve content-level CapDep
  labels in addition to server-level inherent labels.
- Added a current MCP compatibility/security review documenting protocol
  coverage, default security posture for each MCP surface, and ARD as
  operator-curated discovery only.

### Client parity

- Added daemon-owned `session.turn.*` lifecycle RPCs for replayable streamed
  turns, heartbeat timeout cancellation, disconnect cancellation, and
  MCP-control/CLI integration.
- Switched the CLI Rich streaming path from broad audit tailing to
  turn-specific daemon events, with exact-turn Ctrl-C cancellation and a
  non-blocking approval-review key binding.
- Added terminal-width hardening for the CLI toolbar so wide glyphs and narrow
  terminals do not corrupt prompt-toolkit HTML rendering.
- Added daemon-enforced interactive workstream coordination surfaces across
  CLI, TUI, and MCP-control, including owner-only send/cancel behavior,
  lease-token release/renew semantics, admin takeover, release-by-client, and
  expired-lease sweeping.
- Added an executable client parity contract at `docs/client-parity.json` plus
  tests that fail when daemon RPC coverage changes without an explicit client
  parity decision.
- Added CLI commands for daemon app status, setup status, memory, provenance,
  policy explanation, real policy-gated `tool.call`, child sessions, turn
  cancel, session labels, enforcement mode, and first-use prompt controls.
- Added TUI live-supervision actions for selected-session pause/resume/abort,
  active-turn cancel, approval defer, and approval-group approve.
- Expanded the Swift macOS app model with daemon-backed memory, tool,
  override, session-child, session-label, enforcement, first-use, policy,
  relationship, approval-pattern, and tool-call wrappers.

### macOS chat, web search, and policy recovery

- Made the Swift GUI chat the primary surface with scrollable session
  history loaded from `session.get` instead of rendering only the active turn.
- Fixed streamed-turn reliability: daemon turn heartbeats now refresh on agent
  progress, interrupted turns emit accumulated `partial_content`, and each new
  LLM request resets streaming partials so MLX tool-call JSON does not prefix
  user-facing answers.
- Wired CapDepMac turn subscriptions with heartbeat acks, a longer turn timeout,
  and preservation of streamed text when an interrupted event arrives empty.
- Added `ChatContentFormatter` so assistant replies render markdown links and
  condense verbose numbered search catalogs into chat-style bullet sources.
- Routed web-search intent to Kagi when `kagi.kagi_search_fetch` is registered,
  hiding DuckDuckGo fallback tools that never use the configured Kagi API key.
- Tightened planner guidance for post-search replies: short prose summary plus
  optional Sources links, not raw hit dumps.
- Added a yellow **Capability needed** grant banner in chat when a filesystem
  (or other) tool is policy-denied with `/grant` recovery steps; approvals and
  capability grants remain distinct recovery paths.
- Added scrollable conversation history to the TUI console via `RichLog` and
  `format_session_history()`.
- Stripped leaked MLX `{"tool_calls":…}` prefixes from finalized assistant
  text before it is persisted or displayed.
- Added auto-opening **Allow Access** and **Approval** prompt windows in
  CapDepMac, with **Allow & try again** for capability grants and widened
  session-scoped `READ_FS` patterns from the GUI.
- Added rich chat rendering in CapDepMac: fenced code blocks with copy,
  markdown images (`AsyncImage`), and full-document markdown prose blocks.

### Inline media (terminal + MCP)

- Added `terminal_graphics` and `markdown_media` so `capdep chat`, the
  Textual console, and the inline TUI render trusted agent markdown images
  inline on Ghostty/kitty/iTerm2 via the kitty or iTerm graphics protocols.
- Added `mcp_server/media_results` so the MCP control client attaches
  `ImageContent` blocks and an optional `--- CapDep terminal view ---` text
  section to session/turn/tool results; graphics work on piped MCP subprocesses
  that inherit Ghostty/kitty `TERM_PROGRAM`.
- Documented that MCP hosts such as Grok/Codex do not currently forward
  terminal graphics escapes to the outer terminal — use CapDepMac or
  `capdep chat` for reliable inline images.

### Practical setup and daemon-owned settings

- Added Google Workspace SourcePort providers for Gmail, Drive, and Calendar
  canonical resource/destination IDs, plus explicit per-message email label
  composition that preserves server-level label floors.
- Renamed the active connector/settings milestone to `v0.27.0 — Practical
  setup + daemon-owned settings`.
- Adopted the onguard-client architecture: headless background workers are
  normal daemon clients, while the daemon owns policy, shared schedule/config/
  queue/event coordination, provenance, and audit.
- Added daemon-owned settings persistence with `settings.get` and
  `settings.update` RPCs, including audit events for settings changes.
- Added daemon config diagnostics with `config.validate` and
  `config.log_locations` RPCs for clients to surface setup failures and logs
  without reading config files directly.
- Wired CapDepMac settings toggles and Advanced diagnostics to daemon RPCs
  instead of local constants.

### Onguard coordination substrate

- Added persisted structured session origin metadata so human, queued,
  scheduled, and headless-client work can be distinguished in sessions and
  audit payloads.
- Added daemon-owned onguard registry, config, command queue, event/result, and
  schedule RPCs backed by the shared SQLite state database.
- Added onguard audit events and regression tests for durable client
  registration, config proposal/approval, queued command claiming/completion,
  event publication, schedule creation, and session-origin persistence.

### Testing

- Added focused regression coverage for quarantined schema refusal,
  purpose-contamination residual audit, CLI semantic styles, and multi-client
  workstream ownership behavior.
- Added an independent coverage ratchet for daemon groups, clients, MCP
  surfaces, bundled MCP servers, and native tools, with checked-in per-group
  floors and 85% near-term / 90% stretch targets.
- CI now writes `coverage.json` during pytest and runs the coverage ratchet so
  each surface can improve independently without hiding regressions in a
  repository-wide aggregate.
- Exposed settings and config diagnostics through CLI and MCP-control surfaces.
- Added daemon-owned setup remediation descriptors, connector status, runtime
  automation pause/screen-control request state, and source-binding edit/preview
  RPCs.
- Wired CapDepMac setup rows, account status rows, automation menu actions, and
  Trust source-binding editing through daemon RPCs instead of no-op buttons or
  local-only state.
- Added daemon enforcement for high-risk approvals when the Touch ID policy is
  enabled; CapDepMac performs local device authentication and passes a
  daemon-recognized strong-auth marker.
- Expanded MCP-control and the executable client parity manifest for connector,
  runtime-control, setup-action, and source-binding RPCs.

### Roadmap

- Closed v0.32 after landing daemon-owned workstream coordination and moved
  true streaming/disconnect/heartbeat work into v0.33 where it can be built on
  a cancellable turn lifecycle.
- Closed and reorganized v0.17 after shipping its concrete gap-hardening work;
  remaining source-labeling work is now tracked in v0.16 and future
  federation/formal-model work is in Backlog.
- Added a shared CLI semantic style module and retired the standalone v0.5
  palette issue.

## [0.24.0] - 2026-06-20

Connector setup, durable daemon memory, and local daemon lifecycle release.

### Daemon memory

- Made the daemon-owned labeled memory store durable in the SQLite state DB
  while preserving the existing policy-aware `memory.*` tool contract.
- `memory.delete` now removes durable entries through the store abstraction
  instead of reaching into an in-memory implementation detail.

### Daemon lifecycle

- Added default daemon idle shutdown after 60 seconds with no connected
  clients, configurable via `CAPDEP_IDLE_SHUTDOWN_SECONDS`.
- Added Swift GUI daemon supervision: the app connects to a live daemon when
  possible, otherwise uses the existing CLI stop/start lifecycle path to clear
  stale daemon state and start a fresh daemon.
- The Swift GUI now enforces a single running `CapDepMac` instance, starts
  daemon recovery on application launch, and skips macOS notification setup
  when run as an unbundled SwiftPM debug executable.

### Gmail MCP setup

- Added daemon-owned Gmail MCP OAuth setup RPCs. The daemon can now save the
  official Google Gmail MCP server config, store OAuth client values in
  mode-0600 files, run the browser OAuth flow, and report setup status to
  native clients.
- Wired the CapDepMac Accounts settings tab to configure and authorize Gmail
  OAuth through the daemon instead of keeping OAuth setup state in Swift.
- Added `capdep mcp-admin-server`, a separate local admin MCP surface for
  connector setup. It exposes Gmail OAuth status, OAuth client configuration,
  and browser authorization through daemon RPCs without expanding the normal
  session-bound MCP tool surface.

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
  replacement. Modal/Firecracker actuators remain deferred to v0.37+.)
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

[0.53.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.53.0
[0.48.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.48.0
[0.44.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.44.0
[0.41.1]: https://github.com/marctjones/capabledeputy/releases/tag/v0.41.1
[0.41.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.41.0
[0.25.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.25.0
[0.24.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.24.0
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
