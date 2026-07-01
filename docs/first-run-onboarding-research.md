# v0.34 first-run onboarding research

Last refreshed: 2026-07-01

This note closes #141 by translating peer-agent onboarding patterns into the
CapDep v0.34 setup surface. The product constraint is unchanged: clients may
render setup state and request daemon-approved recovery actions, but clients do
not become authority for connector state, OAuth credentials, labels, policy,
tool admission, model selection, provenance, approvals, or audit.

## Peer patterns

| Product | Pattern to keep | CapDep decision |
|---|---|---|
| Claude Code | The setup docs separate install, verification, authentication, update health, and doctor-style recovery. Authentication starts by running the CLI and following browser prompts. Source: <https://code.claude.com/docs/en/setup>. | Keep an explicit readiness ladder and map doctor-style checks to `setup.check`, `setup.status`, and `config.validate`. Browser OAuth starts from daemon-owned action descriptors rather than ambient client config. |
| Codex CLI | The CLI docs present install, run, and first-run sign-in as the primary path; Codex operates on the selected local directory. Source: <https://developers.openai.com/codex/cli>. | Keep CLI setup commands first-class (`setup-plan`, `setup-check`, `setup-status`, `setup-run-action`) and make the working context explicit through SourcePorts in v0.35 rather than implicit client scraping in v0.34. |
| goose | The Desktop and CLI share provider/model/extension configuration; first use prompts for provider setup; `goose configure` updates providers and extensions. Source: <https://goose-docs.ai/docs/getting-started/installation/>. | Keep cross-client consistency, but invert authority: CapDep clients share daemon state, not a mutable client-side config file. Model role/mode is resolved by daemon events and rendered by clients. |
| goose extensions | Extensions are MCP-based, can be toggled from UI/CLI, and external extensions are checked before activation; goose also documents that autonomous file/command actions require access-control attention. Source: <https://goose-docs.ai/docs/getting-started/using-extensions/>. | Defer extension admission to v0.36. v0.34 can surface connectors and bundled tools, but arbitrary MCP enablement must wait for daemon admission, classification, conformance tests, and approval state. |
| OpenHands | The quickstart offers a low-friction Agent Canvas path using npm or Docker, plus a no-install cloud path. Source: <https://docs.openhands.dev/overview/quickstart>. | Keep a "first useful workflow" path. For CapDep that is the daemon-owned `morning-briefing` template after setup gates pass, not a free-form agent canvas with ambient authority. |
| Desktop agents | Useful desktop agents make account, model, app-permission, and first-task readiness visible before the user asks for consequential work. | CapDepMac should show setup rows, connector cards, local model mode, and recovery buttons, but every button resolves through `setup.run_action` or another daemon RPC. |

## CapDep setup stages

1. Daemon reachable: `setup.plan` starts from a live daemon connection and
   reports daemon status before any connector-specific work.
2. Model readiness: `setup.plan` reports planner availability and local-model
   fallback details; turn events expose resolved role/mode.
3. Policy and purpose readiness: setup refuses to imply workflow readiness when
   policy context is unavailable.
4. Connector credentials: `connector.status` reports Google service state as
   `missing_credentials`, `reauth_needed`, `restart_needed`, or `connected`.
5. Recovery actions: rows expose daemon-provided `id`, `label`, `kind`,
   `enabled`, and optional RPC params; clients render these without inventing
   alternate commands.
6. Local app permissions: macOS TCC and notification rows are manual or
   warning states with visible recovery actions where available.
7. Trust/source bindings: setup points users to daemon-owned source-binding
   surfaces, not client-local YAML edits.
8. First workflow: `workflow.templates` and `setup.plan.first_workflow` identify
   the safe morning briefing as the first validation path.

## RPC contract

The v0.34 setup contract is intentionally small:

- `setup.plan`: ordered steps, checks, summary, and first-workflow readiness.
- `setup.check`: CI/smoke-friendly readiness summary and blocking steps.
- `setup.status`: same check rows as `setup.plan.checks`.
- `setup.run_action`: resolves a setup action id into a safe descriptor.
- `connector.status`: connector cards with daemon-provided state and recovery
  actions.
- `workflow.templates`: bounded workflow catalog, including the first useful
  briefing template.
- `setup.google.*`: daemon-owned OAuth configure/login/revoke/status methods.
- `config.validate`, `config.log_locations`, `source_binding.*`, and
  `runtime.*`: supporting operator actions that remain behind daemon handlers.

## Client surfaces

| Client | v0.34 behavior |
|---|---|
| CLI | `capdep setup-plan`, `setup-check`, `setup-status`, and `setup-run-action` call daemon RPCs and print daemon-provided detail/action labels. |
| TUI | `SetupAssistantScreen` calls `setup.plan`, `setup.check`, and `setup.status`, renders daemon labels, and resolves fixes through `setup.run_action`. |
| CapDepMac | `SetupAssistantView`, dashboard setup rows, connector rows, Google OAuth wizard, and model controls consume daemon state and render resolved action labels. |
| MCP-control | Control tools expose setup/status/check/run-action/template/connector RPCs without duplicating readiness logic. |

## Non-goals

- No client-owned OAuth truth or direct secret storage.
- No client-local setup heuristics that disagree with daemon checks.
- No arbitrary MCP extension enablement in v0.34.
- No default autonomous writes, shell commands, or app automation as an
  onboarding shortcut.
- No generated-image/file rendering path trusted unless it comes from
  daemon-mediated artifact events and policy capability checks.

## Closeout evidence

- `tests/test_setup_plan.py` covers ordered setup plans, matching status rows,
  missing-model blockers, and first workflow readiness.
- `tests/test_setup_control_handlers.py` and
  `tests/test_setup_oauth_recovery.py` cover safe recovery descriptors and
  connector states.
- `tests/test_setup_morning_briefing_smoke.py` covers setup-to-briefing smoke.
- `tests/test_client_parity.py` covers setup RPC coverage across clients and
  guards daemon-provided setup action labels.
- Rich media and model routing are covered by the v0.34 focused tests named in
  `ROADMAP.md`.
