# Policy Rule Structure — the PRO-over-CORE lens + default templates

**Purpose.** How CapableDeputy's policy rules are *structured* — and the
default rules that ship out of the box. Companion to
`docs/responsible-ai-frameworks.md` (which says *which* principle each
rule serves). This doc says *how to write rules so they attach to
capability categories rather than named tools*, and gives the default
rule per category. It borrows a vocabulary — **CORE / PRO**, from Van
Lindberg / Process Mechanics (`processmechanics.com/core/`, used with
permission) — because that vocabulary names exactly the structure our
engine already has.

**Status: policy-authoring guide + default-template draft.** Not a
formal model; the binding yardstick is still `security-models.md`
(Principle VIII). The default templates here are proposed defaults for
review, not yet wired into `configs/`.

## The PRO-over-CORE lens

CORE models an AI system as a directed data-flow graph; PRO governs it.
The two map onto CapableDeputy almost one-to-one — the difference is
that **CORE/PRO is design-time analysis and CapableDeputy is runtime
enforcement of the same structure.**

| CORE / PRO (Process Mechanics) | CapableDeputy |
|---|---|
| **Components** — typed nodes (LLM, DB, API, guardrail, human review), each defined by the operations it performs + resources it accesses | the actors: planner LLM (untrusted, outside TCB), quarantined LLM, **tools**, the deterministic chokepoint (= guardrail), the human approver (= HITL). A **tool is a Component, characterized by its declared `effect_class` + resources.** |
| **Operations** — actions on data; each **adds/removes tags** | `effect_class` (the action category) + label add/remove (`inherent_tags`/`arg_inherent_tags` add tags; certified declassifiers remove them) |
| **Resources** — external assets, with access pattern (read/write/delete) | Axis-A data-category + source/location **bindings** (canonical destination ids, FR-043/048); access pattern ≈ effect direction + `surfaces_destination_id` |
| **Execution** — data-flow edges; tags propagate | the session flow + **Denning dynamic taint propagation**; routing = flow patterns ①–⑤ + the mode selector |
| **PRO · Policies** — data-flow constraints run against the graph | `rules.yaml` `DecisionRule`s — predicates over axes A–D + effect class |
| **PRO · Risk register** — FMEA-style risk taxonomy, standards-mapped | `risk_register.py` + per-tool `risk_ids` + per-entry residual-risk thresholds (FR-016/028) |
| **PRO · Outcomes** — pass/fail + evidence (reasonable-care record) | `decide()` outcomes + append-only hash-chained audit (the evidence) |

**The one boundary we keep.** CORE/PRO is a *risk-management* framework
— it quantifies risk (FMEA severity × occurrence × detection) and
manages it "to an acceptable threshold," with guardrails modeled as
having non-zero failure rates. CapableDeputy's enforcement decision is
**deterministic and fail-closed** (Principles I/VI): there is no
"acceptable probability" of a wrong *allow*. We adopt CORE/PRO's
*modeling, risk-cataloging, and evidence* vocabulary; we do **not**
import its "scored, acceptable-threshold guardrail" stance into the
enforcement decision. See `docs/responsible-ai-frameworks.md` and the
disconnects discussion for why this boundary is load-bearing.

## How rules are structured: against Operations, not tools

A `DecisionRule` is a predicate over **capability dimensions** —
`effect_class`, `default_reversibility`, `social_commitment`,
`tool_provenance`, `surfaces_destination_id`, `risk_ids`,
`capability_kind` — plus the session's Axis A/B/D and purpose. It never
names a tool. CORE explains why this is right: **a Component (tool) is
defined by the Operations it performs**, so policy attaches to
Operations (`effect_class`), and any tool performing that Operation
inherits the rule — present or future. (This is exactly T012's
contract: a tool must declare its operations; a wrapper must declare the
*union* of its sub-tools' operations; an undeclared tool is refused.)

Each Operation has a **tag-transfer function**: what labels it adds (it
reads a Resource ⇒ acquires that Resource's category/tier tags) and —
only via a certified declassifier — what it removes. Default rules are
written as *(tag-transfer) + (required human-loop rung given the
resulting tags)*.

> **Note for T012.** Default-by-category works best with a small
> canonical Operation set. The current code uses fine-grained strings
> (`data.read_local`, `social.send_email`, `EXECUTE.devbox`, …); the
> contract proposes a canonical `EffectClass` enum
> (OBSERVE/FETCH/MUTATE_LOCAL/DESTROY/COMMUNICATE/TRANSACT/EXECUTE.*/ADMINISTER).
> CORE's "Operations" argument favors the canonical enum as the
> rule-matching layer (with the fine strings as sub-types) — input to
> the parked enum-vs-string decision.

## Default policy templates (by Operation / effect-class)

Proposed defaults. Each row = one Operation category, its tag-transfer,
the default human-loop rung (`responsible-ai-frameworks.md`), the
`decide()` outcome, and the principle (P1–P8) it implements.

| Operation (effect_class) | Tag-transfer | Default rung | Default outcome | Principle |
|---|---|---|---|---|
| **OBSERVE / FETCH** (read-only, non-egressing) | adds Resource's category × tier tags to the session | human-on-the-loop | `auto` | P3, P8 |
| **MUTATE_LOCAL** | adds nothing; effect on a controlled Resource | on-the-loop if reversible/system | `auto` if reversible, else require-approval | P5, P8 |
| **DESTROY** | — | human-in-the-loop | require-approval + write-discipline / reversibility verify (FR-044) | P5 |
| **COMMUNICATE / TRANSACT** (`social_commitment=true`) | egress sink — checks accumulated session tags vs recipient-trust (Axis D) | human-in-the-loop | require-approval; hard-irreversible (FR-019); deny if restricted tag → untrusted recipient | P3, P5 |
| **EXECUTE.sandbox** | output retains source tags (containment ≠ declassification) | on/out-of-the-loop | `auto` if actuator present + egress-free; else `OverrideRequired` | P8 |
| **EXECUTE.host / remote / deploy** | — | human-in-the-loop / refuse | `OverrideRequired`; prefer sandbox | P1, P8 |
| **ADMINISTER** (control-plane) | — | refuse if tainted | deny if session carries any `external-untrusted` provenance (FR-018) | P1, P2 |

**Companion defaults (not per-Operation):**
- **Never-auto default (P7):** absent a matching rule ⇒ `suggest`/`deny`,
  never `auto` (FR-011).
- **Purpose admissibility (`purposes.yaml`, P4):** a category
  inadmissible for the session purpose is refused at spawn (FR-009);
  no purpose ⇒ `unset` admits no consequential effect (FR-046).
- **Override policy (P5):** per-severity
  `disallowed | single-authorized | dual-control`; 15-min default /
  60-min hard-cap expiry.
- **Integrity floor (P2):** integrity-floored steps refuse
  `external-untrusted` provenance inputs (Biba direction).
- **Tag removal is privileged:** a default rule MUST NOT remove a tag.
  Only a certified declassifier (dual-LLM schema ②, human one-shot,
  reference-handle bind ③) may. An Operation that claims to drop a tag
  without being a certified declassifier is a reviewable defect
  (`llm-flow-patterns.md` invariant #7).

## Consuming a CORE model as input (optional, future)

Because CapableDeputy's inputs *are* a CORE graph (tools = Components,
effect classes = Operations, bindings = Resources, taint = Execution),
a CORE/PRO blueprint could be ingested to *generate or validate*
default rules. Caveat from the rigor boundary above: a CORE model's
tag *removals* are design-time **claims**; CapableDeputy treats them as
claims to verify via a certified declassifier at runtime, never as
facts. Likewise, a CORE policy is a *global flow-invariant*
("customer_data never reaches third_party_api"); CapableDeputy enforces
it as *dynamic taint + egress block* at the chokepoint — the runtime
realization of the design-time invariant.

## CORE vs CapableDeputy — overlaps, disagreements, disconnects

The root of every difference: **CORE/PRO is a design-time
governance-and-liability framework (model the system, check it, prove
reasonable care); CapableDeputy is a runtime enforcement engine (make
the bad outcome unreachable at the moment of action).** Same data-flow
worldview, opposite ends of the lifecycle.

### Strong overlaps & synergy
- **Identical core thesis:** governance = data-flow + tag propagation,
  not documentation. CORE's "tags propagate along edges, policies are
  flow constraints" *is* our Denning taint + chokepoint. Independent
  convergence (a lawyer's and a security engineer's) is strong validation.
- **Both tool-agnostic / capability-categorized:** CORE's "a Component
  is defined by the Operations it performs" justifies our
  `effect_class`-keyed rules + the T012 declaration/union contract.
- **Risk register — their strength fills our gap:** Model Monster's
  R-1000/2000/3000 taxonomy (standards-mapped) is far richer than ours;
  we have now imported the agentic subset into `configs/risk_register.json`
  so tool `risk_ids` (T012) and residual-risk thresholds (FR-016/028) can
  cite them.
- **Outcomes ≡ our audit:** their reasonable-care evidentiary chain (EU
  AI Act Art 9/11) is what our append-only, replayable audit produces; we
  could emit CORE Outcomes from it. Their articles complement ours (Art
  14 human oversight).
- **HITL + guardrails as first-class — and we strengthen the assumption:**
  CORE gives guardrails non-zero failure rates (FMEA detection); our
  deterministic chokepoint is the limiting case — a guardrail whose
  enforcement-decision failure rate is ~0 by construction.

### Genuine disagreements (philosophical)
- **Managed/acceptable risk vs structural impossibility.** CORE manages
  risk "to an acceptable threshold" (FMEA, probabilistic). Capdep's
  enforcement decision is deterministic + fail-closed (I/VI); a
  capability that can't be enforced is **cut** (VII), not shipped with a
  scored control. (Bridge: our residual-risk *exceptions*, FR-016/028,
  are a deterministic cousin of FMEA — but they force an audited
  escalation, never an automated "scored pass.")
- **Telos: defensibility vs prevention.** CORE optimizes to *prove* you
  governed (liability, standards-mapping); capdep optimizes to make harm
  *unreachable*. A CORE-"governed" system can still drift at runtime;
  capdep prevents but doesn't itself produce the liability narrative.
- **Trust model of own components.** CORE treats the LLM as a
  characterized node with a failure rate (models *failure*); capdep
  treats the planner as untrusted, outside the TCB (models *adversarial
  compromise*).

### Misalignments / disconnects (mapping friction)
- **Tag *removal* semantics.** CORE lets any operation remove tags (a
  de-identification op "removes the PII tag"). Capdep refuses
  un-certified tag removal — only certified declassifiers (②/③, human
  one-shot) may; "containment ≠ declassification" (invariant #7).
  **Consequence: a CORE model's tag-removals are claims to verify at
  runtime, never facts.**
- **Opposite construction ends.** CORE = risk taxonomy + design-time
  check, thin on enforced mitigation; capdep = deep enforced mitigations,
  thin on taxonomy. Complementary, but neither maps onto the other's
  core competency.
- **Modeling scope > enforcement scope.** CORE models multi-agent /
  multi-service topologies; capdep is single-tenant, single-chokepoint
  (multi-agent out of scope). CORE can describe systems capdep won't
  govern.
- **Policy expression level.** CORE policies are *global graph
  invariants*; capdep rules are *local per-action predicates*. They
  reconcile (our dynamic taint + egress block is the runtime realization
  of a CORE invariant) but you "compile" one into the other.
- **Resources first-class in CORE, bindings in capdep.** Resolved by
  leaning on `docs/source-bindings.md` — bindings *are* our first-class
  Resource model.

### Bottom line
Complementary layers with exactly one boundary to police: **do not
import CORE's "acceptable-threshold / scored guardrail" risk philosophy
into capdep's enforcement decision** (it would violate Principles
I/VI/VII). Use CORE for modeling, risk-cataloging, and evidence; keep
enforcement deterministic and fail-closed. Integration: capdep is the
runtime enforcement of PRO-over-CORE; CORE's registry feeds our
`risk_ids`; our audit emits CORE Outcomes; capdep models *as* a
near-zero-failure CORE guardrail component.

## Cross-reference

- `docs/responsible-ai-frameworks.md` — the eight principles + the
  human-in/on/over-the-loop ladder these defaults implement.
- `docs/security-models.md` / `docs/llm-flow-patterns.md` — the binding
  models + the flow patterns the tag-transfer functions realize.
- `specs/003-labeling-framework/contracts/tool_definition.md` — the
  tool-declaration contract (T012) that makes Operations reliable.
- CORE / PRO — Van Lindberg, Process Mechanics
  ([processmechanics.com/core/](https://processmechanics.com/core/));
  Model Monster ([modelmonster.ai](https://modelmonster.ai/)) is the
  implementing platform + risk registry.
