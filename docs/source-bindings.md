# Source Bindings & Automated Labeling — Resources, the dictionary, and the LLM tightener

**Purpose.** How CapableDeputy answers "what *is* this data, and how
sensitive?" — the labeling layer that feeds every `decide()`. This is
the capdep realization of CORE's **Resources** (see
`docs/policy-rule-structure.md` for the CORE/PRO lens). Two mechanisms,
both already in the engine, plus the safe pattern for an LLM-based
labeler.

**Status: documents shipped mechanisms** (`policy/bindings.py`,
`policy/labels.py`, `tools/client.py`) + the design pattern for an
LLM admission labeler. Binding resolution and raise-only inspectors are
implemented; the LLM-labeler-as-inspector is a registration pattern, not
a shipped inspector.

## The dividing line: labels are an *input*, not a model decision

`decide()` is a pure function of labels + capabilities + axes
(Principle I). It does **not** classify data — it *consumes*
classifications. So "automated labeling" is about *producing trustworthy
label inputs*, and the trust posture of the producer is everything. Two
producers, by trust:

1. **Deterministic, authoritative** — Source/Location Bindings. Known
   resource → declared category/tier. May set any tier.
2. **Non-deterministic, raise-only** — inspectors (incl. an LLM
   labeler). May only *tighten*, never relax (FR-025 / FR-031).

A producer that could *lower* protection is a fail-open hole and is
forbidden outside deterministic bindings, human declaration, and
certified declassifiers.

## 1. Source/Location Bindings — the datasource dictionary (CORE "Resources")

A binding is one operator-authored rule mapping a **canonical resource
scope** to a label. This is exactly a "dictionary of datasources with
labeling rules": when capdep touches a *known* resource it gets detailed,
deterministic labeling. (`SourceLocationLabelBinding`, FR-043/048.)

```yaml
# configs/source_bindings.yaml
bindings:
  - name: hr-folder
    scope_pattern_canonical: "file:///home/marc/hr/**"   # the Resource (CORE)
    category: personal
    default_tier: restricted                              # the labeling rule
    write_discipline: version-preserving
    risk_ids: [RISK-PII-DISCLOSURE]
    assignment_provenance: operator-declared
  - name: work-github
    scope_pattern_canonical: "github://octocorp/**"
    category: work
    default_tier: sensitive
    risk_ids: [RISK-PROP-LEAK]
```

Resolution semantics (`BindingSet.resolve`, already implemented):
- **Canonical scope.** Patterns are globs over canonicalized URIs
  (`file:///abs/**`, `github://org/**`, `https://site/...`,
  `mcp:server/id`). The resolver locks onto a `canonical_destination_id`
  and auditors record *that*, not the model's raw input (FR-048).
- **Most-specific wins** for category + tier (longest literal prefix);
  true ties break to the **stricter tier**.
- **Most-restrictive compose** for reversibility / mutability /
  write-discipline across all overlapping bindings; `risk_ids` set-union.
- **Fail-closed (FR-023):** an unbound or non-canonicalizable URI raises
  `BindingError` — *no* best-effort labeling. Consulted on every
  read/ingest **and** every write/egress.

This maps CORE's Resource model directly: the binding *is* the Resource
node + its tags; `write_discipline` + tier encode the access-pattern
expectations CORE attaches to a component↔resource edge. It's the
strongest labeling path — deterministic, in-TCB, authoritative.

## 2. Raise-only inspectors — the LLM labeler, made safe (FR-025)

For *unknown* data, or content-derived sensitivity a binding can't see
(a generic `sensitive` doc that actually contains an SSN), capdep
supports **inspectors**: hooks that run on tool output and may add labels.
The provenance system makes one class special:

`AssignmentProvenance.RAISE_ONLY_INSPECTOR` (`policy/labels.py`, FR-025)
— a label from this source **can only ADD taint, never CLEAR it**. The
label-inheritance math treats a raise-only-inspector label as a
most-restrictive floor that downstream composition can raise but never
lower. The hook runs in `tools/client.py::_apply_inspectors` on **every**
tool return, emitting `inspector.applied` audit events.

**This is the safe home for an LLM-based labeler.** Register an LLM that
inspects returned data and emits "looks like PII → `restricted`" as a
raise-only inspector. Then:
- a correct labeler tightens protection where a binding was too coarse;
- a *manipulated or hallucinating* labeler can only **over**-protect
  (annoying, safe) — it is structurally incapable of **under**-protecting
  (the fail-open hole). FR-031: non-deterministic inputs may only tighten.

So an LLM in the labeling path does **not** violate Principle I/VI: it
never participates in the *decision*, and its label is a floor, not an
authority. It sits as substrate behind an **admission-labeler port**
(constitution Security Constraints names this port shape) — outside the
TCB, swappable, with the policy engine never depending on its
correctness for safety.

## The composed pipeline (deterministic floor + optional tightener)

For a known resource you get both, layered, binding authoritative:

1. **Binding fires** (deterministic): `file:///home/marc/hr/**` →
   `personal / restricted`. Authoritative tier for that resource.
2. **Raise-only inspector** (optional, LLM or heuristic) scans content
   and may *tighten* further; never relaxes the binding's tier.
3. Result carries `assignment_provenance`, so the audit shows whether a
   label was binding-declared, human-declared, or inspector-raised — and
   the engine knows an inspector label can never lower anything.

This is CORE's "tag the data at the Resource, propagate downstream,"
made fail-closed: deterministic tags from the known source, an optional
non-deterministic *tightener*, never a loosener. Lowering a tier comes
only from a deterministic binding, a human declaration, or a certified
declassifier (`llm-flow-patterns.md` ②/③).

## What is NOT allowed (the boundary, restated)

- An inspector (LLM or otherwise) MUST NOT lower a tier or clear taint
  (FR-025/031). Such an inspector is a reviewable defect.
- An unbound, non-canonicalizable resource MUST fail closed (FR-023),
  not receive a guessed label.
- "It ran in a sandbox" is not a label downgrade (containment ≠
  declassification, `llm-flow-patterns.md` invariant #7).

## Cross-reference

- `docs/policy-rule-structure.md` — the PRO-over-CORE lens; bindings as
  CORE Resources, effect_class as Operations.
- `docs/responsible-ai-frameworks.md` — labeling is the first of the
  three contingencies that bound every guarantee.
- `src/capabledeputy/policy/bindings.py` — `SourceLocationLabelBinding`,
  `BindingSet.resolve`.
- `src/capabledeputy/policy/labels.py` — `AssignmentProvenance`
  (incl. `RAISE_ONLY_INSPECTOR`); `tools/client.py::_apply_inspectors`.
- `configs/source_bindings.yaml` — the operator's datasource dictionary.
