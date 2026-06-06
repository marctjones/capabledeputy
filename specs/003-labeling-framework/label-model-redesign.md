# Label Model Redesign (003 addendum) — clean four-axis, no backwards compat

**Status: design note for review. No code yet.** Supersedes the
half-migrated state where the legacy flat `Label` enum and the four-axis
model coexist. **Explicit mandate: no backwards compatibility.** The flat
enum, all migration glue, and every SCHEMA_VERSION-5 read path are
deleted, not bridged. `state.db` is wiped on cutover (single-operator,
local; no migration). Heavy rewrites are accepted to reach the correct,
simple-but-still-correct end state.

Design priority order: **correct > simple-given-correct > small diff.**
We never trade correctness for simplicity; we do trade compatibility and
diff size for both.

## TL;DR — the new model, and how it differs

A session accumulates **labels** describing *the data it has touched*; the
**action** being decided and its **context** are kept separate from those
labels. Labels are **two propagating axes** — **A** (`category × tier`) and
**B** (`provenance`). The action kind (**C** = `EffectClass` Operation) and
request context (**D** = recipient/auth/purpose) are *decision inputs, not
propagating labels*. Labels are **applied by 3 sources** (binding
resolution / operation inherent declaration / raise-only inspector) and
**removed by 1** (certified declassifiers only); each operation declares a
**tag-transfer function**.

| Dimension | Original (flat `Label` enum) | New (clean four-axis) |
|---|---|---|
| Structure | one flat set fusing 4 concerns into 8 values | 4 separated axes (2 propagate + operation + context) |
| Category vs tier | fused (`confidential.personal`) | independent — tier is its own total order → **BLP** |
| Provenance/integrity | a flat `untrusted.*` label | Axis B lattice; integrity floor moved onto the **operation** → **Biba** |
| Effect/action | an `egress.*` label *inside the session set* | Axis C is the **Operation**, never taint; enum + `subtype` |
| Context | not represented | Axis D decision input (purpose session-scoped) |
| Extensibility | hardcoded 8 (new category ⇒ edit Python) | open catalog in `labels.yaml` |
| Per-label metadata | none | each tag carries `risk_ids` + `assigned_by` |
| Apply | `frozenset` **union** (add-only, unprincipled) | 3 declared sources + per-operation tag-transfer |
| Remove | no discipline | **certified declassifiers only** (structural) |
| Decision input | `decide(frozenset[Label])` | `decide(labels, operation, context, capabilities)` |
| Backwards compat | — | **none**: flat enum deleted, `state.db` wiped, no migration |

**The two changes that matter most:** (1) **un-fusing the axes** — the
original crammed sensitivity, integrity, action, and context into one
`frozenset`; separating them by lifecycle is what makes BLP / Biba / true
NI *expressible* (the in-scope-but-unbuildable risks the flat enum
blocked). (2) **a real apply/remove discipline** — three ways in
(raise-only except authoritative bindings), one way out (certified
declassifiers), made explicit per operation, replacing blind set-union
with no removal rule.

## 1. The problem we're fixing (grounded)

- The enforcement engine still types its core input as
  `label_set: frozenset[Label]` (`engine.py:316,668`) — the **legacy
  v0.7 flat enum is still the operative model**.
- The flat `Label` enum (8 hardcoded values) **conflates three axes into
  one fused set**: `confidential.personal` fuses *category* + *tier*,
  `untrusted.external` is *provenance*, `egress.email` is *effect*. One
  `frozenset` mixes data sensitivity, integrity, and the action — the
  category error at the root.
- It carries **no tier-as-its-own-thing, no provenance metadata, no
  `risk_ids`, no `assignment_provenance`**, and is **not extensible**
  (new category ⇒ edit Python).
- The replacement four-axis model exists but the **catalog is empty**
  (`configs/labels.yaml: categories: []`), so labeling rides the flat
  enum in practice. A half-migration: worse than either end.

## 2. The central clarification: not everything is a propagating label

The flat enum's deepest error is treating *data sensitivity*, *integrity
provenance*, *the action*, and *the request context* as one undifferentiated
bag. They have different lifecycles. The redesign separates them:

| Axis | What it is | Lifecycle | Propagates? |
|---|---|---|---|
| **A — Data Category × Tier** | what the data *is* + how sensitive | accumulates on the session as data is read | **yes — a label/taint** |
| **B — Provenance / Integrity** | how trustworthy the data's origin is | accumulates on the session | **yes — a label/taint** |
| **C — Effect Class (Operation)** | what an *action* does | property of the tool/operation, per-action | **no — an operation kind** |
| **D — Decision Context** | recipient trust, initiator+auth, purpose | request-time context (purpose is session-scoped) | **no — a decision input** |

**Only Axis A + Axis B are labels that propagate (CORE's "tags").**
Axis C is CORE's "Operation"; Axis D is CORE's execution context. The
`decide()` call is therefore:

```
decide(labels: LabelState,        # Axis A + Axis B — the propagating taint
       operation: Operation,      # Axis C effect_class + the tool's tag-transfer
       context: DecisionContext,  # Axis D — recipient, initiator+auth, purpose
       capabilities: CapabilitySet) -> Decision
```

This separation is the whole game: it ends the fused-frozenset confusion.

## 3. The label model: `LabelState` = Axis A + Axis B

```
Tier            = none < sensitive < regulated < restricted < prohibited   # total order; join = max
ProvenanceLevel = principal-direct > system-internal > external-untrusted   # integrity lattice

CategoryTag   = (category: str,            # open catalog, declared in labels.yaml
                 tier: Tier,
                 risk_ids: frozenset[str],
                 assigned_by: Provenance)   # how this tag got here (audit + removal-legality)
ProvenanceTag = (level: ProvenanceLevel)   # the integrity FLOOR is not here — it is an
                                            # Operation requirement (§5), not a data property

LabelState = (a: frozenset[CategoryTag], b: frozenset[ProvenanceTag])
```

We keep A and B as **two typed axes**, not one generic `Tag`, on purpose:
they compose by different correct rules (A is per-category tier-max; B is
provenance-lattice with a sticky integrity floor). Forcing them into one
generic tag would be *simpler-looking but less correct* — rejected.

**One composition primitive, applied per axis** (`most_restrictive_inherit`):
- A: group by category; `tier = max`; `risk_ids = union`; `assigned_by = strictest`.
- B: `levels = union`; most-tainted dominates. No floor here — the floor is an Operation requirement (§5), checked at decide() against these levels (Biba "no read-down").

## 4. Apply / remove — the lifecycle that was never well-defined

This is the part the redesign exists to fix. There are **exactly three
ways a label is applied and exactly one way it is removed.** Anything
else is a defect.

### Apply (raise/add only) — three sources
1. **Binding resolution** (`SourceLocationLabelBinding`, FR-043/048): a
   read/egress of a known Resource contributes that resource's
   `CategoryTag` (most-specific wins; fail-closed if unbound, FR-023).
   *Authoritative, deterministic, in-TCB.*
2. **Operation inherent declaration**: the tool declares the tags its
   output inherently carries (a web-fetch tool adds
   `external-untrusted`; a health-record tool adds `health/restricted`).
3. **Raise-only inspector** (FR-025): post-hoc content inspection (incl.
   an LLM labeler) may **tighten only** — add a tag or raise a tier,
   never lower. `assigned_by = raise-only-inspector` is structurally
   incapable of removal/downgrade.

### Remove (downgrade/clear) — one source
- **Certified declassifiers only**: dual-LLM schema projection (pattern
  ②), reference-handle bind (pattern ③), or human one-shot
  declassification. Each is a structural, certified downgrade. **No
  ordinary operation may remove a tag.** "It ran in a sandbox" is not a
  removal (containment ≠ declassification — `llm-flow-patterns.md` #7).

### The tag-transfer function (per Operation) — CORE made explicit
Every Operation declares a **tag-transfer**: the function from prior
`LabelState` to posterior `LabelState`.

```
TagTransfer = (adds: LabelState,           # from inherent declaration + resolved bindings
               removes: LabelState | None) # None unless this Operation IS a certified declassifier
apply(state, transfer) = most_restrictive_inherit(state, transfer.adds) \ (transfer.removes or ∅)
```

For non-declassifier operations `removes` is always `None` — enforced
structurally, not by convention. This is the single, legible answer to
"how are labels applied and removed."

## 5. Operations = the canonical `EffectClass` enum (resolves T012)

`effect_class` becomes the canonical enum (CORE's "Operation"), **required
and fail-closed-validated at registry load** — this is T012, resolved
toward the enum because the tag-transfer model wants a closed Operation
taxonomy:

```
EffectClass = OBSERVE | FETCH | MUTATE_LOCAL | DESTROY | COMMUNICATE
            | TRANSACT | EXECUTE_SANDBOX | EXECUTE_HOST | EXECUTE_REMOTE
            | EXECUTE_DEPLOY | ADMINISTER | ACTUATE_PHYSICAL
```

**Rules match on the enum** (the canonical Operation). An optional
free-form `subtype: str | None` is retained per operation for display,
audit, and finer-grained rules when an operator wants them
(e.g. `MUTATE_LOCAL` + subtype `"calendar.delete"`). A rule that omits
`subtype` matches the whole enum class, so the default-rule layer stays
category-clean; a rule may *narrow* to a subtype when needed. The old
free-form strings (`data.read_local`, `social.send_email`) survive only
as subtypes, never as the matching key.

A tool may declare **a set** of operations (a Component performs many);
a wrapper must declare the union (registry-load refuses under-declaration).

Each Operation also carries its **required integrity floor**
`required_floor: ProvenanceLevel | None` (the Biba requirement, §4a):
the minimum input trustworthiness the action demands. Registry-load
refuses any tool missing `effect_class`, `risk_ids`, or a well-formed
tag-transfer (Principle VI).

## 4a. The integrity floor — a requirement of the Operation, not the data

Some actions must not be driven by untrusted input (paying an invoice on
data scraped from an untrusted email is the confused-deputy hole). The
**integrity floor is a property of the Operation**: `required_floor`
declares the minimum provenance an input may have. At decide():

```
if any(tag.level is below operation.required_floor for tag in labels.b):
    refuse   # Biba: untrusted data cannot drive a high-integrity action
```

The data carries its provenance (Axis B label, propagates); the operation
carries the requirement (does not propagate). This keeps the central
separation intact — labels describe data, requirements describe actions.

## 6. What gets deleted (no compatibility)

- `class Label(StrEnum)` and **every** `frozenset[Label]` (74 sites),
  `inherent_labels` (96 sites), `additional_labels` (57 sites) — replaced
  by structured `inherent_tags` (CategoryTag + ProvenanceTag) on the
  tool, and `TagTransfer` on the operation.
- `AssignmentProvenance.LEGACY_MIGRATION`, all v5/flat read paths,
  default-tolerant legacy `from_dict` branches.
- `state.db` is wiped on cutover; the store persists `LabelState`
  directly. We keep versioned serialization *discipline* (so future
  schema evolution is clean) but carry **zero** legacy read code.
- `engine.decide()` signature `label_set: frozenset[Label]` →
  `(labels: LabelState, operation, context, capabilities)`.

## 7. Why this is correct (model lineage, Principle VIII)

- Axis A tier ordering + read-up refusal = **Bell-LaPadula** (now
  expressible: tier is first-class, not fused into a label name).
- Axis B provenance + integrity floor = **Biba** direction (now
  expressible separately from confidentiality — closes the most
  under-served gap).
- `most_restrictive_inherit` = **Denning lattice** join.
- Certified-declassifier-only removal = intransitive **noninterference**.
- Tag-transfer per Operation + bindings as Resources = the **CORE/PRO**
  data-flow model, enforced at runtime (`docs/policy-rule-structure.md`).

## 8. What it unblocks

Separating the axes is the prerequisite for the in-scope-but-unbuilt
risks: **integrity floor / no-read-down** (Biba), **max-tier clearance /
read-up refusal** (BLP, FR-008), and **true NI for `restricted`** — all
currently blocked because the flat enum cannot represent tier and
provenance as independent, composable dimensions.

## 9. Execution plan (rewrite, test-first, no big-bang merge)

Each step leaves the suite green and the LLM-independence + fail-closed
invariants enforced (Principle III). Order:

1. **Land the types**: `Tier`, `CategoryTag`, `ProvenanceTag`,
   `LabelState`, `EffectClass` enum, `TagTransfer`, with composition +
   property tests (Hypothesis: composition is associative,
   commutative-where-required, monotone-raising).
2. **Populate `labels.yaml`** with the real stable-core category set;
   delete the empty stub.
3. **Tool declarations**: replace `inherent_labels: frozenset[Label]`
   with `inherent_tags` + canonical `effect_class`; wire T012 fail-closed
   registry validation (with the contract's CI invariant tests).
4. **Engine**: re-type `decide()` to `LabelState`; port every rule;
   delete the flat-label decision path. Re-prove SC-002 determinism.
   **R4c verification points (from the R4b.2 audit), MUST be checked here:**
   - **Run-both-and-assert-agreement**: keep the legacy axis path and the
     new `LabelState` path computing in parallel and assert identical
     outcomes before deleting the legacy path — the safety net for the
     re-type. In particular confirm the two authority-resolution
     implementations agree: `_compose_a` (new, `_AUTHORITY_RANK` max) vs
     `most_restrictive_inherit_axis_a` (legacy, parent-wins/raise-only)
     must resolve `assignment_provenance` identically, else reconcile.
   - **Fix the mis-declared test fixtures**: many test tool factories
     blanket-declare `operations=(Operation(FETCH),)` regardless of the
     tool's real effect (e.g. send/write fixtures). Inert until `operations`
     is consumed here; correct the write/egress ones as part of this step.
5. **Apply/remove**: route all application through the three sources and
   removal through certified declassifiers only; add the structural test
   that a non-declassifier operation cannot remove a tag.
6. **Store**: persist `LabelState`; delete v5 read paths; wipe `state.db`.
7. **Delete** the flat `Label` enum and all dead compat code; grep-gate
   that `frozenset[Label]` has zero occurrences.

## 10. Decisions (locked 2026-06-05)

1. **EffectClass granularity** — ✅ **Canonical enum + optional free-form
   `subtype`.** Rules match on the enum; `subtype` is retained per
   operation for display/audit and optional narrowing. Old strings live
   on only as subtypes. (§5.)
2. **Integrity floor** — ✅ **Property of the Operation**
   (`required_floor: ProvenanceLevel | None`), not a flag on the data
   tag. Checked at decide() against the session's Axis-B provenance
   (Biba no-read-down). (§4a.)
3. **Axis D purpose** — ✅ **Session-scoped context**, set at
   `session.new`, copied on fork; *not* a propagating data tag.
4. **`state.db` wipe** — ✅ **Wipe on cutover.** No persisted-session
   compatibility; this is the one place compat could have re-entered and
   it is closed.
