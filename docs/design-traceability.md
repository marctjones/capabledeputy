# Design Traceability

**Last refreshed:** 2026-09-05

This file is the working index from active design contracts to the code and
tests that currently enforce them. It is intentionally narrow: it tracks the
contracts that define the daemon-first security architecture and MVP assurance
surface.

Use it as the change checklist:

1. If you change a contract, update this file.
2. If you add a new active contract, add a row here.
3. If a row has no linked test, the design is documented but not yet enforced.

## Core contracts

| Contract | Spec | Primary code | Primary tests |
|---|---|---|---|
| Pure deterministic `decide()` | [specs/003-labeling-framework/contracts/policy_engine.md](../specs/003-labeling-framework/contracts/policy_engine.md) | [src/capabledeputy/policy/engine.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/policy/engine.py:1) | [tests/test_policy_engine.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_policy_engine.py:1), [tests/policy/test_decide_pure_function.py](/Users/marc/Documents/GitHub/capabledeputy/tests/policy/test_decide_pure_function.py:1) |
| Tool-definition fail-closed validation | [specs/003-labeling-framework/contracts/tool_definition.md](../specs/003-labeling-framework/contracts/tool_definition.md) | [src/capabledeputy/tools/descriptors.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/tools/descriptors.py:1), [src/capabledeputy/tools/registry.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/tools/registry.py:1) | [tests/test_tool_definition_validation.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_tool_definition_validation.py:1) |
| Pattern ③ reference handles | [specs/003-labeling-framework/contracts/reference_handle.md](../specs/003-labeling-framework/contracts/reference_handle.md) | [src/capabledeputy/patterns/reference_handle.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/patterns/reference_handle.py:1), [src/capabledeputy/tools/client.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/tools/client.py:1), [src/capabledeputy/mode/dispatcher.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/mode/dispatcher.py:1) | [tests/patterns/test_reference_handle.py](/Users/marc/Documents/GitHub/capabledeputy/tests/patterns/test_reference_handle.py:1), [tests/test_pattern3_redirection_resistance.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_pattern3_redirection_resistance.py:1) |
| Delegation attenuation + cascade | [specs/002-capability-delegation-chains/contracts/delegation.md](../specs/002-capability-delegation-chains/contracts/delegation.md) | [src/capabledeputy/session/graph.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/session/graph.py:1), [src/capabledeputy/policy/engine.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/policy/engine.py:1) | [tests/test_cascade_revocation.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_cascade_revocation.py:1), [tests/test_delegation_e2e.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_delegation_e2e.py:1) |
| Daemon-first client boundary | [docs/architecture.md](architecture.md) | [src/capabledeputy/daemon/server.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/daemon/server.py:1), [src/capabledeputy/ipc/client.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/ipc/client.py:1), [src/capabledeputy/app.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/app.py:1) | [tests/test_client_parity.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_client_parity.py:1), [tests/test_client_integration_parity.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_client_integration_parity.py:1), [tests/daemon_integration.py](/Users/marc/Documents/GitHub/capabledeputy/tests/daemon_integration.py:1) |
| AppleScript UI scripting surface (ships in prod, owner decision) | [docs/adr-0001-applescript-ui-scripting.md](adr-0001-applescript-ui-scripting.md) | [apps/macos/CapDep/Sources/AppleScriptSupport.swift](/Users/marc/Documents/GitHub/capabledeputy/apps/macos/CapDep/Sources/AppleScriptSupport.swift:1), [apps/macos/CapDep/CapDep.sdef](/Users/marc/Documents/GitHub/capabledeputy/apps/macos/CapDep/CapDep.sdef:1) | [apps/macos/CapDep/Tests/AppleScriptSupportTests.swift](/Users/marc/Documents/GitHub/capabledeputy/apps/macos/CapDep/Tests/AppleScriptSupportTests.swift:1), GUI-sensitive smoke [apps/macos/CapDep/scripts/verify-applescript.sh](/Users/marc/Documents/GitHub/capabledeputy/apps/macos/CapDep/scripts/verify-applescript.sh:1) |
| Restricted-tier mode floor | [docs/llm-flow-patterns.md](llm-flow-patterns.md), [specs/003-labeling-framework/contracts/reference_handle.md](../specs/003-labeling-framework/contracts/reference_handle.md) | [src/capabledeputy/mode/dispatcher.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/mode/dispatcher.py:1), [src/capabledeputy/session/graph.py](/Users/marc/Documents/GitHub/capabledeputy/src/capabledeputy/session/graph.py:1) | [tests/test_mode_dispatcher.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_mode_dispatcher.py:1), [tests/test_security_alignment_probes.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_security_alignment_probes.py:1) |

## Assurance plans

| Plan | Source | Execution surface |
|---|---|---|
| Workflow coverage matrix | [docs/workflow-plan.md](workflow-plan.md) | [tests/test_workflow_pressure.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_workflow_pressure.py:1), [tests/test_security_alignment_probes.py](/Users/marc/Documents/GitHub/capabledeputy/tests/test_security_alignment_probes.py:1), [demos/scenarios/run_all.py](/Users/marc/Documents/GitHub/capabledeputy/demos/scenarios/run_all.py:1) |
| Test-tier policy | [docs/testing.md](testing.md) | deterministic `pytest`, live-daemon, GUI-sensitive, external-MCP smoke tiers |

## Outstanding traceability gaps

- `tests/test_reference_monitor_totality.py` now probes no-authority denial across all registered native tools and statically checks direct `.handler` calls. This is a regression tripwire, not a proof covering dynamic aliases or arbitrary upstream code.
- The same test file rejects client imports of daemon/store/dispatch implementations, with explicit existing lifecycle and pure-helper exceptions. Override commands no longer have a local store fallback. Dynamic imports and all possible client-side effects remain outside this static check.
- Some older RFC/spec files remain useful history but are not clearly marked as active vs. historical design authority.


## Stabilization contracts

| Contract | Implementation | Evidence |
|---|---|---|
| Override authority stays in daemon during outage | `cli/override_cmd.py` | `tests/test_override_cli.py` covers all five commands unavailable |
| Bounded MLX residency and cooperative cancellation | `llm/mlx_client.py` | `tests/test_mlx_client.py` covers weights release and generation stop |
| Host owns GUI daemon lifecycle by default | `DaemonSupervisor.swift` | `DaemonSupervisorTests.swift` |
| GUI parity requires bounded RPCs and exact successful chat | `scripts/verify-gui-parity.py` | live execution recorded separately |
