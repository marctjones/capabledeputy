# Security-model / flow-pattern / AI-principle alignment assessment

A grounded, current read of how the **implementation** *and* the **shipped
default policies** line up with the three frames memsafe commits to — the
**security models** (`security-models.md`), the **LLM flow patterns**
(`llm-flow-patterns.md`), and the **responsible-AI principles**
(`responsible-ai-frameworks.md`). Refreshed 2026-06-08 against `main` at
v0.18-dev (post trust-profile A–D, FR-019 egress amendment, certified-
declassification fix, and assurance slices #2–#4). Supersedes the
pre-v0.16 version of this doc.

This assessment rates **two columns separately** — `Impl` (what the engine
code enforces) and `Default` (what the *shipped* configs actually turn on) —
because the gap between them is the whole story. Legend: 🟢 strong · 🟡
partial · 🔴 weak.

## TL;DR

- **memsafe has two layers, and they earn different grades.** An *always-on
  structural core* of engine invariants (reference monitor, the four conflict
  floors, object-capability, IFC propagation, fail-closed defaults, hash-
  chained audit) is config-independent and genuinely strong. A *configurable
  layer* (rules, purposes, clearance, envelopes, inspectors, labeling) is
  strong **in code** but, in a few load-bearing places, **inert in the
  shipped defaults**.
- **The single highest-leverage gap is the labeling oracle shipping off.**
  The content-scan labelers (`email_label_rules`, `fs_label_rules`) ship as
  `.example` only. Every model in §1 rides on accurate labels, so this one
  default gap silently degrades IFC, BLP, *and* Brewer-Nash at once — and it
  *also causes approval fatigue* (mislabeled-benign data gets over-gated).
- **The decision-inspector layer is now wired (v0.16, #46) but ships with no
  inspectors.** It's live in code, inert by default — so the fatigue-reducing
  relaxes the architecture assumes are the operator's homework.
- **Weakest in code:** the integrity/blast-radius corner — Biba, non-
  interference, and P8 containment are jointly soft (see §4).
- **What's not a gap:** the system never *permanently* blocks an intended
  action. Under the `personal` trust profile (v0.17), the operator is root of
  trust and can override any floor with friction; the human-in-the-loop is a
  decision point, not a wall.

---

## 0. The two layers

| Layer | What's in it | Config-dependent? | Overall |
|---|---|---|---|
| **Structural core** | `decide()` chokepoint; the four conflict invariants; capability match + attenuation + cascade-revoke; IFC label propagation; reversibility gate; never-auto default; hash-chained audit | No — fires regardless of config | 🟢 |
| **Configurable layer** | clearance profiles, purposes/admissibility, decision rules, envelopes + dial, decision inspectors, the labeling oracle, override/ratification policy, source bindings | Yes — only as strong as the shipped/authored config | 🟡 (uneven) |

Everything strong in this assessment lives in (or is guaranteed by) the
structural core. Everything soft is either a code gap in the core (Biba, NI,
containment) or a *default-inert* spot in the configurable layer.

---

## 1. Security-model alignment

| Model | Impl | Default | Where it stands |
|---|---|---|---|
| Reference monitor (total mediation) | 🟢 | 🟢 | Single always-on `decide()` chokepoint; every tool call gates before the handler runs; no unmediated path. Caveat: session shadow-mode (operator opt-in) logs-but-allows by design. |
| Bell-LaPadula (read-up) | 🟢 | 🟢 | Clearance check is code-gated on `profiles`, but `profiles.yaml` **ships 4 clearance ceilings**, so read-up refusal is live out of the box. Deliberate: no write-down / static \*-property. Minor gap: no startup guard that profiles loaded. |
| **Biba (integrity)** | 🟡 | 🔴 | Mechanism exists (integrity-floor check + provenance lattice + FR-018 control-plane reflexivity) but needs a per-operation `required_floor` few tools declare, and there is **no session-wide integrity taint**. The repo's own "most under-served" flag is accurate. |
| Brewer-Nash (conflict) | 🟢 | 🟢 | Four always-on conflict invariants (`untrusted/health/financial × egress`) computed from the axes, regardless of config. `personal` profile may suppress 3 of 4 over the operator's *own* data — never `untrusted-meets-egress`. |
| Clark-Wilson (gated txn + sep-of-duty) | 🟢 | 🟡 | Destructive-op gate + certified-declassification transaction always-on. Separation-of-duty (dual-control) is config-gated and **DISALLOWED by default** (managed posture) — safe, but no forced second human out of the box. |
| Object-capability (confused-deputy) | 🟢 | 🟢 | No ambient authority; scoped/attenuated caps; cascade-revoke; reference-handle destinations pinned. In-process handle store is a known boundary (spec 004). |
| IFC / sticky labels (Denning) | 🟢 (A+B) | 🟡 | Propagation + taint accumulation always-on — **but the labels it rides on are thin by default** because the content-scan oracle ships `.example`-only. Axis C/D not yet sticky. |
| Noninterference | 🔴 | 🔴 | Only read-up refusal; no session-to-session effect isolation or side-channel reasoning. Intransitive NI holds by declassifier design; transitive NI is an explicit non-goal. |

---

## 2. Flow-pattern alignment

| Pattern | Impl | Default | Where it stands |
|---|---|---|---|
| ① turn-level gating | 🟢 | 🟢 | Every turn through the chokepoint. |
| ② dual-LLM quarantine / declassify | 🟢 | 🟡 | Extractor + `quarantined.extract` tool + schema-is-declassifier ship and are adversarially proven (slice #4). But **automatic** declassification-on-read is config-gated; absent a wired declassifier a confidential read falls back to ① (still fail-closed on egress). |
| ③ reference-handle (redirection-resist) | 🟢 | 🟢 | `handle_store` is in the default `PolicyContext`; `restricted` tier auto-selects ③/⑤ per turn (#52), fail-closed if neither available. Adversarially proven (slice #3). |
| ④ programmatic / code-mediated | 🟢 | 🟢 | Planner authors a program; dry-run vs policy; bundle TTL. Never auto-selected from labels (deliberate). |
| ⑤ sealed containment | 🟢 | 🟡 | Podman actuator hardened (`--net=none`, `--cap-drop=ALL`, read-only, fail-closed → `OVERRIDE_REQUIRED` if unwired). **Not wired by default and not mandatory** for sensitive work; only *recommended* via `purposes.yaml`. Footgun (pre-disclosed): containment ≠ declassification. |

---

## 3. AI-safety-principle alignment

| Principle | Impl | Default | Where it stands |
|---|---|---|---|
| P1 least authority | 🟢 | 🟡 | Strong scoping/attenuation; egress caps deliberately *not* preloaded (every send → approval), UNSET purpose fails closed. |
| P2 trusted/untrusted separation | 🟢 | 🟢 | Provenance floor; untrusted-egress **never** rule-relaxable (refused at load *and* compose). It's a consequence-guard, not injection detection. |
| P3 confidentiality / controlled flow | 🟢 | 🟢 | Health/financial/untrusted egress floors + a shipped PHI-egress DENY rule. |
| P4 purpose limitation | 🟢 | 🟡 | Admissibility enforced *at spawn when a purpose is named*; 4 purposes ship. Bare session = UNSET (admits reads, refuses consequential effects). Purpose-**contamination** (in-context mixing) is unbuilt. |
| P5 human oversight | 🟢 | 🟢 | Never-auto default + approval/override/ratification FSMs; model can suggest but never author an AUTO (FR-031). Real risk is *fatigue*, not a gap. |
| P6 accountability / traceability | 🟢 | 🟢 | Hash-chained append-only audit, pure replayable `decide()`, tamper-evident verifier across rotations. |
| P7 fail-safe defaults | 🟢 | 🟢 | Fail-closed on no-cap / malformed config / unlabeled / missing actuator; managed + empty override/ratification all refuse. |
| P8 containment / blast-radius | 🟡 | 🟡 | Session isolation + reversibility gating + cascade-revoke, **but fork inherits parent caps/labels** (by design, FR-058) rather than fresh-slate, and sealed execution is optional. |

**Scope claim (unchanged, worth restating): "control, not correctness."** The
system governs *who authorizes, what is touched, where data flows,
reversibility, accountability* — never whether model output is true / fair /
unbiased. It is **operator-centric, not data-subject-centric** (aligns with
privacy *principles*; does not discharge DSAR/erasure *obligations*).

---

## 4. The intersection of the three frames

**Positive intersection — one mechanism, many guarantees.** The four always-on
conflict invariants are *simultaneously* Brewer-Nash ∩ P3-confidentiality ∩
the intransitive-noninterference floor — and they make patterns ②/③ *defense-
in-depth rather than load-bearing*. The reference monitor + capability model +
hash-chained audit jointly discharge P1+P6+P7 with zero config. This is why
the strong cells cluster: they are the *same* structural core seen through
three lenses. This core is config-independent and solid.

**Negative intersection #1 — the integrity/containment corner is jointly
soft.** Three frames are weak *in the same place*: **Biba** (model) ∩ **P8
containment** (principle) ∩ **⑤ sealed-by-default** (pattern). Biba has no
session-wide integrity taint, P8's fork inherits authority, ⑤ isn't mandatory.
A *low-integrity, bounded-blast-radius* guarantee is the one corner where no
frame carries the others.

**Negative intersection #2 — the default labeling gap sits *underneath* all of
§1.** IFC, BLP, and Brewer-Nash are only as good as the labels on the data,
and the content-scan oracle ships off by default. So a fresh deployment
silently degrades *every model in §1 at once* on any data that lacks an
inherent tag. It is not a code weakness (the engine is correct); it is that
the shipped defaults leave the foundation unconfigured — and it is the only
default gap that propagates across the whole security-model axis.

---

## 5. Warning the human — current surfaces and the missing tier

**What exists (reactive, at decision time):** when an action is gated, the
human gets the engine-authored `rationale` + the *specific rule/floor that
fired* (e.g. `health-meets-egress`), structured pasteable `recovery_steps`
(Issue #3), and `capdep why <decision>` to replay why. Crossing a hard floor
requires **typed friction acknowledgment** of the specific irreversible effect
(`override request --friction-confirmed`, scaled LOW/MEDIUM/MAXIMAL). So the
human is never asked to confirm blind — they always see *what* and *why*.

**What's missing:**
1. **No proactive / pre-flight warning.** The warning fires when the action is
   *attempted and gated*, not *before the human commits to a plan*. There is
   no "you're about to set up a flow that will hit a floor" anticipation.
2. **No non-blocking advisory tier.** The decision lattice is
   `ALLOW < REQUIRE_APPROVAL < OVERRIDE_REQUIRED < DENY`. There is no
   **WARN-and-proceed**: a borderline action is either silently allowed or it
   *blocks* on an approval. (`SUGGEST` exists as a rule outcome but collapses
   to `REQUIRE_APPROVAL`.)
3. **No model/principle-level framing for the human.** The surfaced reason is
   the operational rule name, not "this violates Brewer-Nash / purpose
   limitation." `capdep policy models` documents model scope but isn't wired
   into live decisions.

This missing **advisory/WARN tier is itself one of the best anti-fatigue
levers** — see §6.

---

## 6. Fixing the weak spots without causing approval fatigue

**The governing principle: fatigue comes from gating the *wrong* things, not
from gating too little.** The cure is **accuracy + reversibility + expressive
relax + non-blocking advisories** — never loosening the structural floors.
Every fix below *reduces or holds* the approval count while keeping or
improving safety, and none of them can permanently stop an intended action
(the human can always deliberately override).

Four levers, then the per-weak-spot mapping:

- **Lever A — accurate labels.** Gate only what's genuinely sensitive; let
  benign flows pass. Mislabeling causes *both* over-gating (fatigue) and
  under-gating (unsafe), so this lever cuts fatigue and raises safety at once.
- **Lever B — reversibility → act-but-undoably.** Reversible/system-revertible
  effects (git write, draft, scratch) should *auto-execute*, not prompt — the
  human isn't blocked because the action is undoable.
- **Lever C — sealed containment, auto-selected and invisible.** Risky-but-
  reversible execution runs in a disposable sandbox *transparently*;
  containment *replaces* the approval rather than adding one.
- **Lever D — expressive, bounded relax + a WARN tier.** Encode "in this
  situation it's fine" once (inspectors/envelopes/relationship-aware), and let
  borderline cases *proceed with a loud audited advisory* instead of blocking.

### Per weak spot

1. **Default labeling oracle (off) — highest leverage, *reduces* fatigue.**
   Ship conservative `fs_/email_label_rules` **on by default** and wire the
   raise-only LLM labeler so unbound reads still get tagged. Anti-fatigue
   because it stops the binary "trust everything (unsafe) vs approve
   everything (fatigue)": accurate labels are what let "send my grocery list"
   auto-go while "send my lab results" gets gated. *(Lever A.)* Guard against
   over-labeling: keep default rules high-precision; pair with declassification
   paths so a label can be deliberately lowered.

2. **Inspector layer (inert) — the dedicated anti-fatigue layer.** Ship a
   *starter library of conservative relax inspectors on by default*: self-
   egress relax (emailing yourself never needs approval), reversible-scratch
   auto, relationship-aware relax for known family/work recipients *within the
   envelope*. This is the cleanest direct fatigue cut. *(Lever D.)* Guardrail
   already enforced: a relax is bounded by the envelope cell and can never
   cross a DENY floor.

3. **Reversibility catalog (empty) — turn approvals into undoable acts.**
   Populate the reversibility labels and add `VersionedWritePort` backends
   (Drive/git/S3) so more write surfaces are `reversible/system` →
   optimistic-auto. Each backend converts an always-prompt surface into act-
   but-undoably. *(Lever B.)*

4. **Biba (weak) — make it *targeted and loud*, not blanket.** Do **not**
   impose integrity floors on everything (that blocks normal work). Declare
   `required_floor` only on genuinely integrity-critical ops (signing, moving
   money, editing policy/config — the control plane already has FR-018), and
   add session-integrity-taint that gates *only* those critical ops after an
   untrusted read — always with a declassification path to proceed
   deliberately. Fatigue stays near zero because the gate fires rarely and
   always has a deliberate way through.

5. **P8 containment / ⑤ optional — let containment *remove* approvals.** Keep
   fork-inherit (it's the usable choice). Make the sandbox the *frictionless
   default* for execute/code work: auto-select ⑤, run transparently, and turn
   "approve this code execution" into "it ran in a disposable container,
   here's the result." *(Lever C.)*

6. **The missing WARN tier — the structural anti-fatigue fix.** Add a non-
   blocking **advisory** outcome between ALLOW and REQUIRE_APPROVAL: the action
   proceeds, but a prominent, audited "heads-up: egressing personal data /
   this is irreversible" is surfaced. Use it for the large middle band that is
   currently force-gated to approval but isn't truly dangerous. Couple it with
   **pre-flight warnings** (`capdep why --dry-run <planned action>`) so the
   human sees a floor *before* committing. This converts a class of blocking
   prompts into informed-proceed, which is precisely the fatigue cut — while
   the genuinely irreversible-and-sensitive cases stay on approval/override.

7. **Purpose-contamination (unbuilt) — surface, don't gate (yet).** Emit a
   `contamination-suspected` *audit signal* (zero friction) when inadmissible-
   category data is in-context. Visibility first; escalate to a gate only if a
   real pattern emerges. *(Lever D, observability form.)*

8. **Envelopes (3 cells) / override / ratification (empty) — give the dial and
   the human room.** Expand the default envelope grid with sensible
   `{strictest, loosest}` bounds so the cautious↔permissive dial *and* the
   inspectors have room to reduce approvals safely; ship an easy ratification
   CLI flow so operator-authored relaxes are quick to apply. These are what
   make the system *tunable toward less friction* without touching floors.

### Priority order (leverage × fatigue-reduction)

1. **Default labeling oracle on + raise-only labeler** — fixes the §1
   foundation *and* cuts false approvals. (Lever A.)
2. **Starter relax-inspector library on by default** — the most direct fatigue
   cut. (Lever D.)
3. **WARN/advisory tier + pre-flight `--dry-run` warnings** — converts the
   force-to-approval middle band into informed-proceed, and answers the
   "warn the human" gap directly. (Lever D.)
4. **Reversibility catalog + more VersionedWritePort backends** — approvals →
   undoable acts. (Lever B.)
5. **Auto-select + transparent sandbox for execute/code** — containment
   replaces approval; closes part of the P8/⑤ corner. (Lever C.)
6. **Targeted Biba floors + session integrity-taint on critical ops only** —
   closes the integrity corner without blanket friction.
7. **Contamination audit signal; expand envelopes; easy ratification.**

Noninterference (transitive) is left as a documented non-goal; the
intersection-#1 corner is closed by #5 + #6 above to the extent the threat
model needs.
