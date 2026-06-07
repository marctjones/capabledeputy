# Security-model / flow-pattern / AI-principle alignment assessment

A grounded read of how the implementation lines up with the three frames
it commits to — the **security models** (`security-models.md`), the **LLM
flow patterns** (`llm-flow-patterns.md`), and the **responsible-AI
principles** (`responsible-ai-frameworks.md`) — plus what the new Starlark
host changes. Written 2026-06; reflects the code at `v0.15.2-*`.

## TL;DR

- **The architecture is unusually faithful and unusually honest.** Where a
  model can't be fully met it is scoped to an *approximate* form and the
  gap is documented in-repo, not hidden. Fail-closed is the global default.
- **The biggest live gap is not a model deviation — it's that the whole
  decision-refinement layer is dormant.** The `DecisionInspector`
  chokepoint and the Starlark `PolicyScriptHost` are built and tested, but
  **nothing in the daemon loads or registers them** (`decision_inspectors`
  is always the empty default; no `get_script_host` call in `daemon/` or
  `app`). So today operators express policy only through the *declarative*
  `RulePredicate`, which cannot do negation, arithmetic, frequency, or
  cross-field logic. That coarseness pushes real workflows toward
  "always-approve," and approval fatigue is the practical thing that
  erodes human oversight (P5).
- **So: yes, we should write better policies — but first wire the host.**
  The single highest-leverage change is a config-driven loader that
  compiles operator scripts into `DecisionInspector`s. Then the nuance the
  cookbook describes (relationship-aware relax, after-hours tighten,
  frequency caps, per-purpose relaxers) becomes expressible.
- **Systemic weakness #1 is the labeling oracle.** Every IFC guarantee
  rides on correct labels; mislabeled data ⇒ defense is *silently absent*
  (governance-scope contingency #1). Strengthening label coverage (more
  `SourcePort` bindings, catalog-aware tiers, the raise-only LLM labeler)
  does more for real-world safety than any new model.

---

## 1. Starlark: should we redo policies? can we write better ones?

### What changed
`StarlarkScriptHost` gives a real, language-level sandbox for
operator-authored decision logic: a script defines
`inspect(action, session, proposed_outcome)` returning
`relax/tighten/abstain`, with no imports, no builtins, no I/O. The
`DecisionInspector` port + `_apply_decision_inspectors` chokepoint
(`tools/client.py`) already run inspectors after the base decision and
compose them most-restrictively (tighten beats relax).

### The blocker (must fix first)
`decision_inspectors` is **never populated** in the daemon. No loader
turns a `.star` file into a registered inspector. Until that exists, a
Starlark policy cannot affect any real decision — and neither can the
already-shipped builtin inspectors (`SelfEgressRelaxer`,
`AfterHoursPurchaseTightener`). **Action:** add a `policies:` block to the
daemon config + a loader in `daemon/lifecycle.py` that, per entry, calls
`get_script_host(kind).compile(...)` and wraps the compiled script in a
`DecisionInspector` adapter (bridging `ScriptOutcome` → `DecisionRelax/
Tighten`), appended to `PolicyContext.decision_inspectors`.

### What better policies become possible (and the RulePredicate can't do)
The declarative `RulePredicate` is conjunctive-only: AND across fixed
axis fields, a single time window, exact-match values. It cannot express
negation, arithmetic (`amount > X`), frequency ("> N sends/hour"),
"2nd Tuesday", or cross-field conditionals ("if counterparty ∉ family
*and* after hours → tighten"). Those are exactly Starlark's sweet spot —
e.g. one script replaces 6 RulePredicates + an exclude rule.

### Hard guardrails on Starlark policy (so it strengthens, not weakens)
1. **A script is a `DecisionInspector`, never a replacement for the
   structural floors.** The four always-on conflict invariants, BLP/Biba
   gates, capability checks, and envelope hard-floor cells run *before*
   inspectors and a script-`relax` must not be able to cross them. Compose
   so that `relax` is bounded by the envelope cell (FR-026 bounded-relax)
   and can never override a DENY floor.
2. **Inspectors can only see what we pass them.** Today that's
   `action/session/proposed_outcome` dicts — *no* session history or
   external state (Starlark is hermetic by design). Frequency/aggregation
   policies (defense T4) require us to first thread a read-only history
   summary into the `session` dict; without that the script can't do
   rate logic either.
3. **Keep `relax` scarce, `tighten` liberal.** A relax is an autonomy
   grant; a tighten is a safety add. The most-restrictive composition
   already enforces tighten-wins — lean on it.

**Verdict:** don't rewrite the existing YAML rules (they're fine, hard
floors belong there). *Add* a Starlark inspector layer for the nuanced,
fatigue-reducing relaxes/tightens the YAML can't express — after wiring.

---

## 2. Security-model alignment

Legend: **Structural** = enforced by construction; **Approx** = scoped
approximation (documented); **N/P** = deliberately not pursued.

| Model | Status | Aligns well | Can't / deviates |
|---|---|---|---|
| Reference Monitor | Structural | single `decide()` chokepoint, *strengthened* by LLM-isolation (model is outside the TCB) | totality depends on all dispatch routing through the chokepoint |
| Object-capability | Structural (scoped) | unforgeable, scoped, time/rate/prior-use attenuation; control-plane model-unreachable (stricter than classic ocap) | **single-parent tree, not a DAG**; US2 cascade-revoke computed at decide(), not eager |
| Clark-Wilson (core) | Structural | destructive-op gate + human approval (authorizer is human, beyond CW) + pattern ④ as TP | full UDI/CDI/TP/IVP formalism **N/P** (deliberate) |
| Gold-standard audit | Structural | append-only hash-chained log + pure-function `decide()` ⇒ replayable; cross-rotation verify | explains *decision/flow*, **not model cognition** (deliberate non-goal) |
| Least privilege / fail-closed | Structural | never-auto default, undecidable-subset refusal, CI-gated against fail-open | rides on correct labels (see §6) |
| Brewer-Nash | Structural (Priority) | four always-on conflict invariants computed from the axes | **per-session, not per-user-lifetime** COI history |
| Denning lattice / IFC | Partial (Priority) | four-axis labels, monotone `most_restrictive_inherit`, dynamic taint | **open category set, not a total-order lattice**; no formal join/dominance operator (pending) |
| Bell-LaPadula | Approx | dynamic **read-up refusal** via tier × clearance profile (FR-008) | **no write-down** enforcement; static *-property N/P |
| Biba | Approx | scoped **one-direction** integrity (provenance floor, untrusted-meets-egress) (FR-004) | **no write-up / no integrity clearances**; doc flags this as *"the most under-served model and easiest to wrongly assume covered"* |
| Noninterference | Approx | *intransitive* NI by construction + certified declassifiers (dual-LLM schema, human one-shot); per-tier: `restricted` now auto-selects pattern ③/⑤ in per-turn dispatch (#52, fixed) | whole-system transitive NI impossible (and N/P) |
| Provenance security | Approx | taint + single-parent delegation graph, immutable audit | single-parent (not multi-parent DAG); completeness contingent on reference-monitor totality |
| HRU safety | N/P | sidestepped by the capability model | general safety is undecidable (correctly not pursued) |

---

## 3. Flow-pattern alignment

| Pattern | Status | Aligns well | Gap |
|---|---|---|---|
| ① Tainted-context (turn-level) | Shipped | no *silent* egress, structural at chokepoint | unsafe for adversarial sources alone (mitigated by ②) |
| ② Quarantined / dual-LLM | Shipped | schema-*is*-declassifier; planner never sees raw bytes; + P1.7 char/entropy constraints | a confidential read with no quarantined extractor falls back to ① (weaker exposure, still fail-closed on egress) |
| ③ Reference handles | Shipped + wired (`reference_handle.py`, `_bind_reference_handles`, `select_mode_for_restricted`, and `select_mode` per-turn integration, #52) | planner holds opaque handles; bind audited; `restricted` tier now auto-selects ③ (or ⑤) per turn, fail-closed if neither available | — |
| ④ Programmatic / code-mediated | Shipped | planner authors a program, not data handling; dry-run vs policy; bundle TTL | never auto-selected from labels (deliberate — planner can't pick weaker protection) |
| ⑤ Sealed-effect / sandbox | Shipped | disposable, egress-free; Podman actuator | **containment ≠ declassification** (output keeps source labels) — a real footgun if assumed otherwise |
| Cookbook P1.1–P1.8 (security) | All shipped | fail-closed time-window, devbox gate, dual-LLM precondition, first-use prompt, hash-chain audit, output constraints, grant validation | — |
| Cookbook P2.1–P2.8 (approval-reduction) | All shipped | sibling-grouping, shadow mode, auto-narrowing, ttl grants, rate-limit-as-friction, rule shadow | their *payoff* depends on expressive relax policy — which is the dormant inspector layer |
| Approval profiles A–F | Shipped (policy patterns) | sensible default outcomes per workflow class | several profiles assume relationship-aware relaxes that need the inspector layer to realize fully |

---

## 4. AI-safety-principle alignment (the 8 enforceable principles)

| # | Principle | Grade | Can't / deviation |
|---|---|---|---|
| P1 | Least authority / bounded autonomy | Strong | single-parent delegation; tool-poisoning is substrate-level (out of scope) |
| P2 | Trusted/untrusted separation | Strong | **consequence-guard, not injection detection** ("an injected instruction can't cause unauthorized effect" — not "no injection"); one-direction Biba |
| P3 | Confidentiality / controlled flow | Strong | can't stop purpose-contamination; intransitive (not pure) NI |
| P4 | Purpose limitation | **Partial (honest)** | read-admissibility only; **purpose-contamination unbuilt** (R5; risk_register `RISK-PURPOSE-CONTAMINATION`) |
| P5 | Human oversight of consequential effects | Strong (by construction) | the practical risk is *rubber-stamp fatigue*, not a model gap |
| P6 | Accountability / traceability | Strong | decision/flow only (not model cognition); append-only vs GDPR-erasure tension |
| P7 | Fail-safe defaults | Strong (CI-gated) | rides on correct labeling at source |
| P8 | Containment / blast-radius | Strong | containment is **not** declassification (explicit, to prevent false inference) |

Scope claim is explicit and worth restating: **"control, not correctness."**
The system governs *who authorizes, what is touched, where data flows,
reversibility, accountability* — never whether model output is true / fair
/ unbiased (that would require trusting a classifier). It is also
**operator-centric, not data-subject-centric** (aligns with privacy
*principles*, does not discharge DSAR/erasure *obligations*).

---

## 5. Combined assessment — strengths & weaknesses

**Strengths (in combination):**
- The three frames reinforce each other: the security models give the
  *what*, the flow patterns the *how*, the AI principles the *why/scope*.
  A single mechanism often satisfies several at once (e.g. the conflict
  invariants = Brewer-Nash + P3 + intransitive-NI floor).
- Fail-closed + LLM-isolation + pure-function `decide()` + append-only
  audit form a coherent, *replayable*, model-distrusting core. This is the
  load-bearing strength and it is genuinely structural.
- Radical honesty: approximate models are labeled approximate; non-goals
  (interpretability, content safety, transitive NI, HRU) are stated, not
  fudged. This is itself a safety property (no false assurance).

**Weaknesses / where it under-delivers in practice:**
1. **Dormant decision-refinement layer** → only coarse YAML policy → more
   "always-approve" → **approval fatigue → rubber-stamping**, which is the
   real-world erosion of P5/human-oversight. (Most important.)
2. **Labeling oracle dependency** → mislabeled data silently disables the
   whole IFC edifice (governance contingency #1). Coverage of `SourcePort`
   bindings is thin (`source_bindings.yaml` ships empty).
3. **Biba under-implementation** flagged by the docs themselves as the
   easiest to wrongly assume covered.
4. **Purpose-contamination unbuilt** (a real EU-AI-Act/GDPR-relevant gap).
5. ~~Pattern ③ end-to-end auto-selection for `restricted`~~ — **fixed (#52)**:
   `select_mode` now enforces the restricted-tier floor per turn (③/⑤ or
   fail-closed). Note the consequence: with catalog-aware tiers (#50)
   making health/financial `restricted`, any session touching them now
   requires a Pattern ③/⑤ mode (the spawn gate already guaranteed this
   for real sessions).

**Where it actively undermines a model/principle (honest list):**
- Mostly it *under-covers* rather than *contradicts*. The genuine
  self-undermining risks are: (a) anyone reading "sandboxed ⇒ safe to
  send" (containment≠declassification) — a labeled footgun; (b) assuming
  Biba/NI are fuller than they are; (c) the dormant inspector layer making
  the *documented* approval-reduction patterns (P2.x, profiles A–F) not
  actually reduce approvals, which then undermines the oversight model via
  fatigue. None of these is a logic contradiction; all are
  expectation/coverage gaps that the docs largely pre-disclose.

---

## 6. What would strengthen alignment (prioritized)

1. **Wire the DecisionInspector / Starlark loader** (P5, P2.x, profiles).
   Turns the dormant layer on; lets operators express the fatigue-reducing
   relaxes the architecture already assumes. *Guardrail: scripts refine,
   never override structural floors; relax bounded by the envelope cell.*
2. **Grow the labeling oracle** (P2, P3, P7, IFC). Ship real
   `SourcePort`/binding coverage (the git/Gmail/Drive providers), and
   wire the raise-only LLM labeler so untrusted/sensitive reads get tagged
   even when the operator didn't pre-bind. This attacks weakness #2 — the
   highest-leverage *safety* improvement. *(String→tier resolution is now
   catalog-aware — #50, done.)*
3. ~~Verify + close Pattern ③ auto-selection for `restricted`~~ —
   **done (#52)**: `select_mode` enforces the restricted-tier ③/⑤ floor
   per turn, fail-closed when neither is available.
4. **Thread a read-only session-history summary into inspector inputs**
   (T4 aggregation, frequency policy). Without it, neither YAML nor
   Starlark can express "N reads → bump tier" / "> N sends/hour."
5. ~~Make the Biba gap loud~~ — **done (#53)**: `capdep policy models`
   prints each model's honest scope and flags Biba's one-direction limit
   in red.
6. **Surface purpose-contamination as a visible residual** (P4). Even
   without full enforcement, flag decisions where inadmissible-category
   data is in-context (a "contamination-suspected" audit signal) so it's
   not silently invisible.

## 7. Everyday-practice usefulness (consistent with the design)

- **A Starlark policy starter library** (once wired): after-hours-tighten,
  relationship-aware relax, per-purpose autonomy, frequency caps,
  reversible-write auto — the cookbook profiles as drop-in scripts.
- **Shadow-mode for new inspectors/rules** (already have P2.2/P2.8): let
  operators A/B a policy against real traffic before enforcing — removes
  the fear that tuning autonomy weakens safety.
- **More `VersionedWritePort` backends** (Drive/git/S3): each turns a write
  surface from always-prompt into act-but-undoably (`reversible/system`),
  directly cutting approvals without weakening safety — the cleanest
  fatigue win.
- **More `SourcePort` backends** (Gmail/Drive/calendar): canonical ids cut
  false approvals *and* harden the surfaces people actually use against
  confused-deputy redirection.
- **Operator-facing "why" surfacing**: the audit already explains every
  decision; expose a `capdep why <decision>` that prints the rule/floor/
  inspector that fired, to build operator trust and speed approvals.
