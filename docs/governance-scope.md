# Governance Scope — What CapableDeputy Is Expected To Do (and Not)

**Status: positioning doc.** This states, in governance vocabulary,
what CapableDeputy is *for* and where its responsibility ends. It is
binding as scope (Constitution VII secure-by-reduction; VIII
deviations-documented) but it is **not** the design yardstick — those
remain `docs/security-models.md` (formal-model lineage),
`docs/llm-flow-patterns.md` (planner↔data patterns), and
`docs/trust-model.md` (decision layer). This doc must never be used to
dilute them.

## 1. The one sentence

CapableDeputy is a **runtime control at the intersection of InfoSec
governance, Data & Privacy governance, and AI governance — not a
program for any of the three.** It defends the *conjunction* the three
broad programs structurally cannot, because their controls are mostly
design-time and trust-the-model-flavored.

## 2. The intersection it targets

The triple-overlap incident is one event that is simultaneously all
three failures: an agent **handling personal/regulated data**
[Privacy], that is **manipulable / over-agentic** [AI], that
**breaches confidentiality/integrity via an action** [InfoSec].
Canonical form: a prompt-injected agent reads personal data and
exfiltrates it through an unaccountable automated action.

This is exactly Simon Willison's **lethal trifecta** (sensitive data ×
untrusted content × outbound capability) restated in governance terms.
Targeting it is therefore *not a pivot* — it is the original vision
(README, `docs/index.md`) articulated against recognized external
frameworks. See §7.

## 3. What CapableDeputy is expected to do (in-scope)

Each item traces to a faithful model (`security-models.md`) and a flow
pattern (`llm-flow-patterns.md`) — that traceability is the contract,
not these prose summaries:

- **Agency containment** — no ambient authority; scoped, attenuable,
  delegable-only-narrower capabilities. → Object-capability; patterns
  ③/④.
- **No silent egress of tainted/personal data** — a session that has
  read sensitive/untrusted data is structurally blocked at the egress
  sink regardless of what the model "decides." → Denning lattice +
  Noninterference (controlled declassification); patterns ①/②/④.
- **Human oversight of consequential effects** — irreversible /
  committing / cross-compartment effects require human approval; the
  model is structurally excluded from authorizing. → Clark-Wilson
  (separation of duty); trust-model §2/§5.
- **Decision / flow accountability** — every allow/deny/escalate and
  the flow that produced it is replayable and answerable without
  interpreting the model. → Replayable AAA Audit + Materialized Provenance
  security.
- **Prompt-injection blast-radius reduction** — injection is *not
  prevented*; its consequences are neutralized at the chokepoint.

## 4. AI-governance coverage (the honest map)

| AI-governance concern | CapableDeputy |
|---|---|
| Excessive agency / bounded autonomy | **Strong, faithful** |
| Human oversight of consequential actions | **Strong** |
| Accountability / decision traceability | **Strong** |
| Prompt-injection *consequence* containment | **Strong (blast-radius, not prevention)** |
| Model accuracy / hallucination quality | **Out of scope** (contains what a hallucination can *do*, not whether output is wrong) |
| Bias / fairness / disparate impact | **Out of scope** |
| Model eval / red-team / benchmarking | **Out of scope** |
| Robustness / drift / model monitoring | **Out of scope** |
| Content safety / toxicity | **Out of scope** |
| Training-data & model provenance, model cards, the Govern function | **Out of scope** |

Verdict: **deep and faithful on one quadrant** (agentic-effect
containment + oversight + accountability + injection blast-radius);
silent on the model-quality / fairness / assurance majority of AI
governance. That quadrant is the one most AI-governance tooling is
*worst* at. This is a deliberate posture (Constitution VII), not a
backlog.

## 5. Explicitly out of scope, by governance category

These are **deliberate non-goals** (secure-by-reduction). Reviewers
must not flag their absence as a defect:

- **InfoSec program breadth** — availability/DoS, crypto, patching,
  supply-chain attestation of wrapped MCP servers, SOC/detection, and
  the security of CapableDeputy's own substrate (host, daemon, store).
  CapableDeputy is one deep control, not an InfoSec program, and
  *assumes* a trustworthy TCB it does not itself secure.
- **Privacy obligations** — lawful basis, consent, DSAR, DPIA, records
  of processing, retention/erasure. Note two structural facts, not
  just unbuilt features: (a) append-only audit is **in tension with
  subject erasure**; (b) the model is **operator-centric** (protects
  the operator's data from the operator's agent), not
  data-subject-centric. CapableDeputy has no concept of a data
  subject.
- **AI assurance** — everything in §4 marked out-of-scope.

## 6. The three contingencies that bound every claim

No in-scope guarantee holds unconditionally. Every grade above is
capped by:

1. **The labeling oracle.** Protections fire only on correctly labeled
   data. Mislabeled sensitive/personal data ⇒ the defense is silently
   absent. CapableDeputy assumes correct labels; it does not provide
   them.
2. **Substrate trust.** Host/daemon/store compromise voids the
   intersection defense. Securing the TCB is outside CapableDeputy's
   own scope.
3. **The inappropriate-influence case is unbuilt.** The intersection
   incident where *no data leaves and the effect is permitted* but a
   contaminated/biased automated decision is made about a person
   (purpose-contamination) is **v0.9 unspecced design**
   (`trust-model.md` §6; `design-v0.9-labeling.md`); pattern ①
   provably cannot stop it. CapableDeputy covers the
   *exfiltration/over-agency* archetype well, this one not yet.

## 7. Alignment with the original vision

**In line, and a sharpening — not a pivot.** The original vision
(README "Why"; `docs/index.md`) is: change the architecture so that
the lethal-trifecta outcome is unreachable even if every classifier
fails and the LLM is fully compromised, by building on classical
security models + the CaMeL/dual-LLM patterns. Restating "lethal
trifecta" as "the InfoSec×Privacy×AI triple overlap" changes the
vocabulary, not the target. The governance lens additionally makes
explicit what the `project-thesis` always held: breadth in AI / privacy
governance is **deliberately cut** (Constitution VII), which is the
same "deliberately less-capable secure alternative" stance, now stated
in external-framework terms so the scope cannot be misread.

## 8. The faithful-model / flow-pattern tracking discipline (unchanged)

This positioning does **not** relax the core discipline; it depends on
it. Restated so it is not lost:

- Every enforcement mechanism MUST trace to a named model in
  `docs/security-models.md`, with any deliberate deviation recorded
  there. A mechanism with no model lineage, or an undocumented
  deviation, is a reviewable defect (Constitution VIII).
- Every way the planner relates to labeled data MUST be one of the
  named patterns in `docs/llm-flow-patterns.md` (or added there and
  cross-linked to its model row).
- The model docs classify intent honestly as
  Priority / Approximate / Not-Pursued / Salvageable-as-mode. This
  scope doc names *which governance concern* each in-scope mechanism
  serves; it never substitutes for that classification.

Adding a capability still means: name its model, name its flow
pattern, document its deviation, or cut the capability. That is the
contract this doc exists to keep visible — not loosen.
