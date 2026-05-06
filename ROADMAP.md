# CapableDeputy — Implementation Roadmap

This roadmap accompanies DESIGN.md. Phases are sequenced to land a usable v0.1 in approximately 14 weeks of focused solo work, with each phase producing a testable milestone. Phases assume the testing strategy described in §12 of DESIGN.md and the trace/observability model described in §9.

## v0.1 — Core Runtime

### Phase 0 — Foundations (1 week)
- Repository scaffold, Apache 2.0 license, code-of-conduct, contributing guide.
- CI: lint (ruff), type-check (pyright/mypy), test (pytest), coverage (>95% target on security-critical core).
- Daemon skeleton: Unix socket listener, JSON-RPC plumbing, no logic.
- CLI skeleton: `capdep daemon start/stop/status` and `capdep version`.

**Done when:** `capdep daemon start` listens on the socket; `capdep version` round-trips; CI green on all PRs.

### Phase 1 — Session Graph & Audit (1.5 weeks)
- `Session`, `SessionGraph` data model.
- Fork / pause / resume operations (no merge yet).
- SQLite persistence.
- Audit log writer (JSONL, append-only).
- **Full event taxonomy from DESIGN.md §9.2 wired in from day one** — including `llm.*`, `policy.*`, `label.*`, `capability.*`, `tool.*` event shapes, even though most emitters won't exist yet. Retrofitting this later is painful.
- `capdep session list/new/fork/pause/resume`.
- `capdep audit` and `capdep watch` minimal viable forms.
- **Property-based tests** for graph invariants (Hypothesis).

**Done when:** sessions persist across daemon restarts; fork/pause/resume work end-to-end; every operation produces audit events that conform to the trace schema.

### Phase 2 — Labels, Capabilities, Policy (1.5 weeks)
- Label + Capability data model (the 8-label MVP set from DESIGN.md §7).
- OPA bundle with the 5 conflict rules.
- Decision API: `policy.decide(session_state, action) → Allow | Deny | RequireApproval`.
- `capdep policy show/validate/test`.
- **Exhaustive test matrix** over label combinations × actions.

**Done when:** a CLI command can ask "would action X be allowed in a session with labels Y?" and get a deterministic, audited answer.

### Phase 3 — Labeled MCP Client + Tool Integration (2 weeks)
- Labeled MCP Client wrapping the official `mcp` SDK.
- **Integrate upstream MCP servers behind the wrapper** (per DESIGN.md §7.4): Filesystem, Fetch, Gmail, Google Calendar, Obsidian, GitHub. Vanilla servers, no forks — labels and policy live in the wrapper.
- **Write CapableDeputy-native MCP servers** only for labeled memory and the purchase-queue stub.
- Per-server label declaration in YAML config.
- Per-tool argument and result rules.
- `capdep tool list/show/test`.
- YAML-driven fakes for upstream servers used in CI to avoid hitting real APIs in tests.

**Done when:** a tool call through any wrapped server is correctly intercepted, gated, and labeled; results land in context with the expected label union; CI passes without external network access.

### Phase 4 — Turn-Level Mode + LLM Loop (1.5 weeks)
- LiteLLM integration, prompt caching for Anthropic.
- Turn-level inheritance mode: agent loop with label accumulation and gated tool dispatch.
- `capdep send <session> "<message>"`.
- LLM record/replay test infrastructure (cassettes).
- **End-to-end test**: a simple session that reads a labeled file and is correctly blocked from emailing.

**Done when:** a real LLM (replayed) drives a session, accumulates labels from tool results, and gets blocked at the correct egress attempt.

### Phase 5 — Approval System, TUI, and Trace Surface (2.5 weeks)
- `ApprovalRequest` data model and queue.
- Pattern rules with strict pattern validation.
- Textual TUI: five-pane layout (Sessions / Conversation / Approvals / Trace / Events), approval modal with verbatim payload rendering, switchable session-list ↔ session-graph view.
- `capdep approval list/show/approve/deny/defer`.
- `capdep trace`, `capdep replay`, `capdep queue` CLI commands (DESIGN.md §9.3).
- Trace pane drill-down (DESIGN.md §9.4): context → prompt → response → parse → mode → policy → dispatch → result → label-diff.
- **End-to-end test**: prescription-to-wife scenario, demonstrating cross-session declassification.
- **End-to-end test**: replaying a captured trace against a modified policy and observing the decision change.

**Done when:** the prescription-to-wife scenario runs cleanly: blocked by policy, surfaced as an approval, approved through the TUI, executed via a one-shot capability, fully traceable via `capdep trace`. Replay against a tweaked policy produces the expected counterfactual decision.

### Phase 6 — Dual-LLM Mode (1.5 weeks)
- Quarantined LLM with schema-validated outputs (Pydantic).
- Mode dispatcher: detect when escalation is needed.
- Schema library: a starter set of declassifier schemas (DoseSummary, FinancialSummary, ContactInfo).
- **End-to-end test**: extract dose from PHI doc, send dose-only to approved recipient, verify PHI never reached planner context.

**Done when:** an extraction-style request triggers automatic mode escalation; the planner LLM's recorded context provably never contains the underlying labeled data.

### Phase 7 — Programmatic Mode (Starlark) (3 weeks)
- Fork `starlark-py`; extend Value with `labels`.
- Implement label propagation through binary ops, function calls, attribute access.
- Tool call resolution and gating in the interpreter.
- Static policy analyzer (rejects unconditional violations).
- `capdep run <prog.star>` and `capdep dry-run <prog.star>`.
- **End-to-end test**: multi-step labeled-data pipeline executed and audited.

**Done when:** a planner-emitted Starlark program executes with end-to-end label propagation, dry-run produces an accurate prediction, and the static analyzer catches a synthetic policy-violating program before execution.

### Phase 8 — Polish + Documentation (1 week)
- README with the canonical use cases.
- `docs/` site: getting started, threat model, policy authoring guide, MCP server authoring guide.
- Demo videos / asciicasts for each of the four canonical scenarios.
- v0.1 release tag.

**Done when:** a new user can install, configure, and run the prescription-to-wife scenario from documentation alone.

## Total timeline: ~14 weeks

Sequential, single-developer estimate. Faster with a collaborator on TUI + tool stubs while the core runtime work proceeds. Phases 0–5 are critical path; Phases 6–7 are independent and can swap order if Starlark interpreter work proves harder than expected. The extra week vs the original plan is investment in trace/observability — a security-essential feature, not a nice-to-have.

## Beyond v0.1

### v0.2 — Ecosystem and Privacy
- **`SKILL.md` adapter** for ingesting OpenClaw skills as labeled MCP tools.
- **Local-model planner option** — keep the privileged LLM local (Ollama, llama.cpp), only send non-labeled handles to a frontier model.
- **Approval pattern library** — shareable, version-controlled approval pattern rules for common workflows.
- **Engagement with OpenClaw RFC #39160** — propose CapableDeputy as the answer.

### v0.3 — Multi-tenancy and Federation
- Per-user label spaces for household deployments.
- Inter-host federation: phone + laptop with shared session state and remote approvals.
- Hardware token integration for high-stakes approvals.

### v0.4+ — Formal Methods
- TLA+ specification of session graph and policy semantics.
- Mechanized proofs of key safety properties (label monotonicity, capability unforgeability).
- Independent security audit.
