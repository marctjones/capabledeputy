# LLM Flow-Handling Patterns

**Purpose.** Applied, operational patterns for *how a planner LLM
session relates to labeled data*. This is the companion to
`docs/security-models.md`: that file is the formal-model yardstick
(model → mechanism → deviation); this file is the applied taxonomy of
the four ways the runtime lets (or forbids) the model to touch
sensitive information. Each pattern maps onto a model row there — it
does not introduce a new model.

The anti-prompt-injection strength rises down the list: the less the
planner can *see*, the less an injected instruction can *exfiltrate*.

## The four patterns

### 1. Tainted-context flow tracking — ✅ implemented (core, v0.1+)

The planner reads the raw labeled data into its context. The session
accumulates the data's labels; Brewer-Nash conflict rules then deny any
downstream egress that would leak it.

- **Planner sees:** the raw values.
- **Guarantee:** no *silent* egress — a tainted session is structurally
  blocked at the egress sink, regardless of what the model "decides."
- **Model lineage:** Denning dynamic information flow (`security-models.md`).
- **Use when:** the workflow genuinely needs the model to reason over
  the content and there is no onward egress, or egress is itself gated.

### 2. Quarantined-model declassification — ✅ implemented (v0.6)

A sacrificial, **tool-less** LLM reads the raw labeled data under a
fixed Pydantic schema; the planner LLM receives only the
schema-validated projection. The schema *is* the declassifier.

- **Planner sees:** only the structured, minimized fields — never the
  raw bytes.
- **Guarantee:** the planner's context provably never contains the
  labeled source (asserted in tests); smuggling is bounded by the
  schema's field types/lengths.
- **Model lineage:** noninterference *declassification* (intransitive
  NI) + privilege separation (the dual-LLM pattern).
- **Use when:** the planner needs *facts derived from* sensitive data,
  not the data itself (briefings, triage, summaries).

### 3. Reference / placeholder substitution (data-blind planning) — 🔶 rule first-class (spec 003 FR-047); impl pending

The planner manipulates only **opaque handles/placeholders**. The
deterministic runtime binds the real labeled value at a controlled
insertion point and records where it went; the model never holds the
value at any point.

- **Planner sees:** references only (`<doc#3>`, capability/tool
  aliases) — never the value, not even a projection.
- **Guarantee (target):** the planner is *structurally incapable* of
  leaking what it never holds; insertion is explicit, auditable, and
  the destination of each secret is tracked.
- **Status — honest:** the **rule is now first-class in spec 003**
  (FR-047) — pattern ③ is selectable by `select_mode` and is *required*
  for `restricted`-tier sessions (no fallback to ② intransitive
  declassification). The building blocks (per-session unforgeable
  tool/capability tokens v0.3, programmatic variable-binding) exist;
  **implementation pending** is the controlled-re-insertion bind point
  + the "where-the-secret-landed" destination provenance recorded per
  Reference Handle. Tracked through `/speckit-plan` for 003.
- **Model lineage:** object-capability handles; CaMeL ("defeat prompt
  injection by design").
- **Use when:** the planner must *route/orchestrate* sensitive data it
  has no need to read (forwarding, filing, moving between systems).

### 4. Code-mediated processing (programmatic mode / LLM-as-compiler) — ✅ implemented (v0.3)

The LLM emits a program (Python-AST subset), statically dry-run against
the policy *before* execution; the labeled data flows only through the
deterministic interpreter, never the model's context.

- **Planner sees:** never the data — only the task and the program it
  authors.
- **Guarantee:** processing is performed by audited deterministic code;
  the policy is checked statically pre-execution; the model is a
  compiler, not a data handler.
- **Model lineage:** language-based information flow; the interpreter
  half of CaMeL (`security-models.md`).
- **Use when:** the work is mechanical transformation/processing of
  sensitive data at volume.

### 5. Sealed-effect via disposable isolation — 🔶 design (rule: spec 003; impl: spec 004)

Deterministic code transforms labeled data **inside a disposable,
egress-free isolation region** (no network, no host mounts except
declared inputs). The planner never holds the raw data; it only
authors the task and receives the returned output. The region boundary
is simultaneously the **seal** (planner-blindness) and the
**reversibility** (rollback = discard the region), so a contained
pipeline — however long or data-heavy — runs optimistically with no
gate; the gate is placed only where an output crosses the boundary.

- **Planner sees:** the task and the output artifact — never the raw
  contained data.
- **Guarantee:** in-region effects are reversible by construction;
  autonomous execution is safe up to the boundary.
- **Model lineage:** Reference-Monitor assurance + intransitive-NI
  *declassification* at the boundary (`security-models.md`). It is the
  concrete realization of the previously-speculated "sealed-effect".
- **Critical non-property:** the region is **not** a declassifier
  (invariant #7 below).
- **Use when:** mechanical transform/aggregation of sensitive data at
  volume where the planner needs only the result.

## Meta-control: mode auto-escalation — ✅ implemented (v0.7)

Not a pattern but the **selector**. `select_mode(label_set, registry,
*, prefer_programmatic, force_mode)` (`src/capabledeputy/mode/
dispatcher.py`) is a **pure deterministic function** — it never calls
or consults the LLM (Principle I: the choice of protection mode is
itself outside the model). Precedence, strongest first:

1. **`force_mode`** — CLI `--mode` for that turn only.
2. **`prefer_programmatic`** — a session-level flag set at
   `session.new`; opts the whole session into pattern ④.
3. **Auto-heuristic** — if the session carries a `confidential.*`
   label **and** a `quarantined.*` extractor is registered → pattern ②
   (DUAL_LLM). Otherwise → pattern ① (TURN_LEVEL).

Precise mapping & honest caveats:
- The selector covers **① TURN_LEVEL / ② DUAL_LLM / ④ PROGRAMMATIC**
  only. Pattern ③ is **not** a selectable mode (still partial); ⑤ does
  not exist.
- Sensitivity auto-escalates to **② only — never to ④**. Pattern ④ is
  reached *exclusively* via `force_mode` or `prefer_programmatic`
  (opt-in), never chosen automatically from labels.
- Confidential labels **with no quarantined extractor registered** →
  the session **stays ① (TURN_LEVEL)**: the planner still sees the raw
  data. This is *not* a fail-open on enforcement — `decide()` still
  blocks egress at the chokepoint regardless of mode — but it *is* a
  fail-to-weaker-*exposure*: exposure-minimization is forgone while the
  egress guarantee holds. Mode affects how much the planner sees, not
  whether enforcement runs.
- Returns `(mode, reason)`; the choice is audited as `mode.selected`.

So pattern choice is **deterministic and policy-driven, never
model-chosen** — a model cannot opt itself into a weaker pattern.

## Multistep Composition & Incompatibilities

A multistep flow = multiple LLM passes, possibly different patterns per
step, sequential or parallel, possibly sharing a session/label space.
Most model failures are *composition* failures, not single-step ones.
The following are **invariants** — a flow that violates one is a
reviewable defect (Principle VIII), not a tuning choice.

1. **Noninterference is per-step only.** It does not compose upward —
   chained ②/③ declassifications leak at least as much as any single
   one. Never assert whole-flow NI; only a sealed unit may claim it.
2. **MLS-strict work is a sealed sub-session.** Static-MLS-BLP requires
   tranquility (fixed levels/clearances) and is incompatible with
   dynamic Denning taint in the *same* label space. Run it isolated;
   declassify its output (②/③) back to the dynamic world.
3. **Declassification markers are scoped, not sticky.** A `releasable`
   (or any positive declassification) label MUST NOT survive a later
   re-taint of the same session; it is consumed/scoped to the step
   that produced it.
4. **Integrity-critical steps precede untrusted declassification.** A
   Biba-floor (pattern ④/⑤ integrity) step MUST run *before* any
   untrusted-derived/declassified input could reach it — ordering is a
   correctness constraint: a floor step after such a step reads down.
5. **Parallel declassification to a shared sink needs an aggregate
   budget.** Two concurrently within-schema-bound ② extractions can
   jointly exceed intended disclosure (NI parallel-composition /
   aggregation failure). Concurrent declassifications toward one egress
   MUST share a disclosure budget.
6. **Brewer-Nash requires global conflict state.** Independent parallel
   sessions each individually clean can jointly span a conflict set;
   Chinese Wall is sound only with a shared/serialized accessed-
   compartment ledger across all concurrent passes.
7. **Containment is not declassification.** Running a step in a
   disposable isolation region (pattern ⑤) establishes only the
   reversibility/integrity of the *computation*. Its output retains the
   most-restrictive source data label; discarding the region is a
   rollback, never a downgrade. Egress of a contained output MUST be
   gated by its source labels exactly as if uncontained — "it ran in a
   sandbox" is never grounds to permit egress. A flow that declassifies
   by containment is a reviewable defect (Principle VIII).

Per-datum vs. across-data: patterns ① and ③ are mutually exclusive for
the *same* value in concurrent passes (expose vs. hide), but
complementary across *different* data in one flow. Reference Monitor
holds under concurrency **iff** every parallel pass still funnels
through the single `decide()` chokepoint.

Best-flow-per-model and complementary pairs are tabulated in
`docs/security-models.md` (Coverage section).

## Audit trail = the flow-explanation artifact

Each pattern's audit events — data read + labels acquired,
`mode.selected` + reason, `decide()` outcomes, declassification points,
approvals — *are* the decision/flow explanation for that pass: the
applied face of the **Gold Standard (Audit)** + **Provenance-security**
rows in `docs/security-models.md`. It explains *what the planner was
allowed to touch and what gated each effect*, never *why the model
chose its output* (interpretability is out of scope by design); a
model's self-narrated reasoning is not part of this artifact.

## Cross-reference

Each pattern realizes a row in `docs/security-models.md` (patterns 2–4
are noninterference *declassification* variants; pattern 1 is the
Denning lattice mechanism). New flow patterns MUST be added here and
cross-linked to their model row there; pattern 3's gap is tracked as
planned work until it is a documented first-class mechanism. The six
composition invariants above are enforced as cross-step review checks.
