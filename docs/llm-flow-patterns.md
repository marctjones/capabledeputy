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

### 3. Reference / placeholder substitution (data-blind planning) — 🔶 partial / planned

The planner manipulates only **opaque handles/placeholders**. The
deterministic runtime binds the real labeled value at a controlled
insertion point and records where it went; the model never holds the
value at any point.

- **Planner sees:** references only (`<doc#3>`, capability/tool
  aliases) — never the value, not even a projection.
- **Guarantee (target):** the planner is *structurally incapable* of
  leaking what it never holds; insertion is explicit, auditable, and
  the destination of each secret is tracked.
- **Status — honest gap:** the building blocks exist — per-session
  unforgeable tool/capability tokens (v0.3) and programmatic
  variable-binding — but this is **not yet a first-class, named
  mechanism** with the "controlled re-insertion + provenance of where
  the secret lands" guarantee. Crystallizing it is planned work.
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

## Meta-control: mode auto-escalation — ✅ implemented (v0.7)

Not a pattern but the **selector**: the execution-mode dispatcher
auto-escalates a session to pattern 2 or 4 once it carries any
`confidential.*` label and a quarantined extractor / programmatic mode
is available, and hides raw-data readers from the planner. Pattern
choice is therefore policy-driven, not model-chosen.

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

Per-datum vs. across-data: patterns ① and ③ are mutually exclusive for
the *same* value in concurrent passes (expose vs. hide), but
complementary across *different* data in one flow. Reference Monitor
holds under concurrency **iff** every parallel pass still funnels
through the single `decide()` chokepoint.

Best-flow-per-model and complementary pairs are tabulated in
`docs/security-models.md` (Coverage section).

## Cross-reference

Each pattern realizes a row in `docs/security-models.md` (patterns 2–4
are noninterference *declassification* variants; pattern 1 is the
Denning lattice mechanism). New flow patterns MUST be added here and
cross-linked to their model row there; pattern 3's gap is tracked as
planned work until it is a documented first-class mechanism. The six
composition invariants above are enforced as cross-step review checks.
