# AI Planning Delegation — What the Model May Plan, and What It May Never Decide

**Status: design record.** This is the *planning-layer* companion to
`docs/trust-model.md`. That document establishes the stance
("Propose ≠ bind"; the model is never on the path that widens authority,
lowers its own containment, or authorizes its own request). This document
answers the operational follow-up: **now that we want the AI to actively
plan multi-step work — choosing flow patterns, sequencing, decomposing
into sessions, selecting tools — exactly which of those planning acts are
safe to delegate to an untrusted model, and which stay in the TCB?**

Read alongside:

- `docs/trust-model.md` — whether/when an action proceeds, who authorizes.
- `docs/llm-flow-patterns.md` — the five ways a planner may relate to labeled data.
- `docs/security-models.md` — the formal-model lineage.

## 1. The delegation litmus (one rule)

> **The AI may plan *how* an intended effect is produced. It may never be
> the authority on *whether* an effect happens, *what* its observable
> output or destination is, or *what* privilege the session holds.**

Operationally: the runtime re-derives every security decision from
labels + effect **independently of what the AI proposed.** A planning act
is safe to delegate to the untrusted model iff a maliciously-chosen output
at that step can only ever be *privilege-reducing or risk-neutral* — i.e.
it moves toward **more** containment, never less. This is the same
"ratchet-only, stricter-never-weaker" direction that `trust-model.md` §5
permits for any LLM advisor sitting below a deterministic floor.

The corollary is the pair of **test invariants** the whole planning layer
is validated against. They are stated over the **TCB risk triple
`(effect, destination, labels-in-scope)`** — *not* over the observable
output. This distinction is load-bearing: a byte-identical payload to the
same destination is gated *differently* depending on the labels the session
carries (a clean `"ok"` vs an `"ok"` from a session that just read an
untrusted email — the latter could be injection-shaped). `trust-model.md`
§5 already names this triple as what the engine owns ("effect,
irreversibility, amount, recipients, **labels in scope**").

> **1. Determinism / purity.** Approval is a pure function of the TCB
> triple. AI prose, step ordering, decomposition, and *which tool name was
> used* are **not** inputs. This is what actually defeats approval-dodging
> by restructuring: reordering cannot change the approval because ordering
> is not in the triple.
>
> **2. Monotonicity.** The AI can change that function's output *only* by
> moving the triple toward lower risk **through a mechanism the TCB
> recognizes** — schema declassification shrinks labels-in-scope, sealing
> changes containment, an operator allowlist changes the destination's
> status. Never by masking risk or rearranging steps.

So a plan legitimately drops an approval exactly when it *changes the
taint* — raw data never reaches the untrusted planner, the effect runs
sealed, or the destination was operator-pre-allowlisted — and never merely
because the steps were rearranged so the human isn't asked.

Minimizing *incidental taint* is safe (it shrinks blast radius for a fixed
effect). Minimizing *approvals* is the attacker's objective (an approval
intrinsic to an action's risk exists precisely to get human eyes on it).
The two look like one optimization; the litmus separates them.

## 2. The catalog

### 2a. Safe to delegate — the model may plan these

Each is safe because a malicious choice can only add containment or is not
security-relevant; the runtime still gates every resulting `call()`.

| Planning act | Why safe |
|---|---|
| **Taint ordering** — do clean/high-value work first, defer tainting reads to the end or a sub-session | Pure sequencing; cannot expand privilege |
| **Session decomposition (split direction only)** — split into narrower purpose-scoped sessions | More compartments = smaller blast radius. *Safe only in the split direction.* A **merge** that co-mingles purpose-inadmissible categories (`trust-model.md` §6: health ⊄ inputs(eval) — harmful with no egress at all) is privilege-expanding and stays gated by human-declared purpose-admissibility. Passing a result *between* compartments is **not** free today (see §5b) — it requires a `decide()`-gated cross-session bridge, and the merge that reunites labels is where authorization lives |
| **Flow-pattern selection for a fixed effect** — quarantined-extract vs handle-routing vs sealed | Only narrows what reaches the planner; ratchet-toward-stricter |
| **Declassification-point placement** — where in the plan the schema projection converts tainted data to a clean fact | Schema is still the gate; AI only proposes placement |
| **Least-privilege capability requests** — "this task needs only `READ_FS` on `~/work`, one-shot" | The AI can only ask for *less*; it cannot self-grant |
| **Tool/provider choice on the *read/ingest* side** — which search provider, which MCP to query | Each is independently gated; a read, not an egress |
| **Model/cost/latency routing** — cheap model for scaffolding, strong model for the hard step | Not security-relevant |
| **Whole-plan advisory preview** — assemble the plan, run `policy.preview` across it, show the human the full shape and a lower-taint equivalent before anything runs | Advisory; floors still enforced on whatever executes |

### 2b. Never delegate — the TCB / human decides these

The AI may *propose* over these (that is `decide()`'s input), but the
authority stays in the deterministic engine and the human:

- Whether an effect needs approval.
- **The destination / recipient of an egress** — that *is* the risk.
- Whether a handle may be declassified or cross a boundary; whether a label may be dropped.
- Granting or widening a capability; lowering the session's own containment.
- Any tool choice made *specifically* to reach a worse destination or route around a gate.
- Minting any of the `trust-model.md` §3 hard invariants (trust-graph edges, recoverability metadata, purpose-admissibility, initiator authentication).

**The asymmetry to remember:** tool selection is safe on the *ingest/read*
side and unsafe on the *egress* side, because the egress destination is the
thing approvals exist to review.

## 3. Multi-step plans across flow patterns

A plan such as —

1. `quarantined.extract_inbox` → clean structured fact (projection declassifies)
2. handle-route the fact into `fs.create` (Pattern ③ — planner stays data-blind)
3. sealed computation over restricted data (Pattern ⑤)
4. egress to an operator-allowlisted destination (Pattern ③ + allowlist compose)

— is safe **by construction**: every `call()` is independently gated and
labels propagate through each step regardless of the order the AI chose.
The AI authoring this plan cannot make step 4 skip a gate.

One honest architectural note: today the flow patterns are whole-session /
whole-turn **modes** (`select_mode`). "Different patterns at different
steps" therefore wants either (a) **per-call** pattern choice inside a
PROGRAMMATIC program — patterns become *tool choices* (extract-tool vs
handle-aware-tool vs sealed-exec-tool) rather than a session mode — or
(b) **cross-session** handoff, each sub-session running its pattern and
passing handles forward. The *mechanisms* exist (PROGRAMMATIC + gating +
handle store); the missing piece is the planning layer that knows these
are options and composes them.

## 4. Invariants the test suite must enforce

The planning layer is only as safe as these properties. The suite is
adversarial: it feeds the runtime **planner outputs that try to cheat** and
asserts the runtime wins regardless.

1. **Determinism / purity** (property test). Approval is a pure function of
   the TCB triple `(effect, destination, labels-in-scope)`. AI prose, step
   ordering, decomposition, and which tool name was used are not inputs — so
   no AI-proposed reordering/decomposition/routing changes the approval set
   for a fixed triple.
2. **Monotonicity.** No AI-proposed plan step raises the session's tier
   floor, widens its capability set, or lowers its containment below the
   deterministic floor. The AI can move the triple only toward lower risk,
   and only through a TCB-recognized mechanism (declassification, sealing,
   allowlist) — pattern proposals may ratchet stricter, never weaker.
3. **Propagation soundness under every pattern.** Labels propagate
   correctly through whichever pattern the AI picks — letting the AI choose
   the pattern does not weaken this; it makes *every* pattern's propagation
   a hard runtime obligation. (Red-team: a plan that picks a pattern hoping
   its propagation is buggy.)
4. **No boundary self-authorization.** The AI cannot cause a handle to
   cross a session boundary, a label to drop, or a capability to be granted
   by *proposing* it — only `decide()` / the human can.
5. **Egress destination integrity.** An AI that selects a tool/provider to
   reach a different destination than the intended effect is gated on that
   destination, not on the AI's framing of it.

Red-team fixtures to include (planner is the adversary): plan reordering to
dodge an approval; proposing a wider capability "for efficiency"; routing
restricted data to an un-allowlisted URL via a "helper" tool; declaring a
resource recoverable to relax a destroy; using a quarantined-extract label
as cover to smuggle raw content.

## 5. Architecture implications

Three questions fall out of "let the AI plan multi-step work." Summary of
the design decisions; tracked as issues under the *Flow-aware planning*
milestone.

### 5a. A deterministic Plan Ledger (TCB-side), not an AI planning module

We do **not** want to re-pass the whole plan through the active AI session
each turn (token waste, and re-exposing prior context re-taints). We *do*
want a place to hold multi-step state across steps and sessions.

Resolution: a **deterministic Plan Ledger in the TCB** — a persisted DAG of
steps `{tool, proposed flow pattern, input handle(s), output handle,
status}`, advanced by the runtime. The AI *authors and updates* the ledger
via a tool call (a proposal, subject to the litmus above); the runtime
**executes and advances** it, gating each step through `decide()`. The
ledger is AI-authored data with **no authority of its own** — it is
convenience + state, exactly like a PROGRAMMATIC program elevated to a
persistable, multi-turn, cross-session object. This directly answers "do we
need a special planning module": yes, but it is a *state ledger the runtime
owns*, never a trusted planner.

The ledger is **runtime-advance-only**: the runtime marks steps
done/failed; the AI may *propose* new steps or *rewrite pending* ones, but
every such proposal is subject to the §1 litmus and re-gated at execution.
A rewrite is just another proposal — Determinism/purity must hold across
rewrites, so it is designed in, not retrofitted.

### 5b. Async / parallel sessions

The substrate already supports concurrency: the daemon runs *different*
sessions concurrently behind a **per-session turn lock**
(`session_coordinator`); turns within one session serialize. What is missing
is *orchestration* — a plan executor that fans independent steps out to
parallel child sessions and joins results.

But joining results is **not free today.** `reference_handle.py` uses
per-session UUIDs *specifically to prevent cross-session smuggling*, and the
handle store is per-session by design. So parallel decomposition needs a
**new, `decide()`-gated cross-session handle bridge** — a first-class TCB
mechanism with its own risk surface. The *split* direction is inherently
safe (privilege-reducing); the **merge** that reunites labels from parallel
compartments is exactly where authorization lives, and the bridge MUST
re-run `decide()` on the handle's frozen labels at the boundary. This is its
own milestone issue, not something the current store gives us free. Given
the bridge, add **bounded** parallel execution of independent ledger steps;
the reference monitor remains the serialization point for any shared
mutation.

### 5c. Keeping track of "the state of things"

State is already a graph, and the plan becomes a first-class node over it:

- **Session graph** (`SessionGraph` + store) — the compartments: parent
  links, status, `label_state`, `capability_set`, `reference_handles`,
  persisted.
- **Plan Ledger** (5a) — the steps, their status, and the handle wiring
  between sessions.
- **Handle store** — the data within a compartment (per-session today);
  the cross-session bridge (5b) is what lets it flow *between* compartments.
- **Audit log** — the immutable history / replay.

"The state of things" is then queryable: which steps are done/pending,
which sessions are tainted to what tier, which handles are live and where
they landed. No new global state store is needed — the plan ledger links
the existing three.
