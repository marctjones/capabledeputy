---
description: "Spec 005 implementation plan — week-by-week sequencing for the 3-week terminal-MVP"
---

# Plan: Spec 005 Terminal Assistant MVP

## Technical context

- Language: Python 3.12 (consistent with 003 / 004)
- TUI: Textual (already in use under `src/capabledeputy/tui/`)
- LLM: LiteLLM (already wired) — operator picks provider
- MCP transport: stdio + HTTP via spec-004 U001-U012 (pulled forward)
- Storage: SQLite (already used for sessions + override grants)
- Platform target: Ubuntu 24.04 + Windows 11 WSL2

## Schedule

### Week 1 — Native tools + TUI polish foundation

Days 1-2: **`fs.*` native tools.** Implement read/write/create/modify/
delete with binding-canonicalized paths. Write-discipline verification
deferred until spec-004 ships `VersionedWritePort` providers — for MVP
the fs.write declares `default_reversibility={irreversible, external}`
unless the operator's binding pins it otherwise.

Days 3-4: **`web.search` native tool.** Operator-config-driven provider
selection (Brave default; DDG/SearXNG configurable). FR-023 binding
required. Returned URLs labeled `external-untrusted`.

Day 5: **TUI inspector + status bar.** Tool-call inspector pane that
listens on the audit event stream and renders the `PolicyDecision`
fields inline. Status bar with profile + dial + purpose.

### Week 2 — MCP adapter + TUI refusal/override + 2 reference servers

Days 6-7: **Generic MCP adapter** (spec-004 U001-U012). Both transports;
mapping-file loader; ToolDefinition factory; fail-closed registration;
audit events; daemon wire-in.

Day 8: **Anthropic filesystem MCP** integration. Mapping file + fixture
+ test. Operator can list/read/write files via the upstream server, all
gated by CD.

Day 9: **Anthropic brave-search MCP** integration. Same shape; gives
the operator a vetted search backend alternative to native web.search.

Day 10: **TUI refusal-explainer + override-launch.** When a dispatch
denies, surface the rule + reason; offer inline `request override`
that drives the existing `capdep override` flow.

### Week 3 — Rituals + onboarding + polish

Days 11-12: **Ritual data model + RPC.** A Ritual is a frozen sequence
of approved tool calls with operator-ratified T012 declarations. Store
in SQLite. Add `capdep ritual save / run / list / show / refuse`
subcommands. Daemon handlers parallel to the override ones.

Day 13: **TUI command palette + ritual launcher.** Ctrl-P / `:` for
quick actions; Ctrl-R lists rituals and launches the selected one
through `engine.decide()` per step.

Day 14: **First-run wizard.** `capdep init` walks operator through LLM
selection, profile selection, dial selection. Detects Ollama. Refuses
to complete without each required field.

Day 15: **Polish + onboarding doc + tag `v1.0.0-terminal-mvp`.** Final
lint/format/typecheck/pytest; demo screencast; README quickstart.

## Dependencies

- Week 2 MCP adapter blocks the two reference-server integrations
  (within week 2).
- TUI refusal-explainer (day 10) depends on the inspector (day 5).
- Ritual TUI launcher (day 13) depends on ritual data model (days 11-12).
- Everything else is internally serial.

## Parallel opportunities

- Week 1 days 1-2 (fs.\*) and days 3-4 (web.search) could parallelize
  across two engineers if available.
- Week 2 days 8-9 (the two MCP servers) parallel after day 7.
- TUI work (days 5, 10, 13) can parallelize with the tool work in the
  same week.

## Risk register

- **R1: TUI polish drifts in scope.** Mitigation: hard-cap the TUI work
  at the named widgets (inspector / refusal / status / command palette
  / memory view). Anything more is post-MVP.
- **R2: Ollama integration friction on Windows WSL2.** Mitigation:
  cover this in the first-run wizard; document the WSL2 networking
  setup.
- **R3: MCP adapter abstraction surprise.** Mitigation: implement
  against stdio first (Anthropic filesystem is stdio); add HTTP only
  if Anthropic brave-search requires it.
- **R4: Rituals collide with FR-031 asymmetry.** Mitigation: ritual
  files MUST be authored on disk and ratified by operator signature
  (or, for MVP, a `--ratified-by` CLI flag); rituals authored at
  runtime by the AI are structurally impossible (no API exists).
- **R5: First-run wizard becomes its own product.** Mitigation: hard-
  cap at 7 prompts. No GUI, no fancy progress bars. CLI questions.

## Out-of-scope reminders

These are post-MVP per `spec.md`:

- Container sandbox providers (spec-004 phase 4).
- OTLP/Splunk sinks (spec-004 phase 5).
- WebAuthn/Duo/OAuth2.1 (spec-004 phase 6).
- Tier-1 MCP servers beyond Anthropic filesystem + brave-search.
- DefenseClaw plugin (spec-004 phase 8).
- Messaging channels.
- Native-Windows IPC.

## Definition of done

- All FR-200..FR-211 implemented + tested.
- All SC-200..SC-206 verifiable in CI.
- ruff + ruff format + pyright + pytest green.
- Demo screencast: fresh Ubuntu VM, `capdep init`, interactive chat
  exercising memory.\* + fs.\* + web.search + a Ritual + an override.
- README quickstart updated for the terminal-MVP path.
- Tag `v1.0.0-terminal-mvp`.
