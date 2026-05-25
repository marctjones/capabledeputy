# Research: Labeling Framework (003) — Design Decisions & Rationale

**Status**: Phase 0 output. The spec carries 0 `NEEDS CLARIFICATION` markers (resolved in `spec.md` §Clarifications). This file records the **design choices** the implementation will make for each non-trivial 003 mechanism, with rationale and alternatives — so the data-model and contracts in Phase 1 inherit a clean set of decisions.

---

## D1. Persistent storage of orthogonal axes (FR-045)

**Decision**: SCHEMA_VERSION 5 → 6. Replace the single `sessions.label_set TEXT` blob with **four structured columns** (one per axis): `axis_a` (JSON list of `{category, tier, risk_ids}`), `axis_b` (JSON list of provenance levels with integrity floor), `axis_d` (JSON object of decision-context attributes — note Axis C lives on `capability_set[].kind`/tool def, not on the Session). Add a `purpose_handle TEXT` column (FR-046). Migration is **forward-only** (FR-024): legacy `label_set` rows are read by a one-time converter that maps each legacy enum value into its axis-correct slot at the most-restrictive position; the legacy column is retained read-only for one schema cycle for audit, then dropped at v7.

**Rationale**: A structured-per-axis shape is the only persistence that makes axis orthogonality *observable* in the store (Principle VIII / FR-045 reviewability), and lets the resolver consume each axis without re-parsing prefixed strings. JSON-in-column reuses the existing columnar/JSON pattern (`history`, `declassification_log`, etc.) — no new dependency.

**Alternatives considered**:
- *Keep flat `label_set`, encode axis via prefixes* — rejected: technically encodes the same info but fails the FR-045 reviewability check; "orthogonality" is then a naming convention, not a structural property.
- *Separate normalized tables for each axis* — rejected for v0.9: adds joins and migration complexity for a single-tenant single-machine store; revisit if multi-tenant federation needs cross-axis querying at scale.

---

## D2. Risk Register storage and external-reference linkage (FR-015/028)

**Decision**: A single in-repo JSON file at `configs/risk_register.json` (operator-editable, human-declared, AI-read-only) holding entries `{id, summary, framework_refs[]}`. Labels and decisions cite `id`. A new `risk_register` table is **not** introduced — the source of truth is the file (operator-editable; CI lints it for zero orphan refs); the daemon loads it at startup and caches.

**Rationale**: Operators edit risks in source control alongside other curated configs (matches the existing `configs/curated/*.yaml` pattern); no per-row dynamism is needed. A CI lint enforcing SC-001 (every label cites ≥1 register id; every register id has ≥1 external ref) is straightforward against a JSON file.

**Alternatives considered**:
- *Store in SQLite* — rejected: the risk register is configuration, not session state; co-locating with sessions invites mutation by the wrong actor.
- *Inline external IDs on labels directly* — rejected per Q4: brittle, no one-to-many.

---

## D3. Source/Location Label Binding resolver design (FR-043/048)

**Decision**: An in-TCB resolver (`policy/bindings.py`) holding an operator-declared, ordered list of `(scope_pattern, label_set, write_discipline?)` rules loaded from `configs/source_bindings.yaml`. Scope patterns are normalized to a canonical URI form: `file:///abs/path/**`, `unc:///host/share/**`, `https://site/...`, `mcp:server/resource-id`. The resolver canonicalizes incoming resource handles via per-scheme canonicalizers (path → realpath; UNC → normalized; SharePoint drive-item → site URL + item id; symlink resolved). Most-specific subtree binding wins for category/tier; bindings compose **most-restrictive** when they overlap on disjoint dimensions; unbound or non-canonicalizable input fail-closes (FR-023, FR-043). The resolver is consulted on **every read/ingest** by the adapter layer and on **every write/egress** to match destination-id (FR-048).

**Rationale**: A single in-TCB resolver is the only way to keep the binding-application deterministic and replayable; YAML config keeps it operator-legible and version-controllable.

**Alternatives considered**:
- *Per-adapter binding application* — rejected: adapters live outside the TCB (Constitution VII); placing binding decisions there scatters the labeling oracle.
- *Glob → regex compile per call* — rejected for cold-path latency; precompile at config load.

---

## D4. Reference Handle (Pattern ③) plumbing (FR-047)

**Decision**: A `ReferenceHandle` is an opaque, per-session, unforgeable token (`UUID4`) issued by the runtime when a labeled value is fetched. The planner only ever sees the token; the deterministic runtime maintains an in-memory `handle_id → bound_value + labels` map private to the session. **Bind sites are explicit and contracted**: a tool whose `ToolDefinition` declares `accepts_handles=true` may receive a handle as an argument; at dispatch, `decide()` resolves the handle to its labels (NOT its value) and applies the egress/effect rules; only after the gate passes does the runtime substitute the bound value into the tool call. **Where-secret-landed provenance** is recorded as an audit event `pattern3.handle_bind` with `{handle_id, destination_canonical_id, tool, audit_id}` per insertion. Pattern ③ is **required** for `restricted`-tier (FR-047): a `restricted` session at spawn checks that all tools it might use accept handles or run sealed (⑤); else fail-closed.

**Rationale**: Reuses existing per-session unforgeable tokens (v0.3) and programmatic variable-binding building blocks; the "controlled re-insertion" is the dispatch-time substitution; the where-it-landed provenance is the new audit event linking handle to destination.

**Alternatives considered**:
- *Always sealed (⑤) for `restricted`* — rejected as too restrictive for v0.9 (forces every restricted action through 004 substrate); ③ covers the broader use-case where the work just needs *routing*, not transformation.
- *Capability-shaped handles* — rejected: capabilities are authority-bearing tokens; handles are *data-shaped* references — different semantics, must not be conflated.

---

## D5. Write-discipline verification (FR-044)

**Decision**: A `VersionedWritePort` interface (`substrate/version_write_port.py`, port only — impl in spec 004) defines `write(path, content) → WriteResult{prior_version_handle, post_state_hash, attestation}`. The runtime accepts the write as `version-preserving` only if `prior_version_handle is not None` **and** an immediate read of `prior_version_handle` returns content matching the pre-write state hash. The check is in-TCB; the actuator (provider tool) supplies the materials but the verification is ours.

**Rationale**: FR-044's "verify, not trust" is exactly the WI-1 fail-closed-adapter philosophy applied to a different effect class. The actuator is replaceable behind the port; the verification is the TCB's responsibility.

**Alternatives considered**:
- *Trust adapter's claim* — rejected: violates FR-044 explicitly and creates an adapter-can-lie path.
- *Read-modify-compare after the write* — partial; we still need the prior-version handle to be a stable retrievable reference, so the port returns one.

---

## D6. Override Grant vs ordinary approval data model (FR-038)

**Decision**: A new `OverrideGrant` table (separate from the existing approval store): `{id, session_id, action_kind, target, target_category_tier, hard_floor_crossed, invoker_principal, attester_principal, override_policy_at_grant, friction_level, audit_id, expires_at}`. Capabilities issued by an Override Grant set `origin = override_granted` and carry `override_grant_id`. The decision record (audit event) explicitly labels which mechanism allowed the action: `approval` for in-envelope `require-approval`, `override` for any floor crossing. `decide()` returns a distinct outcome variant `OverrideRequired{floor, policy}` rather than collapsing into `require-approval`.

**Rationale**: Keeping them distinct in the data model is what makes SC-014 (Override Policy honored 100%) testable and what makes the audit unambiguous about *why* a hard floor was crossed.

**Alternatives considered**:
- *Single "approval" table with a severity column* — rejected: muddies the audit and risks downstream code treating an override as a normal approval.

---

## D7. ToolDefinition extension (FR-005, EXECUTE tiering FR-042)

**Decision**: Extend `ToolDefinition` with: `effect_class: EffectClass` (enum: `OBSERVE | FETCH | MUTATE_LOCAL | DESTROY | COMMUNICATE | TRANSACT | EXECUTE.sandbox | EXECUTE.host | EXECUTE.remote | EXECUTE.deploy | ADMINISTER | ACTUATE_PHYSICAL`), `default_reversibility: ReversibilityLabel`, `default_mutability_target_facets: MutabilityLabel`, `social_commitment: bool`, `tool_provenance: ToolProvenance`, `accepts_handles: bool` (pattern ③), `surfaces_destination_id: bool` (FR-048 contract attestation). A `ToolDefinition` missing any required field fails registry validation at daemon startup (fail-closed, Principle VI).

**Rationale**: Tools are the declaration site for Axis C and for the FR-048 destination-id contract; centralizing this on the existing `ToolDefinition` keeps the surface narrow.

**Alternatives considered**:
- *Inferring effect class from tool name / behavior* — rejected: that's the "trust the model / runtime classifier" anti-pattern. Declarations must be human-authored.

---

## D8. Purpose Handle registry + admissibility (FR-046, FR-009)

**Decision**: An operator-declared YAML at `configs/purposes.yaml`: `{purpose_id, label, admissible_categories?, inadmissible_categories?, default_isolation_posture?, recommended_pattern?}`. Sessions are spawned with `purpose_handle` set; the resolver checks admissibility *before granting any capability* and refuses (fail-closed) if the session would hold any capability with read access to an inadmissible category. `intent` (free text) is retained as a human-readable annotation.

**Rationale**: Structured purpose is required for FR-009 at-spawn enforcement; YAML matches the curated-config pattern.

**Alternatives considered**:
- *Derive purpose from intent text via LLM* — rejected (Principle I).

---

## D9. select_mode extension for ③/⑤ and `restricted` (FR-047 / FR-040)

**Decision**: `select_mode` is extended to include `REFERENCE` (③) and `SEALED` (⑤) as additional return values. Precedence chain: `force_mode` → `prefer_programmatic` → **tier-driven floor**: effective tier `restricted` ⇒ require `REFERENCE` (if any session tool declares `accepts_handles=true`) or `SEALED` (if SandboxActuator is available, spec 004); if neither is available, **fail-closed at session spawn** (do not fall back to ②). Otherwise the existing auto-heuristic (confidential.* + quarantined extractor → DUAL_LLM, else TURN_LEVEL).

**Rationale**: This is the deterministic floor for the pattern-selection asymmetry argument: the model may suggest a pattern *above* the floor; selection at/below the floor is deterministic.

**Alternatives considered**:
- *Let ④ programmatic cover ③ cases* — rejected: ④ requires emitting a program; ③ is data-blind orchestration where the planner needs to route but not author code.

---

## D10. Outcome Envelope + Risk-Preference Profile storage (FR-030)

**Decision**: Envelopes live in `configs/envelopes.yaml`: list of `{cell_key: (category, effect, decision_context, reversibility), strictest, loosest}`. The Risk-Preference Profile is a single value in `configs/risk_preference.json` (`cautious | balanced | permissive` plus version + signature) — owner-set, AI-read-only. The dial selects the outcome inside each cell's envelope; hard-floor cells have degenerate envelopes (single point).

**Rationale**: Configuration shape parallels purposes/bindings; the dial is a one-line setting precisely as the spec wants.

**Alternatives considered**:
- *Per-session dial* — rejected: the dial is an owner posture, not a session-time decision; per-session would invite the AI nudging it.

---

## D11. CI invariants and storage-shape audit (Principle III / FR-045 / Principle VI)

**Decision**: New `tests/invariants/test_storage_shape.py` asserts every `sessions` row populates the four axis fields (no flat-legacy rows post-migration). Existing `test_enforcement_llm_independence` is extended to cover `resolution.py`, `bindings.py`, `envelope.py`, `overrides.py`. A new `tests/invariants/test_failclosed.py` parametric over every new resolver path proves unmapped/non-canonicalizable inputs refuse, not best-effort-allow (Principle VI). Storage-shape audit also exposed as a CLI: `capdep audit storage-shape`.

**Rationale**: Operationalizes Principle VIII's reviewable-defect rule.

---

## D12. Risk-preference dial scope (Q1, 2026-05-25 — FR-030 / FR-046)

**Decision**: The dial is **bound to the Purpose Handle**, not global, not per-session. Each entry in the operator-declared Purpose registry (`configs/purposes.yaml`) carries its own `risk_preference_dial` value (one of `cautious | balanced | permissive`). Sessions inherit their purpose's dial value at spawn and on `fork`. A session MUST NOT mutate its own dial value at runtime — any change is a control-plane operation routed through the standard ratification path (FR-014).

**Rationale**: Aligns the operator's risk knob with the structural admissibility boundary (FR-009 / FR-046). A `daily-briefing` purpose can stay `permissive` while `tax-prep` is `cautious` without the operator swapping per session. Per-session would invite drift and AI-side nudging; global is too coarse. Inheriting on `fork` matches existing Session inheritance for caps + intent.

**Alternatives considered**:
- *Global single dial value* — too coarse; same dial for `daily-briefing` and `tax-prep` forces choosing the strict one and tolerating the friction always.
- *Per-session, set at spawn* — invites drift and the AI proposing dial changes mid-session; eliminated by the Principle-IV least-authority constraint.
- *Hierarchical (global → purpose → session)* — overengineered for v0.9; revisit only if real configurations show per-purpose is insufficient.

---

## D13. Override Grant default expiry (Q2, 2026-05-25 — FR-032)

**Decision**: Override Grant expiry is configurable per Override Policy entry; absent an explicit value the system defaults to **15 minutes**; the spec enforces an absolute **60-minute hard cap** that no Override Policy entry, even misconfigured, may exceed. Configuration values above 60 min are refused at policy authoring time (Principle VI fail-closed).

**Rationale**: 15 min is long enough for a routine ad-hoc admin task without forcing repeated re-justification (Principle V usability); short enough that an operator who walks away mid-task doesn't leave a bypass hot. Matches `sudo`'s 5-minute cache pattern but biased longer because capdep's Override Grants are explicitly less ambient (must be invoked per scope, not implicitly cached). The 60-min absolute cap prevents misconfiguration from yielding an all-day bypass.

**Alternatives considered**:
- *Global 60-second expiry* — too short; operators would re-justify every minute under interactive workflows.
- *Global 5-minute expiry* — usable but doesn't accommodate multi-step administrative tasks (e.g., correcting a registry mistake).
- *Configurable with no cap* — rejected: a single misconfigured policy entry could disable enforcement permanently, violating Principle VI fail-closed.

---

## D14. Ratification authorization model (Q3, 2026-05-25 — FR-014 / FR-036)

**Decision**: Ratification of suggested labels / profiles / rules reuses the **Override Policy state machine** (FR-036), valued in `{single-authorized | dual-control}` per affected severity. Hard-floor-touching ratifications default to `dual-control`; non-hard-floor ratifications default to `single-authorized`. The named role mapping is a **Ratification Authorization** entity, separate from (but using the same infrastructure as) **Override Authorization**; an operator MAY declare them identical or distinct. Invoker MUST NOT be the dual-control attester. The AI is NEVER authorized to ratify.

**Rationale**: Ratification is a control-plane operation (FR-018); reusing the existing severity-tiered authorization model means no new authorization vocabulary and operationalizes Principle V (deterministic state machine). Distinct entity (Ratification Authorization) preserves the operator's option to grant ratification authority to a different role than override authority (e.g., a team lead reviews rule additions; only the operator themselves overrides hard floors).

**Alternatives considered**:
- *Any-human-with-control-plane-access* — too permissive: a small team with several control-plane principals would have no separation-of-duty for hard-floor rule changes.
- *Dual-control always* — too heavy for routine adds (e.g., a benign new email-sender label); friction would push operators toward bypass-shaped behavior.
- *Dedicated `RatificationAuthority` capability with its own authorization vocabulary* — adds a new mechanism to learn and audit; reusing Override Policy keeps the operator's mental model unified.

---

## D15. Decision-latency target + monitoring (Q4, 2026-05-25 — SC-023)

**Decision**: Per-decision latency target is **p95 ≤ 50 ms / p99.9 ≤ 250 ms** for a single chokepoint dispatch (axes A-D evaluation + baseline-and-bounded-relax composition + rule lookup + capability match + audit emission). Measured against a standard rule-set fixture of ≥1k rules, ≥100 categories, ≥50 expectation bindings. When steady-state exceeds the target the engine MUST emit a `decision.latency_degraded` audit event so the operator sees the regression rather than discovering it via subjective sluggishness in chat.

Implementation:
- `tests/test_decision_latency.py` runs the standard fixture through `decide()` 10,000 times and asserts the p95 / p99.9 percentiles.
- A new audit event type `decision.latency_degraded` (added to `audit/events.py`) carries `{latency_ms, rule, fixture_size, threshold_crossed: "p95"|"p99.9"}`.
- A new lightweight in-process histogram (`policy/latency.py`) counts each dispatch; on every Nth dispatch the engine checks the recent window's p95 against the target and emits the event if exceeded.

**Rationale**: A latency budget keeps implementations honest and audits operator-visible degradation rather than letting it manifest as subjective chat sluggishness. The chosen budget (50 ms p95) matches today's measured behavior and leaves enough headroom for the new composition machinery (envelope dial, baseline-and-bounded-relax, source-binding canonicalization) without requiring premature optimization.

**Alternatives considered**:
- *p95 ≤ 10 ms* — aggressive; forces hashed indexes and complicates the rule-lookup code; not justified by interactive needs.
- *p95 ≤ 200 ms* — feels slow inside a tight REPL loop where many tool calls fire per turn; operator would notice.
- *Defer — no SC* — leaves the engine free to silently regress and discovery falls back to operator subjective complaints; rejected per Principle VIII (latency is an observable property).

---

## D16. Risk-threshold configuration scope (Q5, 2026-05-25 — FR-016 / FR-028)

**Decision**: Residual-risk thresholds (the value FR-016 references) are declared **per Risk Register Entry** (FR-028), not globally and not per-category. Each entry in `configs/risk_register.json` carries a `threshold` field scoped to its framework reference (FAIR magnitude band, NIST AI RMF impact tier, EU AI Act risk class, FIPS 199 category-impact, etc.). A decision crosses a threshold iff any cited risk-id's declared threshold is exceeded; the residual-risk exception object names the specific risk-id(s) crossed. A Risk Register Entry without a declared threshold (where the framework reference requires quantification) MUST be refused at registry validation.

Implementation:
- `policy/risk_register.py` loads `configs/risk_register.json` at daemon startup; caches the `id → {framework_refs[], threshold}` map.
- `decide()` consults the cache when emitting the residual-risk exception object (FR-016); the exception payload lists exactly which risk-ids' thresholds were crossed.
- A new CI lint (`tests/invariants/test_risk_register_thresholds.py`) refuses to ship a `risk_register.json` whose entries cite a quantification-required framework but omit `threshold`.

**Rationale**: Reuses the existing risk register (single in-repo file, operator-editable). Per-entry thresholds let the operator tune severity per framework dimension without an explosion of cells. The residual-risk exception object is more actionable when it cites the specific risk crossed — operator reads "FAIR-2 (financial loss > $10k)" instead of "some threshold crossed."

**Alternatives considered**:
- *Global single threshold* — too coarse; financial and health risks have different magnitudes.
- *Per axis-A data category* — misses cross-category risks (e.g., the OWASP LLM01 "prompt injection" risk isn't a category at all).
- *Per Outcome Envelope cell (FR-030 cell)* — highest fidelity, combinatorially heavy; operator config burden dominates the value.

---

## Out-of-Scope (spec 004) — recorded here so this plan is honest

- `SandboxActuator` implementation (FR-040 substrate); the **port** is in 003.
- Provider source adapters: filesystem MCP wrapping, Microsoft Graph (SharePoint/OneDrive/mapped drive), network shares — they implement the `SourcePort` contract from 003 but are 004's build.
- `VersionedWritePort` implementations (Graph version-history binding; local-FS .bak/versions strategy) — the **port + verification** is 003; the *implementations* are 004.
- Cross-session global conflict / aggregate-disclosure ledger (composition invariants #5/#6 in `docs/llm-flow-patterns.md`) — stays a documented review check until a dedicated spec picks it up.
