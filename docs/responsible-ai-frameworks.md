# Responsible-AI Principles — the Actionable Core

**Purpose.** The distilled, actionable set of responsible / safe-AI
principles CapableDeputy actually enforces — and an honest line around
the ones it does not. This is the third companion to
`docs/security-models.md` (formal-model lineage) and
`docs/llm-flow-patterns.md` (planner↔data patterns). Those two say *how*
the enforcement works; this one says *which responsible-AI principle
each mechanism serves, organized so the core principles are visible and
not buried under every framework's restatement of them.* It is the
source material for the project's **default policy templates**: each
core principle below is meant to become a `rules.yaml` /
`purposes.yaml` / capability-grant default.

**The thesis — control, not correctness.** Read this first; it decides
everything else:

> CapableDeputy's job is to keep the **human in control of what the
> agent does**, not to make the **model correct**. A principle is
> **in scope** when it is about *who authorizes an action, what it may
> touch, where its data may flow, whether it can be undone, and whether
> it is accountable.* It is **out of scope** when it is about whether
> the model's output is *true, fair, unbiased, or safe in content* —
> because deciding that would mean trusting a classifier, which the
> architecture refuses by construction (Constitution I/II).

**Status: actionable principle set + alignment map (peer to
`security-models.md` in altitude and discipline; subordinate to it as a
contract).** The formal models remain the binding yardstick (Principle
VIII). The frameworks here are alignment *targets*, not formal models.
Overclaiming an alignment — or quietly promoting an out-of-scope item
into a covered one — is a reviewable defect here, exactly as an
undocumented model deviation is in `security-models.md`.

## The core risks every framework is built around

Strip the vocabulary from Meta's Rule of Two, the OWASP lists, NIST AI
RMF, GDPR, and Privacy-by-Design and the same six root risks remain.
Four are *control* risks (in scope); two are *correctness/substrate*
risks (out of scope):

| | Core risk | In scope? | Because |
|---|---|---|---|
| **R1** | Excessive / unbounded agency | ✅ | a property of the *action's authority* |
| **R2** | Agent manipulated by untrusted input (injection, goal-hijack) | ✅ | a property of *information flow* (we contain the consequence, not detect the attempt) |
| **R3** | Inappropriate disclosure / use of data (exfiltration, purpose creep) | ✅ | a property of *flow + purpose* |
| **R4** | Unaccountable / unauthorized consequential action | ✅ | a property of *oversight + audit* |
| **R5** | Model produces wrong / biased / toxic output | ❌ | a property of *output content* — judging it needs a classifier |
| **R6** | Compromised substrate / supply chain | ❌ | a property of the *TCB* — a contingency, not policy |

R1–R4 are the entire "keep the human in control of the agent's actions"
half of responsible AI. R5–R6 are the "is the model good" half, handled
by *complementary* controls (eval, fairness audit, content filters,
attestation) that are deliberately not this project's job.

## The eight core principles (the actionable list)

These eight absorb the many framework restatements of the same idea.
Each is **expressible in the policy language** and is the basis of a
default policy template. Ordered by the risk they guard.

### 1. Least authority / bounded autonomy — guards R1
No ambient authority; an agent holds only the scoped, attenuable,
expiring authority a task needs.
- **Mechanism:** object-capability — kind × scope-pattern × amount,
  with one-shot / time-bound / rate-limited / prior-use revocation
  (Principle IV).
- **Default template:** capability-grant defaults (prefer one-shot and
  session-scoped over persistent; narrow scope patterns).
- **Canonicalizes:** Rule-of-Two property bound, OWASP ASI04
  (privilege abuse) + LLM "excessive agency", least-privilege.

### 2. Trusted / untrusted separation — guards R2
Untrusted content must never acquire the authority of a trusted
instruction.
- **Mechanism:** Axis-B provenance/integrity taint + Biba-direction
  floor; flow patterns ② (quarantine) and ③ (handles) keep untrusted
  bytes away from the planner's authority.
- **Default template:** integrity-floor rules; auto-select ②/③ for
  `external-untrusted` provenance.
- **Canonicalizes:** lethal-trifecta [A], Rule-of-Two [A], OWASP LLM01
  (prompt injection), OWASP ASI01 (goal hijack).
- **Honest scope:** expresses the *consequence guard*, **not**
  detection. "Prevent injection" is not expressible; "an injected
  instruction cannot cause unauthorized effect/egress" is.

### 3. Confidentiality / controlled information flow — guards R3
Data a session has read cannot leave through an inappropriate sink,
whatever the model "decides".
- **Mechanism:** Denning lattice taint (Axis A) + egress block at the
  chokepoint; patterns ②/③/⑤ minimize exposure.
- **Default template:** egress rules keyed on data-category × tier ×
  recipient-trust (Axis D).
- **Canonicalizes:** lethal-trifecta [C], OWASP LLM06 (sensitive-info
  disclosure), GDPR integrity-&-confidentiality, Contextual Integrity.

### 4. Purpose limitation / appropriate use — guards R3
A data category inadmissible for the session's declared purpose cannot
be read; data is admitted for a purpose, not in general.
- **Mechanism:** Purpose Handle + Brewer-Nash-extended category
  admissibility (FR-009/046); patterns ②/③ deliver derived facts or
  opaque handles, never the raw datum (data minimisation).
- **Default template:** `purposes.yaml` admissibility matrix.
- **Canonicalizes:** GDPR purpose-limitation + data-minimisation,
  FIPPs use-limitation, Contextual Integrity's transmission principle.
- **Honest scope:** expresses purpose as *read-admissibility* and tests
  spawn/grant/delegation refusal for inadmissible categories. The narrower
  *model-reasoning contamination* case — admissible data, no egress, but it
  inappropriately influences a permitted decision — is **not expressible**
  (it requires inspecting the model's reasoning; it is R5). This is the
  project's known floor (`governance-scope §6.3`).

### 5. Human oversight of consequential effects — guards R4
Irreversible, committing, or cross-compartment effects require a human;
the model is structurally excluded from authorizing.
- **Mechanism:** approval state machine + reversibility-weighted gating
  + distinct `OverrideRequired` path (Principle V); the human reviews
  the verbatim payload.
- **Default template:** `rules.yaml` gating stanzas + override policy
  (`disallowed | single-authorized | dual-control`).
- **Canonicalizes:** Rule-of-Two "supervise when all three", EU AI Act
  Art 14 human oversight, Clark-Wilson separation of duty.
- **Honest scope:** oversight is of the *effect*, not the *correctness*
  — a human authorizes that an action may happen, not that the model
  was right to want it. See "The role of the human" below.

### 6. Accountability / traceability — guards R4
Every decision and the flow that produced it is reconstructible and
answerable without interpreting the model.
- **Mechanism:** append-only, hash-chained audit + `decide()` as a pure
  function of fully-logged inputs (replayable decision record).
- **Default template:** audit is always-on; no template needed, but
  retention/verification defaults belong here.
- **Canonicalizes:** GDPR/FIPPs accountability, NIST RMF *Govern*
  (decision-record slice), Replayable AAA Audit.
- **Honest scope:** explains the *decision/flow*, never model cognition
  (interpretability is a deliberate non-goal); a model's self-narrated
  "reasoning" is not logged as explanation.

### 7. Fail-safe defaults — guards R1–R4
On any input it cannot confidently and deterministically classify, the
system refuses or assumes the most-restrictive outcome.
- **Mechanism:** fail-closed admission (Principle VI), proven by a
  CI-enforced test that fails the build on any fail-open regression.
- **Default template:** the never-auto default (absent a matching rule
  ⇒ `suggest`/`deny`, never `auto`, FR-011).
- **Canonicalizes:** Privacy-by-Design "privacy as the default",
  safe-by-default, defense-in-depth.

### 8. Containment / blast-radius limitation — guards R1
Prefer reversible, egress-free, disposable execution; bound what a
compromised or erring agent can affect.
- **Mechanism:** disposable isolation region (pattern ⑤),
  `EXECUTE.sandbox` gating, reversibility composition (contained +
  egress-free ⇒ reversible-by-construction).
- **Default template:** isolation-posture defaults — sandbox is the
  preferred execution tier; un-actuated `EXECUTE.sandbox` ⇒
  `OverrideRequired`.
- **Canonicalizes:** OWASP ASI05 "sandbox all code execution",
  Rule-of-Two "drop property [C]".
- **Honest scope:** containment is **not** declassification
  (`llm-flow-patterns.md` invariant #7).

## The role of the human — who is in the loop, when

"Humans in control" is not one setting; it is a **ladder**, and the
policy engine deterministically places each action on the right rung
from its effect class × reversibility × tier × purpose. The model can
ratchet *up* the ladder (toward more human control) but can never move
itself *down* (Principle I; the `select_mode` / decision selector is
ratchet-only). Definitions vary across the literature; these are the
ones this project uses.

| Position | What it means | CapableDeputy mechanism | When it applies |
|---|---|---|---|
| **Human-over-the-loop** (a.k.a. human-in-command) | The human *authors the policy* the system then runs without them. The human governs from above. | Operator-authored `rules.yaml` / `purposes.yaml` / override policy; capability grants; the suggest → human-ratify → deterministic-apply loop. Control-plane is user-driven and **model-unreachable** (Principle IV). | Always — this is the foundation; every rung below executes the policy a human set from here. |
| **Human-in-the-loop** (HITL) | The agent **pauses**; a human approves the specific action before it executes. | `decide()` → require-approval / `OverrideRequired`; the approval FSM with verbatim-payload review (Principle V). | Consequential / irreversible / committing / egress-of-sensitive / floor-crossing actions. The never-auto default (P7) lands here by default. |
| **Human-on-the-loop** (HOTL) | The agent **acts autonomously**; the human monitors and keeps veto/override at the system level. | Optimistic execution of reversible, contained, non-egressing actions (FR-034); the human watches via audit, SHADOW-mode rules (observe-before-enforce), first-use banners, rate-limit-as-friction escalation, and intervenes by revoking capabilities or tightening rules. | Reversible / low-stakes / sandboxed work where per-action approval would be friction without safety gain. |
| **Human-out-of-the-loop** | No human; fully autonomous. | **Refused for consequential effects by construction** (never-auto). Permitted *only* for reversible, egress-free, contained actions — where "out of the loop" is safe because the action is undoable and leaks nothing. | The optimistic / sealed-isolation posture, and only there. |

The single most important property: **the model never chooses its own
rung.** Which loop position applies is a deterministic function of the
action's structural properties, set by policy a human authored
(human-over-the-loop) — so a manipulated or hallucinating model cannot
demote a consequential action from "in-the-loop" to "out-of-the-loop".
That is what "keeping the human in control" means here, mechanically.

## In scope / out of scope (binding — do not flag out-of-scope as a defect)

**In scope** — the eight principles above (R1–R4): control of the
agent's authority, information flow, purpose, oversight, and
accountability.

**Out of scope** — the correctness/quality and substrate family
(R5/R6). These are deliberate non-goals (Constitution VII), restated so
reviewers do not flag their absence; they belong to *complementary*
controls, not capdep policy:

| Out-of-scope concern | Why not expressible | Complementary control |
|---|---|---|
| Output correctness / non-hallucination | requires judging truth of output | eval / testing |
| Fairness / non-discrimination / bias | population statistics + content judgment | bias/dataset audit |
| Content safety / toxicity | content classification = the rejected perimeter classifier | content-filter layer |
| Robustness / drift | measuring model behavior over time | model monitoring |
| Model-reasoning transparency | interpretability is a deliberate non-goal | — (decision/flow audit is the substitute) |
| Supply-chain / training-data provenance | design-time substrate concern | SAIF / attestation |

**Two partials** (a principle is in scope only in one direction —
state it carefully or you overclaim): injection (P2: consequence-guard
yes, detection no) and purpose limitation (P4: read-admissibility yes,
contamination-of-reasoning no).

**Privacy posture, two structural facts** (from `governance-scope §5`,
binding here): CapableDeputy is **operator-centric, not
data-subject-centric** — it has no concept of a data subject, so it
aligns with privacy *principles* (purpose limitation, minimisation,
accountability) but does **not** discharge data-subject *obligations*
(consent, DSAR, erasure); and **append-only audit is in tension with
erasure / storage-limitation** — a deliberate trade-off favoring
accountability.

## The frameworks this distills (reference, not the focus)

The value of this doc is the eight principles + the human-control
ladder above; the source frameworks are listed only so each principle's
lineage is checkable. Responsible/safe-AI: Meta *Agents Rule of Two*,
the lethal trifecta (Willison), OWASP Top 10 for Agentic Applications
(ASI01–10) and for LLM Applications, NIST AI RMF + GenAI Profile,
Google SAIF, MITRE ATLAS. Data & privacy: Contextual Integrity
(Nissenbaum), GDPR Art 5, FIPPs/OECD, Privacy by Design (Cavoukian),
NIST Privacy Framework, EU AI Act Art 14.

## How this is tracked

- Each core principle MUST trace to a mechanism + a `security-models.md`
  row (and, if planner/data-flow, a `llm-flow-patterns.md` pattern). A
  principle with no underlying mechanism is aspiration, not alignment —
  a reviewable defect (Principle VIII).
- Adding a default policy template MUST cite the principle it
  implements; changing which loop-rung an effect class lands on MUST
  update the human-role table.
- The failure mode this file guards against is **overclaim**: a "guards
  Rx" the mechanism does not earn, an out-of-scope item quietly
  promoted, or a partial stated as total (candidate `/speckit-analyze` /
  constitution-gate check).

## Cross-reference

- `docs/security-models.md` — the binding formal-model yardstick (every
  principle here maps onto its rows).
- `docs/llm-flow-patterns.md` — the planner↔data patterns that realize
  principles 2–4 and 8.
- `docs/trust-model.md §9` — Contextual Integrity & adaptive-governance
  anchor (the data/privacy lineage; the decision-layer human-authority
  model).
- `docs/governance-scope.md` — the in/out-of-scope statement that binds
  the scope section here.
- `docs/policy-rule-structure.md` — how the eight principles become
  default rules (the PRO-over-CORE lens) + the CORE vs CapableDeputy
  comparison.
- `docs/source-bindings.md` — the labeling layer (CORE Resources) the
  whole model depends on, and the safe LLM-labeler pattern.
- `.specify/memory/constitution.md` — Principles I–VIII; IV (control
  plane is human-only), V (human-in-the-loop FSM), VI (fail-closed),
  VIII (alignment-and-deviation discipline binding).

## References

- Meta — [Agents Rule of Two](https://ai.meta.com/blog/practical-ai-agent-security/)
- OWASP — [Top 10 for Agentic Applications / Agentic Skills Top 10](https://owasp.org/www-project-agentic-skills-top-10/); OWASP Top 10 for LLM Applications
- NIST — AI RMF (AI 100-1) + GenAI Profile (AI 600-1); Privacy Framework v1.0
- Google SAIF; MITRE ATLAS
- Ann Cavoukian — Privacy by Design, 7 Foundational Principles
- EU — GDPR Art 5 & Art 14; OECD Privacy Guidelines (FIPPs)
- Helen Nissenbaum — *Privacy in Context* (Contextual Integrity)
- Human-oversight models — [human-in-the-loop vs human-on-the-loop](https://www.elementum.ai/blog/human-in-the-loop-vs-human-on-the-loop) (definitions vary by source; this project's usage is fixed in the table above)
