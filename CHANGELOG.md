# Changelog

All notable changes to CapableDeputy are documented here. Versions follow
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may carry
breaking changes).

## [Unreleased] — 0.14.0 (in development)

Work in progress on `003-labeling-framework`. Not yet released.

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

[0.13.1]: https://github.com/marctjones/capabledeputy/releases/tag/v0.13.1
[0.13.0]: https://github.com/marctjones/capabledeputy/releases/tag/v0.13.0
