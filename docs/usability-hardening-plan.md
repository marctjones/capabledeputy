# Usability-hardening plan — kill approval fatigue without weakening floors

Turns the §6 remediation of [security-alignment-assessment.md](security-alignment-assessment.md)
into executable slices, same vertical-slice method as
[workflow-plan.md](workflow-plan.md) (unblock → build → score → commit + tag +
review checkpoint).

**Governing invariant for every slice:** a fix may only *reduce or hold* the
approval count via **accurate labels (A) · reversibility (B) · transparent
containment (C) · expressive relax + a WARN tier (D)**. No slice may loosen a
structural floor (the four conflict invariants, capability check, BLP/Biba
gate, envelope hard-floor cell). The human is never permanently blocked — every
floor keeps its deliberate override path. Each slice states the floor it must
not cross.

Status legend: ⬜ not started · 🟡 in progress · ✅ done. Machinery legend:
**config** (machinery exists, ship/curate config) · **wire** (exists, needs
wiring) · **feature** (new code in the engine/substrate).

---

## Phase 1 — the fatigue-killers (remediation items 1–3)

The three highest-leverage cuts. Do these first; the rest compound on them.

### Slice U1 — Labeling oracle on by default — U1a ✅ · U1b ⬜
- **Goal (Lever A):** a fresh deployment auto-labels genuinely-sensitive reads
  (financial files, health email, credentials) *without* operator config, so
  the engine gates the right things and benign reads flow.
- **U1a DONE** (`configs/fs_label_rules.yaml` + `configs/email_label_rules.yaml`
  now ship ACTIVE; added a proper `credentials` restricted/fixed-high category
  to `labels.yaml` so credential rules don't under-classify to the unknown-
  default `regulated`). Adversarial test pins: sensitive paths/senders
  auto-label, benign reads stay UNLABELED (the precision/anti-fatigue
  property), raise-only never under-classifies.
  `tests/test_default_label_rules.py`.
- **Machinery:** `policy/fs_labeling.py` + `policy/email_labeling.py` loaders
  exist and are wired in `daemon/lifecycle.py`; today they load `.example`
  rules that aren't active. **config** for U1a, **feature** for U1b.
- **U1a (config):** curate a *high-precision* default ruleset (e.g. `~/.ssh/*`,
  `*/Financial/*`, `*/Medical/*`, sender-domain/subject patterns) and ship it
  active. Keep it precision-over-recall to avoid over-labeling.
- **U1b (feature, decision-gated):** the raise-only LLM labeler — a quarantined
  model that may only *raise* (tighten) a label, never lower it, so a
  compromised labeler fails safe. Needs its own design (model-in-loop latency,
  cost, the FR-012/031 "AI-read-only labels" boundary — admissible only because
  raise-only is monotone-safe).
- **Acceptance:** a read of a financial file resolves to `financial/restricted`
  with no operator config; a benign note stays unlabeled; over-labeling rate on
  a sample corpus is low.
- **Fatigue:** ↓↓ (stops the "trust-all vs approve-all" binary). **Floor it
  must not cross:** labels are raise-only; a rule/labeler can never *lower* a
  tier (only a certified declassifier can).
- **Decision D1:** ship which default rules? deterministic-only first (U1a), or
  also wire the LLM labeler (U1b)? Recommendation: U1a now, U1b as a later
  decision-gated sub-slice.

### Slice U2 — Starter relax-inspector library on by default ⬜
- **Goal (Lever D):** ship a few conservative relax inspectors *active* so the
  obvious "this is fine" cases stop prompting.
- **Machinery:** inspector layer is wired (v0.16 #46); built-ins live in
  `substrate/decision_inspectors_builtin.py` (`SelfEgressRelaxer`,
  `AfterHoursPurchaseTightener`); loader in `decision_inspector_loader.py`.
  **config** + maybe one or two new built-ins.
- **Build:** a default `decision-inspectors` block loading: self-egress relax
  (emailing yourself never needs approval), reversible-scratch auto, and a
  relationship-aware relax for known family/work recipients *within the
  envelope*. Ship in shadow mode first if desired, then promote.
- **Acceptance:** email-to-self → ALLOW (no card); scratch write → ALLOW;
  family-recipient email within envelope → relaxed; a DENY floor is never
  crossed by any of them.
- **Fatigue:** ↓↓ (direct). **Floor it must not cross:** the bounded-relax
  clamp (FR-026) already forbids a relax from crossing a floor or leaving the
  envelope cell; tighten-wins composition stands.

### Slice U3 — WARN/advisory tier + pre-flight dry-run ⬜ (the structural one)
- **Goal (Lever D):** add a **non-blocking advisory** outcome between ALLOW and
  REQUIRE_APPROVAL, plus a pre-flight warning, so the large "not truly
  dangerous but worth noting" middle band *proceeds with a loud audited
  heads-up* instead of force-gating to approval. Also answers the "warn the
  human" gap (assessment §5).
- **Machinery:** **feature.** `Decision` enum today is
  `ALLOW < REQUIRE_APPROVAL < OVERRIDE_REQUIRED < DENY` — no advisory tier. The
  `SUGGEST` rule outcome currently collapses to REQUIRE_APPROVAL.
- **Build:**
  - U3a: a new outcome (e.g. `ALLOW_WITH_ADVISORY` / `WARN`) placed *just above
    ALLOW* in the lattice — it proceeds, emits a prominent audited advisory
    carrying the rule + rationale, and never requires a human action.
  - U3b: route the discretionary middle to WARN instead of approval — i.e. the
    `SUGGEST` never-auto default and soft relax-eligible cells become WARN, NOT
    the structural floors (which stay DENY/approval/override).
  - U3c: `capdep why --dry-run <planned action>` so the human sees a floor
    *before* committing.
- **Acceptance:** a personal-data egress that today force-gates to approval (but
  is reversible / non-floor) proceeds with a WARN audit event; a
  health-meets-egress action still DENIES; nothing that is a structural floor
  becomes WARN.
- **Fatigue:** ↓↓↓ (converts a whole class of blocking prompts to informed-
  proceed). **Floor it must not cross — CRITICAL:** WARN may only apply to the
  discretionary band (never-auto default / soft rules). It must be *impossible*
  for a structural floor (any conflict invariant, BLP/Biba gate, envelope hard-
  floor) to resolve to WARN. This is the slice's load-bearing safety property
  and needs an explicit guard + adversarial test.
- **Decision D2 (needs sign-off before building):** is a non-blocking advisory
  tier the right oversight philosophy? (Some argue any non-blocking tier risks
  alert-blindness.) And exactly which cells map to WARN vs stay approval?

---

## Phase 2 — turn approvals into undoable acts (items 4–5)

### Slice U4 — Reversibility catalog + VersionedWritePort backends ⬜
- **Goal (Lever B):** reversible/system-revertible effects auto-execute (act-
  but-undoably) instead of prompting.
- **Machinery:** `policy/reversibility.py` + `policy/optimistic.py` + the
  reversibility gate in `engine.py` exist; `labels.yaml` ships an **empty**
  reversibility catalog. VersionedWritePort backends are thin. **config** +
  **feature** (backends).
- **Build:** populate reversibility labels for effect classes; add 1–2
  `VersionedWritePort` backends (git first, then Drive) so those writes are
  `reversible/system` → optimistic-auto.
- **Acceptance:** a git-backed write executes without approval and is provably
  revertible; an *unlabeled* write still defaults irreversible → gated.
- **Fatigue:** ↓↓. **Floor it must not cross:** reversibility is human-declared
  (AI-read-only); unlabeled ⇒ irreversible (fail-closed) stays; irreversible
  egress floors unchanged.

### Slice U5 — Auto-select + transparent sandbox for execute/code ⬜
- **Goal (Lever C):** containment *replaces* the approval — risky-but-reversible
  execution runs in a disposable sandbox transparently.
- **Machinery:** podman actuator (`substrate/podman_sandbox.py`) exists,
  hardened, fail-closed; `mode/dispatcher.py:select_mode` already auto-selects
  ③/⑤ for `restricted` (#52). **wire.**
- **Build:** auto-select ⑤ for EXECUTE/code effects; run transparently; return
  result; fail-closed → OVERRIDE_REQUIRED if no actuator wired.
- **Acceptance:** a code-exec action runs in a `--net=none --cap-drop=ALL`
  container with no approval card; absent an actuator it fails closed.
- **Fatigue:** ↓↓. **Floor it must not cross:** containment ≠ declassification
  — sandbox output keeps its source labels (the pre-disclosed footgun stays
  guarded; egress of sandbox output is still gated by its labels).

---

## Phase 3 — close the integrity corner + tunability (items 6–7)

### Slice U6 — Targeted Biba floors + session integrity-taint ⬜
- **Goal:** close the integrity/blast-radius corner (assessment §4
  negative-intersection #1) *without* blanket friction.
- **Machinery:** integrity-floor check + provenance lattice + FR-018 control-
  plane reflexivity exist; no systematic floor discipline, no session integrity
  taint. **feature.**
- **Build:** declare `required_floor` on genuinely integrity-critical ops only
  (signing, money movement, policy/config edits); add session integrity-taint
  that gates *only those* ops after an untrusted read — always with a
  declassify-to-proceed path.
- **Acceptance:** after reading untrusted content, a *signing* op requires a
  declassification step; an ordinary note edit is unaffected.
- **Fatigue:** ~0 (gate fires rarely, always has a path through). **Floor it
  must not cross:** the gate is targeted (named critical ops), never blanket.

### Slice U7 — Contamination signal + envelope grid + easy ratification ⬜
- **Goal:** visibility + tunability, all near-zero friction.
- **Build:** (a) emit a non-blocking `contamination-suspected` audit signal when
  inadmissible-category data is in-context (P4 observability, not a gate);
  (b) expand the default `envelopes.yaml` grid with sensible `{strictest,
  loosest}` bounds so the dial + inspectors have room to reduce approvals
  safely; (c) a quick ratification CLI flow so operator relaxes are easy to
  apply.
- **Fatigue:** ↓ (more dial headroom) + observability. **Floor it must not
  cross:** envelope hard-floor cells stay degenerate (strictest == loosest);
  the contamination signal never blocks.

---

## Sequencing & decision gates

```
Phase 1 (fatigue-killers):   U1a → U2 → U3   (U1b decision-gated)
Phase 2 (undoable acts):     U4 → U5
Phase 3 (integrity + polish):U6 → U7
```

- **U1a → U2 → U3 is the critical path.** U1a makes labels accurate (so the
  right things gate), U2 relaxes the obvious-safe cases, U3 converts the
  remaining discretionary band from blocking to advisory. Together they target
  the bulk of real-world approval fatigue.
- **Two decisions to settle before their slices build:**
  - **D2 (before U3):** adopt a non-blocking WARN tier? and which cells map to
    it? (oversight-philosophy call.)
  - **D1 (before U1b):** wire the raise-only LLM labeler, or stay deterministic?
    (model-in-the-loop trust call.)
- Every slice ends at a review checkpoint (the spec-003 phase-boundary rule),
  is committed + tagged (`v0.18.x-usability-N`), and ships an adversarial test
  proving its "floor it must not cross" holds — especially U3's "no structural
  floor can become WARN."
