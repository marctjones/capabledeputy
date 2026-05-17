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

## How this is tracked

- Adding/altering an enforcement mechanism MUST add or update a row
  above, naming its model and any deviation + reason.
- An undocumented deviation, or a mechanism with no model lineage, is a
  reviewable defect (candidate `/speckit-analyze` / constitution gate).
- Detail and proofs live in `DESIGN.md` and `spec/CapableDeputy.tla`;
  governance lives in `.specify/memory/constitution.md` (Principles
  I–VII). This file is the map between them.
