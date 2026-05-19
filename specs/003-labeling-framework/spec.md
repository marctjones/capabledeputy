# Feature Specification: Labeling Framework

**Feature Branch**: `003-labeling-framework`
**Created**: 2026-05-19
**Status**: Draft
**Input**: User description: reconciled from `docs/design-v0.9-labeling.md` (incl. its authoritative Reconciliation section), `docs/trust-model.md`, `docs/governance-scope.md`, `docs/security-models.md`.

## Clarifications

### Session 2026-05-19

- Q: Legacy 8-value label data — in-place migration or forward-only cutover? → A: Forward-only; legacy data treated as most-restrictive until re-labeled (no in-place rewrite). Resolved in FR-024.
- Q: Does v0.9 build a data-category assignment mechanism, or consume categories assigned upstream? → A: Consume — v0.9 builds the registry, resolution layer, and decision function only; categories arrive from source declaration / curated+admission-controlled MCP / human declaration, plus an optional raise-only inspector. No trusted content classifier is built. Resolved in FR-022, FR-025.
- Q: How do multiple matching rule sources compose? → A: Baseline + bounded relax — resolution sets a strictest-across-sources baseline; a human-authored decision rule may relax it only within explicit human-declared bounds; hard floors (prohibited, admissibility exclusion, max-tier clearance, integrity floor) are non-relaxable. Resolved in FR-026.
- Q: Are the five tiers a strict total order for all comparisons? → A: Yes — none < sensitive < regulated < restricted < prohibited, totally ordered; "most-restrictive" = max tier; clearance/read-up compare on this order; prohibited is the maximum and terminal. Resolved in FR-027.
- Q: What form does the mandatory risk-id take? → A: A single in-repo risk register; each entry has a stable internal id mapping to ≥1 external framework reference; labels/decisions cite the internal id. Resolved in FR-015, FR-028 + Risk Register Entry entity.
- Q: How is context-expectedness determined deterministically? → A: An action is "expected" iff it matches an operator-registered expectation binding (initiator + effect + optional time window/parameters); non-match = "anomalous". Deterministic, human-declared, AI-read-only. Resolved in FR-029 + Expectation Binding entity.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Orthogonal labels with deterministic sensitivity resolution (Priority: P1)

The current single label set conflates *what data is* (sensitivity), *where it came from* (provenance), and *what an action does* (effect). The operator needs these as independent axes so a datum's sensitivity can be resolved by context instead of guessed from a flat enum, while enforcement stays deterministic and outside model control.

**Why this priority**: This is the structural core. Every later story (decision context, purpose exclusion, robustness) depends on having orthogonal axes and an engine-side resolution layer. On its own it already delivers value: correct, explainable sensitivity decisions per data category and context profile, replacing the lossy 8-value enum.

**Independent Test**: Configure two context profiles (e.g., a clinician use-case and a general use-case) and a data category (`health`); assert the same datum resolves to different tiers per profile, that the resolution is a pure function of logged inputs (same inputs → same tier, reproducibly), and that no model call participates in the resolution.

**Acceptance Scenarios**:

1. **Given** a datum tagged data-category `health` and a context profile whose `(health, user, use-case, purpose)` maps to `regulated`, **When** the engine resolves sensitivity, **Then** the tier is `regulated`, recorded with the inputs that produced it, and reproducible.
2. **Given** distinct categories `health` and `financial`, **When** both are present in a session, **Then** they remain distinct labels (no collapsing) unless a human-authored policy rule explicitly relates them.
3. **Given** a data category whose resolution mode is `fixed-high`, **When** any profile is applied, **Then** the tier cannot be lowered by any profile.
4. **Given** identical resolution inputs, **When** resolution runs on different occasions, **Then** the outcome and its recorded rationale are byte-identical (deterministic, no model self-narration).

---

### User Story 2 - Decision-context axis and the multi-axis never-auto rule (Priority: P2)

The operator needs the outcome of an action to depend not only on data sensitivity but on *who initiated it and whether they were authenticated*, *the counterparty/relationship*, *whether the context is expected*, and *how reversible the effect is* — evaluated by a human-authored rule, with a safe default when no rule matches.

**Why this priority**: Sensitivity alone cannot distinguish "the backup cron you configured at 2am" from "an unauthenticated email asking for the same effect." This story makes the decision faithful to real risk and encodes the never-auto default. It builds on US1's axes.

**Independent Test**: With no matching human-authored rule, assert every consequential action resolves to `suggest` or `deny` (never `auto`); add a human-authored rule keyed on the decision-context axis and assert the same action now resolves as the rule specifies.

**Acceptance Scenarios**:

1. **Given** an action with no matching human-authored rule, **When** the engine decides, **Then** the outcome is `suggest` or `deny`, never `auto`.
2. **Given** a human-authored rule for `(initiator=cron-configured-by-principal, effect=backup, expectedness=matches-configured-job)`, **When** that exact context occurs, **Then** the outcome is `auto`; **When** the same effect arrives from an unauthenticated inbound trigger, **Then** the outcome is `deny`.
3. **Given** an effect declared as low-reversibility / high-loss, **When** decided, **Then** the outcome escalates at least to `require-approval` regardless of initiator, unless a human-authored rule explicitly relaxes it over human-declared recoverability.
4. **Given** the AI proposes a new rule, **When** it is not yet human-ratified, **Then** it has no effect on decisions.

---

### User Story 3 - Purpose-scoped category admissibility (Priority: P3)

The operator needs to guarantee that some data categories never enter a session whose purpose has no legitimate use for them (e.g., health data must not reach an employee-evaluation session), enforced structurally at session spawn rather than by asking the model to ignore it.

**Why this priority**: This closes the inappropriate-context-use class that tainted-context flow tracking provably cannot. It depends on US1 categories and the purpose dimension.

**Independent Test**: Declare `health ⊄ inputs(employee-evaluation)`; spawn an `employee-evaluation` session and assert it holds no capability that could read `health`-category data, and that any attempt to grant or delegate one is refused deterministically.

**Acceptance Scenarios**:

1. **Given** a human-declared admissibility rule excluding `health` from purpose `employee-evaluation`, **When** an `employee-evaluation` session is spawned, **Then** it is created without any capability whose scope could read `health` data.
2. **Given** such a session, **When** a delegation or grant would introduce read access to an inadmissible category, **Then** the request is refused and audited.
3. **Given** a category that *is* admissible for the purpose, **When** the session is spawned, **Then** access follows the normal sensitivity-resolution path (admissibility does not over-restrict admissible categories).

---

### User Story 4 - Robustness, traceability, and assurance deltas (Priority: P4)

The operator and reviewers need every label and decision to be risk-traceable and auditable: a risk-id on each, threshold-crossing allows captured as explicit residual-risk exceptions, a terminal tier no approval can unlock, control-plane operations unreachable from untrusted-tainted sessions, and reversibility-weighted gating (with a social-commitment effect class) replacing the binary destructive-op gate.

**Why this priority**: These convert the model from "plausible" to defensible/auditable against named frameworks. They layer onto US1–US3 without changing their core behavior.

**Independent Test**: Verify each label carries a non-empty risk-id; force a threshold-crossing allow and assert an exception object is recorded; attempt to unlock the terminal tier via approval and assert it is impossible; attempt a control-plane operation from an untrusted-tainted session and assert refusal.

**Acceptance Scenarios**:

1. **Given** any label or decision, **When** inspected, **Then** it carries at least one framework risk-id; an orphan label (no risk-id) fails validation.
2. **Given** an action whose decision crosses a configured risk threshold but is allowed, **When** decided, **Then** an auditable exception object (residual-risk acceptance) is recorded.
3. **Given** data resolved to the terminal `prohibited` tier, **When** any approval is attempted, **Then** no approval path can unlock it.
4. **Given** a session carrying untrusted provenance, **When** it attempts a label/capability/profile/audit (control-plane) operation, **Then** the operation is refused.
5. **Given** an effect that is a third-party social commitment, **When** decided, **Then** it is gated as reputationally irreversible even if its mechanical effect looks recoverable.

---

### User Story 5 - Model-fidelity targets: clearance, integrity floor, sealed effect (Priority: P5)

The operator needs the resolution layer to carry a per-profile maximum-tier clearance with read-up refusal (dynamic Bell-LaPadula), an integrity floor with no-read-down on the provenance axis (the Biba direction), and a sealed-effect handling path so the most restrictive data gets true noninterference rather than falling back to intransitive dual-LLM declassification.

**Why this priority**: These complete the faithful-model targets named in `security-models.md`. They are valuable but the system is already coherent without them; they are the last, most demanding increment.

**Independent Test**: Configure a profile with a max-tier clearance below a datum's resolved tier and assert read-up is refused; route `restricted`-tier data through the sealed path and assert the planner context provably never holds it.

**Acceptance Scenarios**:

1. **Given** a profile with max-tier clearance `regulated` and a datum resolving to `restricted`, **When** access is attempted, **Then** it is refused (no read-up).
2. **Given** an integrity-floored step, **When** an input below the floor (untrusted provenance) is presented, **Then** the step refuses it (no read-down within the step).
3. **Given** `restricted`-tier data and a sealed-effect path, **When** the workflow runs, **Then** the planner session provably never contains the raw data (asserted, not asked of the model).

---

### Edge Cases

- **Unclassifiable data**: a datum the assignment mechanism cannot confidently categorize MUST resolve to the most-restrictive applicable tier (fail-closed), never to an `unclassified`-permissive outcome.
- **Mislabeled data**: if a datum is assigned the wrong category, downstream protection is silently lost — the spec states this explicitly and the assignment mechanism MUST record provenance of how each label was assigned so the failure is at least auditable after the fact.
- **Conflicting context profiles** (per-user vs per-use-case disagree): resolution MUST take the more restrictive tier.
- **Registry extension collision**: an attempt to register a category whose name shadows a stable-core category MUST be refused.
- **Effect-class union for wrappers**: a wrapper skill that wraps tools of differing effect classes MUST inherit the union (most powerful) effect, never the minimum.
- **Profile/rule absence**: no profile and no rule for a category MUST yield the category's most-restrictive default, not a permissive fallthrough.
- **AI attempts to mint a read-only invariant** (trust edge, recoverability, admissibility, authentication): MUST be refused and audited as a control-plane violation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST represent labels on four independent axes — data category (A), provenance/integrity (B), effect class (C), decision context (D) — with no axis derivable from another.
- **FR-002**: Axis A MUST provide a frozen stable core of data categories plus an open registry for extensions; each category MUST carry separate Confidentiality and Integrity impact, a default tier, and a resolution mode (`fixed-high` | `context-up` | `context-resolved`).
- **FR-003**: Distinct sensitive categories MUST remain distinct; any collapsing/relating of categories MUST be an explicit human-authored policy rule, never a labeling-time behavior.
- **FR-004**: Axis B MUST be a monotone provenance lattice (`principal-direct` ≥ `system-internal` ≥ `external-untrusted`), mechanically derived, with exactly one audited sanctioned declassifier, and MUST support an integrity floor with no-read-down.
- **FR-005**: Axis C effect class MUST be declared on the tool/source (no runtime content classifier); a wrapper inherits the union of wrapped effects; tool-provenance is a recorded sub-attribute.
- **FR-006**: Axis D decision context (initiator+authentication, counterparty/relationship, context-expectedness, reversibility/recoverability) MUST be first-class, human-declared, and AI-read-only.
- **FR-029**: Context-expectedness MUST be determined deterministically as a match against operator-registered **expectation bindings** (initiator + effect + optional time window and/or parameter constraints). An action matching a binding is `expected`; any action not matching one is `anomalous`. Expectation bindings are human-declared and AI-read-only; no statistical/heuristic anomaly inference is permitted (Principle I).
- **FR-007**: A sensitivity-resolution layer MUST map `(data-category, user, use-case, purpose)` to a tier in `{none, sensitive, regulated, restricted, prohibited}` deterministically, engine-side, with no model participation.
- **FR-027**: The five tiers MUST form a strict total order `none < sensitive < regulated < restricted < prohibited`. "Most-restrictive" is the maximum under this order; all clearance and read-up comparisons (FR-008) and baseline composition (FR-026) MUST use it; `prohibited` is the order's maximum and terminal (FR-017).
- **FR-008**: Each context profile MUST carry a maximum-tier clearance; access to data resolving above the clearance MUST be refused (read-up refusal).
- **FR-009**: Purpose MUST additionally gate category *admissibility*: a human-declared rule may mark a category inadmissible as an input to a purpose; such categories MUST be excluded from a session of that purpose at spawn (no readable capability granted or delegable).
- **FR-010**: The action outcome MUST be a deterministic pure function over the cross-product of axes A–D, valued in `{auto, suggest, require-approval, deny}`.
- **FR-011**: Absent a matching human-authored rule, the outcome MUST be `suggest` or `deny` — never `auto` (never-auto default).
- **FR-026**: Rule-source composition MUST be **baseline + bounded relax**: (a) the *baseline* outcome is the most-restrictive across all matching resolution sources (category default/resolution-mode, context profile, max-tier clearance, purpose-admissibility); (b) a human-authored decision rule MAY relax the baseline (e.g., `suggest`→`auto`) **only** within explicit human-declared bounds (e.g., human-declared recoverability, authenticated initiator); (c) the relaxation inputs MUST be human-declared, AI-read-only facts (FR-012) — the model may never supply or assert them; (d) the following are **hard floors no rule may cross**: the terminal `prohibited` tier (FR-017), purpose-admissibility exclusion (FR-009), per-profile max-tier clearance / read-up refusal (FR-008), and the provenance integrity floor (FR-004). A relaxation that would cross any hard floor MUST be refused and audited.
- **FR-012**: The trust/relationship graph, recoverability metadata, purpose-admissibility, and initiator authentication MUST be human-declared; the system MUST reject any attempt by the model to mint, widen, or assert them.
- **FR-013**: A derived/delegated label MUST inherit the most-restrictive value of any non-enumerated field.
- **FR-014**: Labels, profiles, and rules MUST be changeable only via an AI-suggests → human-ratifies → engine-applies path; an unratified suggestion MUST have zero effect on decisions.
- **FR-015**: Every label and every decision MUST cite at least one internal risk-register id; a label citing none MUST fail validation (the SC-001 orphan audit).
- **FR-028**: The system MUST maintain a single in-repo risk register; each entry has a stable internal id and maps to one or more external framework references (OWASP LLM/Agentic, MITRE ATLAS, NIST AI RMF, EU AI Act, FIPS 199, GDPR/HIPAA/PCI, FAIR). Labels and decisions reference the internal id, not raw external identifiers; an internal id MUST NOT exist with zero external references.
- **FR-016**: A decision that crosses a configured risk threshold but is allowed MUST produce an auditable residual-risk exception object.
- **FR-017**: The tier `prohibited` MUST be terminal — no approval, override, or escalation path may unlock it.
- **FR-018**: Label, capability, profile, and audit operations MUST be `ADMINISTER`-class and unreachable from any session carrying untrusted-tainted provenance (control-plane reflexivity).
- **FR-019**: Gating MUST be reversibility-weighted over human-declared recoverability, replacing the binary destructive-op gate, and MUST include a social-commitment effect class treated as reputationally irreversible.
- **FR-020**: The system MUST provide a sealed-effect path so that `restricted`-tier data can be handled without ever entering the planner session context (true noninterference, asserted structurally).
- **FR-021**: Every resolution (tier, admissibility, outcome) MUST be reproducible from logged inputs; model self-narrated reasoning MUST NOT be recorded as decision rationale.
- **FR-022**: Every labeled datum MUST carry an assignment-provenance record of how its category was assigned (source-declared, curated/admission-controlled MCP, human-declared, or raise-only inspector); an inspector may only raise (add taint), never clear it.
- **FR-023**: Unclassifiable or ambiguously classifiable data MUST resolve fail-closed to the most-restrictive applicable tier.
- **FR-025**: v0.9 MUST consume data-category assignments from existing upstream sources and MUST NOT build a trusted runtime content classifier or a category-inference service; the only assignment component v0.9 introduces is an optional raise-only inspector. The labeling oracle is bounded and made auditable (FR-022), not eliminated.
- **FR-024**: Migration is forward-only: existing data still carrying a legacy 8-value label MUST be treated as most-restrictive until it is re-labeled under the four-axis model. No in-place rewrite of existing session/store records is required; legacy state MUST never resolve to a more permissive outcome than the new model would give.

### Key Entities *(include if feature involves data)*

- **Data Category (Axis A)**: a named kind of data; attributes: stable-core|registered, C-impact, I-impact, default tier, resolution mode, ≥1 risk-id.
- **Provenance Level (Axis B)**: position in the integrity lattice; attribute: is-sanctioned-declassifier (exactly one).
- **Effect Class (Axis C)**: declared capability effect; attributes: effect kind, reversibility weight, social-commitment flag, tool-provenance.
- **Decision Context (Axis D)**: initiator+authentication, counterparty/relationship, expectedness, recoverability — all human-declared.
- **Expectation Binding**: operator-registered (initiator + effect + optional time window/parameters) defining what counts as `expected`; non-match ⇒ `anomalous` (FR-029).
- **Context Profile**: per-user and per-use-case mapping `(category,user,use-case,purpose)→tier`, plus max-tier clearance.
- **Admissibility Rule**: human-declared `(purpose, category) → inadmissible` exclusion.
- **Human-Authored Decision Rule**: predicate over axes A–D → outcome; carries risk-id; ratification state.
- **Risk Register Entry**: stable internal risk id + one-or-more external framework references; the single source labels/decisions cite (FR-028).
- **Residual-Risk Exception**: record of an allowed threshold-crossing decision (who/what/when/which risk).
- **Label-Assignment Record**: how/by-what a label was put on a datum (for oracle auditability).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of stable-core and registered labels cite ≥1 internal risk-register id, and 100% of risk-register entries carry ≥1 external framework reference (zero orphan labels, zero unmapped register entries) — verifiable by an automated registry audit.
- **SC-002**: Sensitivity/admissibility/outcome resolution is 100% reproducible: re-running any recorded decision from its logged inputs yields an identical outcome and rationale in an automated determinism check.
- **SC-003**: With an empty rule set, 0 consequential actions resolve to `auto` (100% land in suggest/require-approval/deny) — the never-auto default holds.
- **SC-004**: In an inappropriate-context test battery, 100% of attempts to bring an inadmissible category into a purpose-scoped session are refused at spawn or grant time.
- **SC-005**: 0 successful control-plane operations from untrusted-tainted sessions across the adversarial test suite.
- **SC-006**: 0 paths unlock the `prohibited` tier across the approval/escalation test suite.
- **SC-007**: Every threshold-crossing allow in the test suite produces exactly one residual-risk exception object (no silent threshold crossings).
- **SC-008**: 100% of `restricted`-tier sealed-path scenarios assert the planner context never held the raw data.
- **SC-009**: Every data category in the shipped core traces to a documented framework risk, and every named deliberate non-goal is absent from requirements (scope-honesty audit passes).

## Assumptions

- The stable core is approximately the ~12 categories enumerated in `docs/design-v0.9-labeling.md`; the exact frozen list is finalized during planning, pruning any category that cannot trace to a framework risk.
- "User" and "use-case" already exist as session/profile concepts; profiles layer on existing session context rather than introducing a new identity system.
- Data-category assignment is consumed from existing upstream sources (now a firm scope boundary — FR-025/FR-022 — not a soft assumption); the existing curated/admission-controlled MCP path (WI-1/WI-2) is the primary source for externally-ingested data.
- External substrate (sandbox, admission scanner) is leveraged only behind in-repo ports and is never part of the decision plane (Constitution VII).
- Deliberate non-goals (model accuracy/bias/eval/content-safety; privacy lawful-basis/consent/DSAR; substrate security) are out of scope by secure-by-reduction and are not requirements here.
- Enforcement remains LLM-isolated and deterministic (Constitution Principle I); this feature does not introduce any model call into resolution or decision.
- Spec'd on `main` consistent with 001/002; the `before_specify` git-feature hook is intentionally not executed per standing project decision.
