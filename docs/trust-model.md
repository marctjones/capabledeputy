# Trust Model — When an Action Proceeds, Who Authorizes, What the AI May Not Decide

**Status: design capture, not implemented.** This consolidates a trust
model specified across design discussion; it is **v0.9
`/speckit-specify` scope**, not built. It is the decision-layer
companion to the other three docs:

- `.specify/memory/constitution.md` — governance (Principles I–VIII).
- `docs/security-models.md` — formal-model lineage & deviations.
- `docs/llm-flow-patterns.md` — how a planner relates to labeled data.
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
