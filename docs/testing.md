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

## Coverage Ratchet

Coverage is ratcheted independently for daemon code, clients, MCP surfaces,
bundled MCP servers, and tools. The near-term target is 85% per group and the
stretch target is 90%, but the enforced rule is non-regression from the
checked-in baseline. New feature work should add tests in the narrowest group
that owns the behavior.
