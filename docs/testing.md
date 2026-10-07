# Testing Strategy

CapDep tests are grouped by what they prove and by how deterministic they are.
Default CI should stay deterministic and local; opt-in tiers can exercise
platform UI, real models, or real external MCP servers.

## Background native email workflow

Run the supervised email workflow without taking keyboard, mouse, or window
focus on the Mac:

```bash
.venv/bin/python scripts/test-email-mvp-gui.py --artifacts /tmp/capdep-email-gui-acceptance
```

Use a fresh artifact directory for each run. The runner builds a DEBUG test app
with a unique bundle identity, preferences, socket and database. It starts a
real daemon with synthetic IMAP, model responses and SMTP; it never contacts a
real account or restarts the user's app. macOS Accessibility permission is
required for the runner's host application.
Runs are serialized by a host lock; executable names are unique as well, so
System Events cannot confuse two test processes with the same name.

The test uses Accessibility value changes and button actions on the production
SwiftUI views. `CAPDEP_GUI_BACKGROUND_TEST=1` replaces window presentation with
nonactivating panels that cannot become key or main windows. The DEBUG entry
point launches with `open -g`; notifications and normal window-opening callbacks
are suppressed in this mode. The driver never synthesizes keyboard/mouse events
or activates/raises the app, and checks foreground state after every operation.
There is no focus-stealing fallback. Release builds ignore the test mode.

Evidence includes native Accessibility snapshots, daemon logs, independent fake
SMTP recipient/content/count observations, a binary digest and `result.json`.
Select `--smtp-outcome failed_before_submission` or `--smtp-outcome outcome_unknown`
to exercise honest failure receipts. `--skip-build` reuses the current DEBUG
binary. The runner checks persisted status after daemon restart, then restarts
only its test app and checks the restored transcript and source-session receipt
with no repeated submission. DEBUG restart setup selects the daemon-authorized
session through the production `focusSession` method; the subsequent checks use
Accessibility. This validates native view/actions and daemon integration; normal
window activation, real-provider delivery and daily use remain separate checks.

Approval identities survive daemon restarts in the state database's companion
`.approval-ids.sqlite3` allocator and `.identity` marker. Back up and restore
both together with daemon state. Pending reviews themselves expire on restart
and require a new review. Missing, corrupt or mismatched allocator storage
fails closed; deleting both files to reset numbering is unsupported recovery.

## One-command profiles

Use the repository wrapper when validating a change locally or in an automated
runner:

```bash
scripts/test-capdep.sh core
```

`core` is the default and mirrors the Python CI gate: lint, formatting,
type-checking, the deterministic pytest suite, and the coverage ratchet. It
uses isolated test state and must not contact accounts, start the user's daemon,
or load a model.

The wrapper uses `/tmp/capdep-uv-cache` by default so it also works from
restricted IDE or sandbox environments. Set `CAPDEP_UV_CACHE_DIR` to choose a
different writable cache location.

On a macOS build host, add the Swift package tests and unsigned app-shell build:

```bash
scripts/test-capdep.sh macos
```

For an intentional daily-driver smoke on a configured workstation, use:

```bash
scripts/test-capdep.sh live
```

`live` first completes both deterministic profiles, then runs `capdep doctor`,
checks daily-driver readiness, starts the host-owned LaunchAgent if needed, and
runs `scripts/verify-gui-parity.py`. The parity probe has a real local-model
chat assertion but performs no irreversible account action. Keep real-account
mutation coverage separately opt-in and explicitly approved.

For the tmux development launcher, CapDep automatically uses
`~/Models/Qwen3-8B-4bit-mlx` when that standard cached baseline exists. Set
`CAPDEP_DAILY_MLX_MODEL` to an explicit already-cached path to override it;
otherwise the offline Hugging Face cache fallback is used. Neither path
downloads a model or enables cloud inference.

For the policy portion of daily-driver acceptance against an already-running
configured daemon, run:

```bash
.venv/bin/python scripts/verify-daily-driver-policy.py --output /tmp/capdep-policy-acceptance.json
```

The companion hermetic daemon lane exercises restricted-memory handle routing
over the Unix-socket RPC boundary without an account or model dependency:

```bash
.venv/bin/pytest -q tests/test_daily_driver_acceptance.py
```

It records daemon-projected policy traces for an inert allowed read, an
unauthorized email denial, a bounded standing grant that still requires focused
approval, an untrusted-taint egress denial, a real queued-turn cancellation,
and session/capability recovery across a daemon restart. The local-document
approval exercises a temporary file only; no external effect is executed.

To exercise the configured Google Workspace read-only account path, opt in
explicitly:

```bash
CAPDEP_GOOGLE_WORKSPACE_LIVE=1 .venv/bin/python scripts/verify-google-workspace-read-only.py
```

The verifier makes one bounded Gmail, Calendar, and Drive read through the
daemon, checks that capability-free sessions are denied, and validates labels,
provenance, and redacted audit commitments. It reports only structural
evidence; it does not print account content or perform mutations.

## Default deterministic tier

Run:

```bash
uv run pytest
```

This tier covers daemon policy behavior, stores, MCP protocol adapters,
workflow demos, client parity manifests, and pure client model logic. It must
not require network services, local GUI permissions, real credentials, or
Apple TCC grants.

## Live-daemon integration tier

Live-daemon tests start a real Unix-socket daemon with test state under
`tmp_path`, then drive client surfaces through daemon RPC. New tests should use
`tests/daemon_integration.py` instead of rebuilding handlers locally.

Current coverage includes:

- MCP-control onguard operations dispatching into daemon-owned registry,
  queue, schedule, and artifact state.
- Restricted-memory routing that exposes only an opaque reference handle over
  RPC, then records the post-decision bind in audit and provenance.
- A deterministic citable web-source fetch whose untrusted label blocks a
  subsequent otherwise-capable email egress attempt.
- Local document revision: the default scoped grant produces a focused
  approval request, while a separate exact destructive standing grant allows
  only the named file revision.
- Inbound-mail drafting: tainted inbox content may become a local draft, but
  the same session cannot send it to an external recipient.
- Task management: a task can be completed only through an exact, explicit
  destructive standing grant for that task identifier.
- CLI onguard read paths calling the daemon instead of duplicating store logic.
- TUI console/spectator live-daemon smoke tests and onguard coordination
  rendering.
- SwiftPM CapDepMac daemon-contract model tests for security context and
  onguard coordination payloads.

These tests are deterministic and should remain part of default CI unless they
become slow or platform-sensitive.

## macOS GUI-sensitive tier

Swift GUI and local automation tests that depend on app launch, Apple Events,
screen capture, Touch ID, Full Disk Access, or Automation permissions belong in
a macOS-sensitive tier. They should prove daemon contract usage through
model/action methods where possible and reserve brittle UI automation for
launcher, single-instance, recovery, and permission-flow smoke tests.

These tests should never carry core safety logic. If a GUI test needs to assert
policy, approvals, labels, provenance, or settings persistence, assert the
daemon RPC result and only use the GUI as a caller.

Run the Swift package tests with source coverage:

```bash
cd apps/macos/CapDep
swift test --enable-code-coverage
cd ../../..
.venv/bin/python scripts/swift_coverage_summary.py --limit 20
```

Run the opt-in GUI interaction smoke when Accessibility automation is available
for the terminal/Codex host:

```bash
.venv/bin/python scripts/test_capdepmac_gui_interactions.py
```

By default this smoke launches CapDepMac through the supported local launcher in
background mode, injects a prompt through the app's opt-in
`CAPDEP_GUI_TEST_COMMAND_FILE` hook, and verifies that the real app process
submitted the prompt and completed a daemon turn by reading
`~/Library/Logs/CapDep/chat-trace.log`. This does not require the app to take
keyboard focus. On failure it writes text artifacts under
`/tmp/capdepmac-gui-smoke-artifacts/`, including the command file, recent chat
trace tail, process list, and error detail.

Run the queueing smoke to prove prompts entered before earlier results return
are accepted and completed through the app process:

```bash
.venv/bin/python scripts/test_capdepmac_gui_interactions.py --multi-prompt
```

Run the generated-image GUI smoke when the local image backend is ready and you
want to prove the completed GUI trace contains image markdown:

```bash
.venv/bin/python scripts/test_capdepmac_gui_interactions.py --generated-image
```

Use `--no-wait-response` for a faster launch/submit-only diagnostic when you
are debugging app startup rather than turn completion.

Use the keyboard driver for a focus-taking human-input smoke:

```bash
.venv/bin/python scripts/test_capdepmac_gui_interactions.py --driver keyboard
```

Both modes are macOS-sensitive smokes, not default CI gates.

On some SwiftUI/macOS combinations, System Events can see the CapDepMac window
but not child control identifiers. In that case the smoke warns about AX hooks,
uses keyboard input against the focused chat window, and verifies the prompt via
`~/Library/Logs/CapDep/chat-trace.log`. Use `--require-ax-hooks` when explicitly
testing AX selector visibility with `--driver keyboard`.

## External MCP smoke tier

Real upstream MCP server tests are opt-in because they may require installed
servers, network access, OAuth credentials, account state, or vendor service
availability. They should validate compatibility and operator setup, not
replace deterministic conformance tests.

External MCP smoke should prove:

- server startup and tool/resource discovery;
- fail-closed classification for unknown or ambiguous tools;
- label/provenance propagation for resources and tool outputs;
- daemon policy/approval routing for actions.

Use `docs/external-mcp-smoke.md` for the opt-in matrix driven by
`CAPDEP_REAL_MCP_SMOKE_CONFIG`.

## Coverage Ratchet

Coverage is ratcheted independently for daemon code, clients, MCP surfaces,
bundled MCP servers, and tools. The near-term target is 85% per group and the
stretch target is 90%, but the enforced rule is non-regression from the
checked-in baseline. New feature work should add tests in the narrowest group
that owns the behavior.

Use the ratchet after a full deterministic test run:

```bash
uv run pytest
uv run python scripts/coverage_ratchet.py
```

Use the coverage matrix when deciding where to add tests next. It reports every
Python package or module from `coverage.json`, so broad areas such as CLI, TUI,
MCP, daemon, policy, and upstream code are not hidden by a repo-wide average:

```bash
uv run python scripts/coverage_matrix.py --scope package --fail-under 85
uv run python scripts/coverage_matrix.py --scope module --fail-under 85 --limit 40
```

The matrix is diagnostic. The ratchet is the CI gate: update it only after a
clean full run and only when the update preserves or raises the checked-in
floor, unless an explicit release note explains a deliberate reset.
