# Design: Unified Policy & Configuration Authoring

**Status:** proposal · **Date:** 2026-07-17 · **Scope:** how operators configure
CapableDeputy, and how the code behind it stays maintainable.

## 1. Purpose

CapableDeputy's configuration surface has grown to **18 operator-edited files**
(16 YAML + 2 JSON), **11 separate `load_*` functions** in `policy/` alone, and
**~26 distinct `*Error` types** — one bespoke loader and error per file. Worse
than the file count, there are **~6 different rule grammars** a human must learn
(`rules.yaml`, `email_label_rules.yaml`, `fs_label_rules.yaml`, `envelopes.yaml`,
`purposes.yaml`, `override_policy.yaml`, and now `requirements.yaml`). A routine
change ("stop the agent emailing my bank statements") requires knowing *which*
file, *which* grammar, and *which* of five overlapping "how strict" concepts to
touch, and in what precedence.

This document proposes a target design optimized for two audiences at once:

- **The end user** editing policy: one intent should equal one obvious edit in
  one obvious place; a non-programmer should be able to read the whole policy.
- **The maintainer** of the code: one loader, one schema, one error path, one
  place precedence is defined, and a test harness that proves the semantics hold
  regardless of authoring surface.

It also answers a specific question head-on: **should we push everything into
Starlark so the user learns only one language?** (Short answer: no — see §6.)

## 2. Goals / non-goals

**Goals**
- One *regular* rule grammar shared across labeling, decisions, and requirements.
- One documented precedence lattice; most-restrictive-wins, deterministic.
- Routine changes are a single edit — usually a single line (the posture).
- Config is analyzable: statically validated, queryable, exhaustively fuzzable.
- Backed by defaults so a fresh install runs without hand-authoring 11 files.

**Non-goals**
- Rewriting the engine. The engine keeps its typed internal representations
  (`LabelState`, `DecisionRules`, `EnvelopeSet`, …); this is about the *authoring
  surface* that compiles down to them.
- Changing the security model. This is presentation + ergonomics, not new
  authority. Every floor the engine enforces today still holds.

## 3. Principles (why this shape, not another)

1. **Constitution Principle I — deterministic, LLM-isolated enforcement.** Policy
   must be a pure function of explicit inputs. This is the single strongest
   argument for *declarative data over code*: data can be analyzed, replayed, and
   proven; arbitrary logic can only be executed. (Directly informs §6.)
2. **Constitution Principle VI — fail-closed.** One loader, one error type, one
   fail-closed rule — not 26 copies that already drift (e.g. `load_postures`
   raises `AttributeError` instead of `PostureError` on a non-mapping top level).
3. **Regularity beats power.** A human can hold a small, uniform model in their
   head. Six specialized grammars are individually expressive and collectively
   unlearnable.
4. **Author uniformly; specialize internally.** Humans write one shape; the
   loader compiles to whatever the engine needs. Uniformity where humans read;
   specialization where the machine runs.

## 4. The core model: four concepts, one sentence

Everything a human authors is the **same sentence**:

```
when <this data / this action matches>  →  <outcome>
```

The entire policy reduces to four concepts, each a set of those sentences (or a
named bundle of them):

| Concept       | Question it answers                    | Who edits it            |
|---------------|----------------------------------------|-------------------------|
| **Labels**    | what is this data / where's it from?    | occasionally            |
| **Rules**     | what may happen to labeled data?        | occasionally            |
| **Posture**   | a named bundle of defaults + active rules | **routinely — usually the only edit** |
| **Purpose**   | per-task tightening                     | rarely                  |

Everything in today's tree folds into one of these:

- `email_label_rules.yaml`, `fs_label_rules.yaml`, `labels.yaml` → **Labels**
  (`when source is inbox → label untrusted, category:personal`).
- `rules.yaml`, `envelopes.yaml`, `approval-patterns.yaml`,
  `egress_escalation.yaml`, `override_policy.yaml` → **Rules**. An envelope is
  just a rule whose outcome is a *range* the dial picks within; an approval
  pattern is a rule with outcome `approve`; an override trigger is a rule with
  outcome `override-required`.
- `postures.yaml`, `profiles.yaml`, `risk_preference.json`,
  `decision-inspectors.yaml`, `requirements.yaml` → **Posture** (a named bundle:
  which dial, which rules/inspectors are active, which requirements are demanded).
- `purposes.yaml` → **Purpose**.

Requirements (from #307) are the same sentence with a `must` modality — the
engine *proves* them against the compiled policy instead of *applying* them:
`financial data must never egress externally`.

### Worked routine changes (each = one edit)

- *"Loosen up, I trust myself"* → `posture: low-friction` — one line. ~90% of
  users only ever touch this.
- *"Never email financial data outside my domain"* → one `when financial +
  send_email + external → deny` rule.
- *"My work address counts as internal"* → one binding in **Labels**.

## 5. One precedence lattice

The other half of "easy to understand" is a **single, fixed way rules combine**,
so a human never wonders which knob wins:

```
structural floors            (non-negotiable; §Principle VI)
  > requirements (operator MUSTs, proven at load)
    > authored rules
      > posture defaults
        > purpose tightening
— and MOST-RESTRICTIVE ALWAYS WINS.
```

This is a deterministic total order over outcomes (the engine already ranks
`DENY < OVERRIDE_REQUIRED < REQUIRE_APPROVAL < WARN < ALLOW`). It is defined in
**exactly one place** and is what the #306 conformance harness fuzzes. Note this
subsumes the still-open "posture-vs-purpose risk-preference precedence" that #307
was scoped to own — it becomes one line in the lattice, not a new mechanism.

## 6. The language question: declarative-first, Starlark as the escape hatch

> *Should the user learn only one language, and should that be Starlark?*

The instinct — "one language" — is right. The conclusion — "make it Starlark" —
is wrong. The one language the user learns should be the **declarative rule
grammar** of §4, not a scripting language. Reasoning:

**Why not Starlark-for-everything**

1. **Most policy is *facts*, not *logic*.** `financial + send_email → deny` is a
   fact. Expressing facts as code is a known anti-pattern (it's why Kubernetes,
   Terraform, and Envoy are declarative). A table of label rules written as
   Starlark functions is *harder* to read, diff, and review than the same table
   as data.
2. **Analyzability is the product.** Declarative rules can be statically
   validated, exhaustively fuzzed (this is literally #306), reordered by
   precedence deterministically, and *queried* ("show every rule that can DENY
   health data"). Code can only be executed. For an engine whose entire value is
   Principle I (deterministic, provable), turning core policy into arbitrary code
   destroys the property that makes it trustworthy.
3. **The user is often not a programmer.** `posture: strict` and a `when →
   outcome` table are readable by a non-coder; a Starlark `def` with control flow
   is not. "One language" only helps if the language is *easy*.
4. **Code in the trust boundary is a liability even sandboxed.** You must bound
   CPU/stack, forbid nondeterminism, and answer "what does a Starlark exception
   mean at decision time?" Data cannot have that class of bug.

**Where Starlark genuinely belongs**

Starlark already has a home here — `substrate/policy_script_host.py`, used for
**decision inspectors**. That is exactly right, because an inspector is
*computational*: it inspects a proposed decision and computes a relax/tighten,
behind a floor guard that can never cross a structural DENY. That is the 5% of
policy that is real logic, not a fact.

**The tiered answer (one coherent story, three levels of power)**

| Level | Surface | For | Analyzable? |
|-------|---------|-----|-------------|
| 1. **Fixed vocabulary** | `when <label+action> → <deny\|approve\|allow>` | the 95%; non-programmers | fully (fuzzable, queryable) |
| 2. **Bounded expressions** | a CEL-style *expression* language in the `when` clause (`when data.tier >= restricted and dest not in allowlist`) | power users needing richer matching | mostly (pure, total, side-effect-free) |
| 3. **Starlark escape hatch** | inspectors in `policy_script_host` | genuine logic, behind the floor guard | no — only executed, floor-guarded |

The user learns **one thing** — the `when → outcome` sentence. A power user can
reach for richer *conditions* (level 2) without learning a scripting language.
Only the rare operator writing genuinely computational policy touches Starlark
(level 3), and even then it can never weaken a floor (proven by #306's inspector
surface). This is the Kubernetes model: YAML for the 95%, CEL for richer
predicates, admission webhooks/operators for the computational 5% — nobody writes
their whole cluster config in Go.

**Recommendation:** declarative rule grammar as the one language; a bounded
CEL-style expression sub-language for the `when` clause; Starlark reserved for
the inspector escape hatch it already occupies. Do **not** migrate labeling,
decision rules, envelopes, or requirements into Starlark.

## 7. Config layout, defaults, and format

- **~4 files, not 18** — `labels.yaml`, `rules.yaml`, `posture.yaml`,
  `purposes.yaml` — plus optional escape-hatch files for inspectors. Or a single
  `capdep.yaml` with those as top-level sections; the section is the unit, not the
  file.
- **One format.** Drop the two JSON files (`risk_preference.json`,
  `risk_register.json`) into YAML. Mixed formats for hand-edited policy is pure
  friction.
- **Layered defaults.** Ship sane built-in defaults (the three shipped postures
  already do this). A fresh install runs from defaults; the operator overrides
  only deltas — instead of today's "all 11 files must be present or the daemon
  refuses to start" (`V09ConfigError`).
- **Selection, knobs, and demands live together.** What you *select*
  (`posture: strict`), the raw *knobs* (rules/labels), and what you *demand*
  (`requirements:`) sit in one place, so the requirement gate provably runs
  against the selected posture (already wired at daemon start by #307).

## 8. Command surface — don't hand-edit YAML for routine changes

Hand-editing YAML becomes the *advanced* path, not the default:

```
capdep posture use strict
capdep rule add "financial + send_email + external → deny"
capdep label bind me@work.com internal
capdep policy check        # load everything, validate every cross-reference,
                           # run the requirement gate, report ALL problems at once
capdep policy explain send_email --to bob@acme.com --labels financial
                           # "why would this be denied?" — renders the winning rule
```

`capdep policy check` is the single validation entry point (#307's
`verify_requirements` is one slice of it). `capdep policy explain` is the payoff
of declarative-first: because rules are data, the engine can *tell the human why*
a decision goes the way it does — impossible if policy were opaque code.

## 9. Maintainability architecture

The authoring uniformity maps to a clean internal pipeline:

```
one YAML surface  ──► one schema-driven loader ──► compile ──► typed engine structures
  (§4 grammar)         (one ConfigError,             (per       (LabelState, DecisionRules,
                        one fail-closed rule)         concept)    EnvelopeSet, Posture, …)
```

- **One loader, one error.** Replace 11 `load_*` + ~26 `*Error` with a single
  schema-driven `load_config()` → one `ConfigError`. Every section inherits
  identical missing/unparseable/invalid handling; the drift bugs disappear.
- **Compile, don't re-represent.** The loader is a *compiler* from the uniform
  surface to the engine's existing typed structures. The engine is untouched, so
  this is additive and incrementally adoptable.
- **The harness is the safety net.** The #306 floor-invariance harness fuzzes the
  *compiled* decision surface, so it proves the semantics hold no matter what the
  authoring layer looks like. That is what makes it safe to change the authoring
  surface without fear: adopt the new grammar behind the same green harness.
- **Cross-reference validation in one pass.** `capdep policy check` validates that
  a posture's `inspector_set` names real inspectors, a rule's `crosses_floor` is a
  real floor, a requirement's `category` is a real label — today these checks are
  scattered and partial.

## 10. Migration path (incremental, not a rewrite)

1. Define the `when → outcome` schema + the single `load_config()` compiler; have
   it emit today's typed structures. Keep old loaders as thin adapters.
2. Land the **precedence lattice** as one function (closes the open #307
   precedence gap) with the #306 harness guarding it.
3. Add `capdep policy check` / `explain` over the compiled model.
4. Fold concepts in one at a time (envelopes → range-outcome rules; label-rule
   files → Labels; requirements already compile-checkable), deleting each old
   loader as its concept moves. The harness stays green throughout.
5. Convert the two JSON files to YAML; introduce the defaults layer last.

Each step is shippable and reversible; none requires touching `decide()`.

## 11. Risks & open questions

- **Expressiveness vs regularity.** A single grammar is less precise than
  specialized formats (an envelope range genuinely differs from a point outcome).
  Mitigation: §4's "author uniformly, specialize internally" — the grammar has a
  *range* outcome; the engine still gets an `EnvelopeSet`.
- **CEL-style level 2 is real scope.** A bounded expression language is a
  building project of its own; it may be deferred until level-1 demand proves it's
  needed. Level 1 + level 3 (existing Starlark) is a complete story on day one.
- **Backwards compatibility of existing operator files.** The adapter approach
  (step 1) keeps old files working during migration; a hard cutover would not.
- **Who owns "defaults"?** The shipped postures are the natural defaults source;
  the defaults layer must itself pass `capdep policy check` in CI.

## 12. Summary

- The lever is **one regular rule grammar** (`when → outcome`) across labeling,
  decisions, and requirements — not fewer files.
- **One precedence lattice**, most-restrictive-wins, defined once.
- **~4 concepts, defaults-backed**, routine change = one line (usually the
  posture); a `capdep policy` CLI so YAML editing is the advanced path.
- **Declarative-first, not Starlark-first.** One language for the user — the rule
  grammar — with a bounded expression sub-language for power users and Starlark
  kept to the computational inspector escape hatch it already occupies.
- **Maintainability** comes from one loader / one error / compile-to-engine, with
  the #306 harness proving the semantics survive the change.
