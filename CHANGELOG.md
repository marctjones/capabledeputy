# Changelog

All notable changes to CapableDeputy are documented here. Versions follow
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may carry
breaking changes).

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

[0.14.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.14.0
[0.13.1]: https://github.com/marctjones/capabledeputy/releases/tag/v0.13.1
[0.13.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.13.0
