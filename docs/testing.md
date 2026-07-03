# Testing Strategy

CapDep tests are grouped by what they prove and by how deterministic they are.
Default CI should stay deterministic and local; opt-in tiers can exercise
platform UI, real models, or real external MCP servers.

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
submitted the prompt by reading `~/Library/Logs/CapDep/chat-trace.log`. This
does not require the app to take keyboard focus.

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
