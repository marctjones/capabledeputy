# Trust Model — When an Action Proceeds, Who Authorizes, What the AI May Not Decide

**Status: design capture, not implemented.** This consolidates a trust
model specified across design discussion; it is **v0.9
`/speckit-specify` scope**, not built. It is the decision-layer
companion to the other three docs:

- `.specify/memory/constitution.md` — governance (Principles I–VIII).
- `docs/security-models.md` — formal-model lineage & deviations.
- `docs/llm-flow-patterns.md` — how a planner relates to labeled data.
- `docs/governance-scope.md` — what the system is *expected* to do
  (and deliberately not), in InfoSec/Privacy/AI-governance terms.
- **this** — *whether/when an action proceeds, who authorizes it, and
  the precise boundary of what the AI may propose vs. bind.*

## 1. Core stance

- **Never-auto by default.** An inbound trigger (email, message, tool
  result) MUST NOT cause information sharing or any consequential
  action automatically. The default outcome is **suggest to the human**
  (or require-approval / deny). Auto-action is a rare, explicitly
  carved exception — never the fallback.
- **Propose ≠ bind.** The AI may *propose, decorate, optimize, and
  explain*. The deterministic engine and the human *bind*. The model
  is never on the path that widens authority, lowers its own
  containment, or authorizes its own request.
- **Gaps fail to *ask*, never to *AI-allow*.** Where policy is silent,
  the deterministic default is escalate-to-human, not deny-dead-end and
  not model-decides. The only safe "flexibility" knob is
  **deny-vs-ask**, never **ask-vs-AI-allow**.

## 2. The multi-axis human-authored trust rule

An action's outcome is decided by a human-authored rule evaluated over
a cross-product of axes; absent a matching rule, the outcome is
*suggest/approve* (or deny) — never auto:

| Axis | Examples |
|---|---|
| **Initiator + authentication** | a cron *you* configured; an *authenticated* message from you; an *unauthenticated* email; the AI's own idea |
| **Effect / action** | read; share/egress; create; **destroy**; financial commitment; **social commitment to a third party**; code/shell orchestration |
| **Data type & sensitivity** | health, financial, personal, work-contacts, etc. × tier (releasable→prohibited) |
| **Counterparty / relationship** | spouse; sibling; "someone *the principal* (not the AI) has replied to"; colleague; unknown |
| **Context / expectedness** | 2 am matching a configured job = normal; the same request by daytime text = anomalous |
| **Reversibility / loss** | recoverable-from-backup / low-value / recreatable → relax; irreversible / high-value → tighten |

Outcome ∈ {auto-allow, **suggest**, require-approval, deny}. Reversible
+ non-sharing actions (delete recreatable data, create-but-don't-share)
*relax* toward auto **only via a human-authored rule over
human-declared facts** — never via AI inference.

## 3. Hard invariants (AI-read-only facts)

These facts gate authority and therefore MUST be human-declared; the
AI may *read and propose over* them but MUST NOT mint or assert them:

- **Trust graph** — relationship/contact trust edges are minted only by
  *authenticated principal* actions, never AI actions. The graph is
  AI-read-only (Biba/integrity on the trust object itself).
- **Recoverability metadata** — "this store is backed up / low-value /
  recreatable" is operator-declared per resource. An AI that could
  assert recoverability could destroy anything by being wrong or
  manipulated about it.
- **Purpose-admissibility** — which data categories are admissible
  *inputs* to which purposes is human-declared (see §6).
- **Initiator authentication** — claimed identity ≠ authenticated
  identity; only adapter-authenticated identity sets `trusted`
  provenance. An unauthenticated source can never self-elevate.

Violating any of these is the confused-deputy / self-elevation hole and
is a reviewable defect (Principle VIII).

## 4. The safe learning loop (practical without the AI deciding)

> AI **suggests** the action (or proposes a standing rule) → the
> **human** approves the instance and/or ratifies a multi-axis rule →
> the **engine** deterministically matches and applies that rule
> thereafter; anomalies let the AI *propose escalation*, never
> downgrade.

This is the generalization of human-ratified pattern-approval rules:
the policy *learns* via human ratification, never via the model.

## 5. Selector & approval-construction boundary

- **Mode/flow-pattern selection** is a pure deterministic function,
  LLM-isolated (`select_mode`). An LLM advisor, if ever added, is
  **escalate-only** below a deterministic floor, fully audited; it may
  request *stricter*, never weaker (ratchet-only).
- **Approval requests:** the engine owns the *verbatim bound action and
  the risk facts* (effect, irreversibility, amount, recipients, labels
  in scope), rendered independently and authoritative. The LLM may
  compose a *secondary, clearly-marked explanation/grouping* around
  that core. The human authorizes against the engine facts, never the
  model prose. Approve at the **capability/effect** abstraction (the
  irreversible effect + parameters), not per fine-grained step
  (bundled/minimum approvals).

## 6. Purpose limitation / inappropriate-context use

A distinct risk class: sensitive data *contaminating a decision it has
no legitimate bearing on* (e.g. health status influencing an employee
evaluation) — **no data leaves, nothing irreversible, no capability
violated**, yet real harm.

- **Pattern ① cannot prevent this** — once both are in the planner's
  context, the influence is unobservable; "ask the model to ignore it"
  is exactly the trust-the-model control we reject.
- **Only structural defense:** a **purpose-scoped session that never
  contains the conflicting category** — the eval session holds no read
  capability for `health`-category data, enforced at spawn (patterns
  ③/④/sealed). Brewer-Nash realized in the architecture, not in the
  model's good behavior.
- Therefore **purpose gates *category admissibility*, not only
  sensitivity tier**: some categories are *inadmissible inputs to a
  purpose regardless of sensitivity* ("health ⊄ inputs(employee-
  evaluation)"). This extends the v0.9 purpose axis.

## 7. Tracked gaps (all v0.9-spec scope; capture-only)

1. **Initiator/trigger provenance + authentication** as a first-class
   policy input (distinct from data provenance). Headline gap.
2. **Recipient/relationship trust** as a first-class scope.
3. **Multi-axis human-authored rule** expressiveness (the §2 cross-
   product), generalizing pattern-approval rules.
4. **Reversibility-weighted gating** (replace the binary destructive-op
   gate; FAIR loss-weighting; human-declared recoverability).
5. **Social-commitment effect class** (third-party commitment is
   irreversible reputationally even when "just an email").
6. **Purpose-scoped sessions with category-admissibility** (the §6
   inappropriate-context-use defense).
7. Carried from `security-models.md`: context-profile clearance;
   first-class pattern ③ / sealed-effect; integrity floor + no-read-
   down (Biba); formal lattice dominance.

## 8. Canonical scenarios (Principle III/VIII fixtures)

- **Forward a doctor's/friend's note:** to spouse = (rule) allowed; to
  colleague / unknown / **spoofed-wife** = not.
- **DB backup:** the cron *you* configured at 2 am = auto; the *same*
  effect from a daytime authenticated text = require-approval (AI may
  explain the anomaly, not auto-run); from an unauthenticated email =
  deny.
- **Recoverable vs irreversible delete:** delete of backed-up/low-value
  data (operator-declared) = no approval; irreversible delete =
  approve.
- **Inappropriate context:** health data MUST NOT enter an employee-
  evaluation session (purpose-scoped exclusion, not model restraint).

These are the definitive secure-but-practical tests; each becomes a
deterministic fixture when v0.9 is specced/implemented.

## 9. External-framework anchor: adaptive governance & Contextual Integrity

**Status: external-framework anchor (design capture), not implemented.**
This explains *why* §2 and §6 are the architecture rather than an
add-on, by tracing them to a recognized privacy-theory lineage. It is,
for the decision layer, the analogue of what `docs/security-models.md`
is for the enforcement layer: a map from an external model to our
mechanisms, with deliberate deviations named.

### 9.1 The external lineage

- **Contextual Integrity** (Nissenbaum): privacy = *appropriate
  information flow*, defined per context by ⟨sender, recipient,
  subject, attribute/type, transmission principle⟩. Norms are
  context-relative, never absolute.
- **Adaptive privacy governance / Contextual Privacy Policies**:
  governance that resolves those norms *dynamically, per-context, at
  runtime* — the opposite of static notice-and-consent / one-time DPIA.
- **CA-CI** (capabilities-approach + CI, 2026): CI extended to
  foundation-model agents whose capabilities shift across purposes;
  operationalizes EU AI Act fundamental-rights impact + anticipatory
  governance.

### 9.2 Contextual Integrity ⇄ CapableDeputy mechanism map

| CI element | Mechanism (this project) | Model row (`security-models.md`) |
|---|---|---|
| attribute / data type | information-flow labels (data-category × tier) | Denning lattice |
| sender / recipient | initiator-authentication + recipient-trust axes (§2) | (v0.9 gaps §7.1–.2) |
| transmission principle | the human-authored multi-axis rule (§2); conflict rules | Brewer-Nash; Access-matrix/HRU |
| context-relative norm ("health ⊄ inputs(employee-eval)") | purpose-as-category-admissibility (§6) | Brewer-Nash (extended) |
| norms resolved per situation, at runtime | per-action `decide()` chokepoint + context profile | Reference Monitor |
| norms updated | the §4 suggest → human-ratify → deterministic-apply loop | Clark-Wilson (sep. of duty) |

CapableDeputy is, in effect, **Contextual Integrity enforced
deterministically at agent runtime**.

### 9.3 Adaptive privacy ⇄ InfoSec ⇄ AI governance

The same convergence stated by *object of concern*:

| Discipline | Protects | Runtime question |
|---|---|---|
| InfoSec risk mgmt | the system & its assets | could a threat break CIA of this action? |
| Data & Privacy | personal data + subject's rights | is this *use/flow* appropriate to context & purpose, even if secure? |
| AI governance | the model's behavior & impact | could the model's decision/action cause harm, even with no breach & clean data? |

- **Unique residue:** InfoSec — availability, crypto, patching.
  Privacy — lawful basis, purpose creep, retention, subject rights.
  AI — hallucination, misalignment, excessive agency, opacity.
- **Pairwise overlap:** breach-of-personal-data (InfoSec∩Privacy);
  prompt-injection / confused-deputy (InfoSec∩AI); unintended-purpose
  or discriminatory automated decision (Privacy∩AI).
- **Triple overlap = the class this project targets:** an injected
  agent reads personal data and exfiltrates it via an unaccountable
  action — one incident, all three failures at once.
- **The runtime collapse:** all three are traditionally *design-time /
  periodic* (architecture review, DPIA, model card). An autonomous
  agent fuses them into a single **runtime** problem — which is exactly
  why enforcement must live at one always-invoked chokepoint, with the
  context-dependent parts (purpose, sensitivity, recipient trust,
  reversibility) resolved per action and the model kept out of the
  decision.

### 9.4 Where "adaptive governance" aligns — and where it is disarmed

The emerging idea is *correct in its diagnosis* (governance must be
runtime and context-adaptive) but carries a hazard: mainstream
adaptive-privacy / CPP work leans toward *automated, self-adjusting
policy*. If the adjusting component is the model (or anything
injectable), "adaptive" becomes a privilege-escalation surface — the
confused-deputy hole.

CapableDeputy adopts the diagnosis and **disarms the hazard**:

- adaptive in *context resolution*, but the adaptation is
  **deterministic** (§1 never-auto, §3 AI-read-only facts, §5
  ratchet-only selector);
- governance *learns* only via the §4 human-ratified loop, never via
  the model;
- the only flexibility knob is deny-vs-ask, never ask-vs-AI-allow.

So the project is the *disciplined* form of adaptive governance: it
supplies the tamper-proof enforcement substrate the CI/CPP literature
under-specifies.

Mapped onto the project's own models and patterns:

| Governance lens | Carried by (security model) | Carried by (flow pattern) |
|---|---|---|
| InfoSec confidentiality / no silent egress | Denning lattice; Noninterference (controlled declass.) | ① taint-tracking; ②/④ declassification |
| Privacy purpose-limitation / context norm | Brewer-Nash → purpose-as-category-admissibility (§6) | ③ reference-substitution; ④ code-mediated (purpose-scoped session never holds the inadmissible category) |
| AI excessive-agency / accountability / decision explainability | Object-capability (no ambient authority); Reference Monitor (LLM-isolation); Clark-Wilson (human sep. of duty); **Gold Standard (Audit) + Provenance security** | selector deterministic & LLM-isolated; ④ as certified transaction; append-only audit + pure-function `decide()` make every control-plane decision replayable & answerable |
| Adaptivity itself (the dial) | — | flow-pattern *strength* IS the privacy-adaptivity dial: stronger context norm ⇒ deterministically selected stronger pattern (①→②→③/④), never model-chosen |

The last row is the key alignment: "resolve the privacy norm by
context" is realized as **`select_mode()` deterministically choosing a
stronger flow pattern as the context tier rises** — adaptive output,
non-adaptive (LLM-isolated) mechanism.

Decision/flow explainability is therefore a **strength, not a gap**:
because the decision layer is a deterministic pure function of logged
inputs, every allow/deny/escalate — and the flow that produced it — is
reconstructible without interpreting the model (Gold Standard Audit +
Provenance security; `security-models.md`). The deliberate boundary:
this explains the *decision and the flow*, never *model cognition*
(interpretability is out of scope by design — trusting the model is
exactly what the architecture refuses), and a model's self-narrated
"reasoning" MUST NOT be recorded as if it were that explanation.

### 9.5 Honest limits (carries §7 gaps; no new claims)

- CI norms are socially negotiated; we flatten them to deterministic
  rules + human-ratified profiles — a deliberate robust-not-
  comprehensive reduction (Constitution VII), not full CI.
- EU AI Act FRIA / anticipatory governance is *design-time*; this is
  *runtime*. Complementary — we enforce the conclusions an assessment
  reaches, we do not perform it.
- Only *encoded* context adapts; genuinely novel context ⇒ fail-closed
  / escalate-to-human (the correct adaptive response per §1, but
  conservative vs. "automate everything").
- The **labeling-oracle** problem and the **Biba/integrity** half
  remain the residual hard problems (§7.7; `security-models.md`
  Approximate/Biba) — CI is confidentiality-flavored and does not
  close them.
- **Explainability is decision/flow-scoped, and that is the limit by
  design.** The decision layer is fully explainable and replayable
  (Gold Standard Audit + Provenance security); **model-internal
  interpretability is a deliberate non-goal** — opening that box means
  trusting the model. Logged model self-narration is not explanation
  and must not be presented as such. (An earlier framing that called
  explainability simply "absent" was wrong: it is present and strong
  at the decision/flow layer, and absent only where it is
  intentionally refused.)

### 9.6 References

- Nissenbaum, *Privacy as Contextual Integrity*.
- Benthall, *Adaptively Regulating Privacy as Contextual Integrity* (FTC).
- *Contextual Privacy Policies: The Next Evolution in Data Governance*.
- CMU Heinz, *CA-CI: a framework for privacy/dignity risks of modern AI*.
- Cornell DLI, *Privacy Policies as Contextual Integrity: Beyond Rules Compliance*.
