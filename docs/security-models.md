# Security Models — Theoretical Lineage & Implementation Map

**Purpose.** This is the project's design yardstick: CapableDeputy aims
to be, as far as practical, a *pure implementation of recognized formal
security models* — not an ad-hoc collection of checks. Every
enforcement mechanism MUST trace to a named model, and every place we
**deliberately deviate** from the textbook model MUST be recorded here
with its reason. If a mechanism cannot be traced to a model or its
deviation is undocumented, that is a design defect.

This consolidates references previously scattered across `DESIGN.md`,
`spec/CapableDeputy.tla`, and the constitution. It is the index; those
remain the detail.

## The models we build on

| Model | Property class | One line |
|---|---|---|
| **Reference Monitor** (Anderson 1972) | Assurance | A mediation point that is always-invoked, tamperproof, and small enough to verify. |
| **Lattice information flow** (Denning 1976) | Flow | Data carries labels in a partial order; information may only flow "upward." |
| **Bell-LaPadula** | Confidentiality | No read-up / no write-down — secrets cannot flow down. |
| **Biba** | Integrity | Dual of BLP — low-integrity data cannot corrupt high. |
| **Noninterference** (Goguen-Meseguer 1982) | End-state | High inputs must not be observable in low outputs. |
| **Brewer-Nash / Chinese Wall** | Conflict of interest | Access to one dataset forbids access to a conflicting one. |
| **Clark-Wilson** | Integrity / transactions | State changes only via certified well-formed transactions with separation of duty. |
| **Object-capability** (Dennis–Van Horn; Miller) | Authority | Authority is an unforgeable, scoped, attenuable reference; no ambient authority. |
| **Access-matrix / HRU** (Lampson; Harrison-Ruzzo-Ullman) | Authority | Rights are a subject×object×right relation; the decision is a function of it. |

## Mechanism → model → implementation → deliberate deviation

| Our mechanism | Model(s) | Constitution | Deliberate deviation |
|---|---|---|---|
| Deterministic `decide()` chokepoint | **Reference Monitor** | I | Always-invoked + small + verifiable are met; "tamperproof" extends to *LLM-isolation* (the model is an untrusted subject outside the TCB) — a stronger axiom than the classical model. |
| Information-flow labels | **Denning lattice**; BLP/Biba in spirit | II | Labels are an **open set with conflict rules**, not a fixed total-order lattice; flow is enforced as **dynamic session taint propagation**, not static subject clearances / object levels. |
| Brewer-Nash conflict rules | **Brewer-Nash** | II | Conflicts are declarative trigger×conflict label pairs evaluated per-session; not per-user dataset history. |
| "No silent egress of tainted data" | **Noninterference** | I, II | Achieved by construction at the chokepoint **plus declassification escape hatches** → this is *intransitive* noninterference (controlled declassification), not pure NI. Encoded in `spec/CapableDeputy.tla`. |
| Capabilities (kind+pattern+amount, unforgeable, per-session) | **Object-capability** | IV | Control-plane (grant/approve/revoke) is deliberately **user-driven, model-unreachable** — stricter than classical ocap, where any holder may delegate. |
| Decision = f(labels, capabilities, action, clock, prior-use) | **Access-matrix / HRU** | I | Not identity/role ACL (RBAC/ABAC); the "matrix" is capability + information-flow state, evaluated as a pure function. |
| Destructive-op gate + approvals | **Clark-Wilson** | V | Gated dispatch = the well-formed transaction; human verbatim approval = separation of duty / IVP. We do **not** formalize full CW UDI/CDI/TP/IVP triples. |
| Human-in-the-loop approval state machine | **Clark-Wilson** (sep. of duty) | V | The authorizer is a human; the model (LLM) is structurally excluded — an added constraint beyond CW. |
| Time-bound / rate-limited / prior-use revocation | **Object-capability attenuation** | IV | Attenuation extended to *temporal* and *usage* dimensions; evaluated at decide(), not capability rebind. |
| Capability delegation chains (v0.8, spec'd) | **Object-capability attenuation**; monotone lattice | IV, VI | **Single-parent tree, not a DAG** (auditability over generality); cascade **computed at decide()**, not eager teardown. |
| Dual-LLM quarantined extraction | **Noninterference declassification** | I, II | Schema validation *is* the declassifier — a structural, certified downgrade rather than an operator decision. |
| Per-tenant label spaces | **Lattice compartments** | II | Additive scoping of the same conflict engine; no cross-tenant lattice join. |
| Container isolation / federation signing / append-only audit | Defense-in-depth & **Reference-Monitor assurance** | Sec. Constraints | Supporting assurance, not a confidentiality/integrity model; audit gives the "verifiable" leg of the reference monitor. |
| Fail-closed admission & undecidable-subset refusal | (cross-cutting) | VI | Where a model's check is undecidable (glob⊆glob) or unmapped, we take the **most-restrictive** action — a conservative *approximation* of the model, never a permissive one. |

## Global deliberate deviations (the framing, stated once)

1. **Dynamic taint, not static clearances.** We track flow on the live
   session rather than assigning subjects fixed BLP/Biba levels — fits
   an agent runtime; the lattice property is preserved, the bureaucracy
   is not.
2. **Controlled declassification.** Pure noninterference forbids any
   high→low flow; real workflows need some. We allow it only through
   certified boundaries (dual-LLM schema, human one-shot) → intransitive
   NI, explicitly bounded.
3. **LLM-isolation as an added axiom.** No classical model anticipates
   an untrusted optimizer inside the workflow; we treat the model as a
   subject permanently outside the TCB (strengthens Reference Monitor).
4. **Fail-closed approximation.** When a model's relation is
   undecidable or unknown, we under-approximate authority (refuse), per
   Constitution VI — never over-approximate.
5. **Secure-by-reduction.** Where a model cannot be enforced for a
   capability, the capability is cut, not shipped with a weaker control
   (Constitution VII).

## Coverage, Priorities & Known Gaps

Three tiers of intent (Principle VIII). The point of stating these is
discipline: **do not compromise a Priority model in order to chase a
better-than-stated result on an Approximate one, and do not spend
effort on a Not-Pursued model.**

### Priority — implement faithfully; these are the backbone

These are faithful *and* the most practical in real use; they MUST NOT
be weakened to advance a lesser model.

- **Reference Monitor** — the deterministic chokepoint is a faithful
  reference monitor (always-invoked, tamperproof via LLM-isolation,
  small/verifiable). Non-negotiable backbone.
- **Object-capability** — unforgeable, scoped, attenuable authority; no
  ambient authority. Faithful in the capability layer (planner-side
  fidelity completes when flow-pattern ③ is first-class).
- **Denning lattice information flow** — the everyday workhorse;
  becomes a *genuine* lattice once v0.9 tiers (levels) × data-category
  (compartments) land.
- **Clark-Wilson (enforceable core)** — gated well-formed transactions
  + separation of duty (human approval) + pattern ④ as the certified
  TP. The *core* is faithful and practical for the change/integrity
  path (the full formalism is Not-Pursued, below — these are distinct).
- **Brewer-Nash** — conflict-rule engine; faithful and practical for
  multi-tenant / conflict-of-interest.

### Approximate — the approximation *is* the goal

We target a defined approximation; exceeding it is explicitly **not**
worth compromising a Priority model. The stated fidelity is the ceiling
we design to, not a shortfall to keep closing.

- **Bell-LaPadula → dynamic BLP.** Goal: tiers = levels, context
  profile = clearance, tier→required-flow-pattern = no-write-down. We
  do **not** pursue static certified clearances / formal *-property
  (that is Not-Pursued). Missing to reach the goal: an explicit
  max-tier clearance on the context profile + read-up refusal.
- **Noninterference → intransitive (+ per-tier true NI).** Goal:
  controlled-declassification NI globally, and *true* NI for
  `restricted`/`prohibited` via patterns ③/④. Whole-system transitive
  NI is Not-Pursued. Missing: first-class pattern ③ / sealed-effect
  (else `restricted` falls back to ②, which is intransitive).
- **Biba → one-direction integrity only (scoped).** Goal: the
  "no write-up" direction via the provenance axis
  (`untrusted-meets-egress`). We explicitly do **not** pursue full
  Biba (integrity clearances + "no read-down"); the confidentiality
  tiers do not address integrity — this is the most under-served model
  and the easiest to wrongly assume covered.

### Not Pursued — explicit non-goals (do not attempt)

Documented so effort is never spent here and reviewers do not flag
their absence as a defect:

- **Whole-system transitive noninterference** — theoretically
  incompatible with a useful agent; only the intransitive/per-unit form
  (Approximate) is targeted.
- **Full static MLS Bell-LaPadula** (certified clearances, formal
  *-property) — conflicts with dynamic taint + open-registry + capability
  design; the dynamic approximation is the substitute.
- **Full Clark-Wilson formalism** (UDI/CDI/TP/IVP certification regime)
  — heavyweight; the enforceable core (Priority) is the substitute.
- **Full Biba** (integrity clearances + no-read-down) — not on the
  roadmap; only the scoped one-direction approximation is targeted.
- **General HRU / Take-Grant safety** — safety is undecidable; sidestepped
  by the capability model by construction. Implementing it generally is
  impossible, not merely hard.

### Salvageable as isolated modes (candidate, scoped — not global)

A model impractical *globally* can be faithful *locally* when a step is
isolated from the models it conflicts with. These are **candidate
opt-in modes**, distinct from the global "Not Pursued" stance above;
each MUST obey the composition invariants in
`docs/llm-flow-patterns.md` (esp. #2 sealed sub-session, #4 ordering):

- **Per-step Biba ("integrity-protected transaction")** — a pattern
  ④/⑤ step enforcing an integrity floor on its inputs (refuse any input
  whose provenance < trusted; no read-down within the step). Faithful
  Biba *within the step*. Highest-value of the three; closes the
  integrity gap locally without global integrity clearances.
- **Sealed MLS Bell-LaPadula ("classified session")** — a sub-session
  with a frozen label set + fixed principal clearance + no dynamic
  relabel; output declassified (②/③) back to the dynamic world.
  Practical **only** if never mixed concurrently with dynamic taint
  (invariant #2).
- **Certified-transaction Clark-Wilson** — only pre-registered
  (hash-allowlisted) ④ programs may mutate CDIs, with post-condition
  IVP checks; approaches the full formalism if a program-certification
  registry is added.

*Not* salvageable by any mode/flow: whole-system transitive NI and
general HRU/Take-Grant safety (theoretical impossibilities, restated
above) — no multistep trick changes this.

### Tracked missing mechanisms (planned work, by leverage)

1. **Context-profile clearance** (max tier a principal/use-case may
   handle + read-up refusal) → completes *dynamic BLP*.
2. **First-class flow-pattern ③ / sealed-effect** → unlocks *true NI*
   for `restricted`.
3. **Integrity floor + "no read-down"** on the provenance axis → the
   unaddressed *Biba* half (NOT solved by confidentiality tiers).
4. **Formal lattice dominance/join in the engine** (replace ad-hoc
   conflict-rule pairs) → makes *Denning* fully faithful.

## How this is tracked

- Adding/altering an enforcement mechanism MUST add or update a row
  above, naming its model and any deviation + reason.
- An undocumented deviation, or a mechanism with no model lineage, is a
  reviewable defect (candidate `/speckit-analyze` / constitution gate).
- Detail and proofs live in `DESIGN.md` and `spec/CapableDeputy.tla`;
  governance lives in `.specify/memory/constitution.md` (Principles
  I–VIII; VIII makes this map binding). This file is the map between them.
- Applied companion: `docs/llm-flow-patterns.md` — the four
  operational patterns for how a planner LLM relates to labeled data
  (taint-tracking = the Denning row above; quarantine / reference-
  substitution / code-mediated = the noninterference *declassification*
  rows). New flow patterns are tracked there and cross-link here.
- Decision-layer companion: `docs/trust-model.md` — when/whether an
  action proceeds and who authorizes it. It extends two rows here:
  **Brewer-Nash** → purpose-as-category-admissibility (purpose-scoped
  sessions exclude inadmissible categories — the inappropriate-context
  defense ① cannot give); **Clark-Wilson** → reversibility-weighted
  gating (replace the binary destructive-op gate; human-declared
  recoverability). Both are v0.9-spec scope, capture-only.
