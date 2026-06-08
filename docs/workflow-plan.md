# Workflow assurance plan

The executable spec for our workflow/test-coverage work. **Not a phase
waterfall** — this work is discovery-driven (findings F1–F7 all emerged
*from building*, not planning), so the structure is:

- **A coverage matrix** (§1) — the north star and the definition of *done*.
  Fill the empty cells. Counts *mechanisms pressured*, not tests, so it
  can't be padded with low-signal green (the "1126 passed" trap).
- **A vertical-slice pipeline** (§2) — the recurring stages every workflow
  passes through. Take the highest-risk empty cell end-to-end.
- **A scorecard** (§3) — the per-workflow definition of *good* / the gate
  for keep-vs-prune.
- **Two gates** (§4) — the only genuine sequencing constraints.

Companion docs: [workflow-registry.md](workflow-registry.md) (status of what
exists), [workflow-index.md](workflow-index.md) (navigation),
[security-alignment-assessment.md](security-alignment-assessment.md) (the
model/pattern/principle frames these rows come from).

---

## 1. Coverage matrix — the north star

Each row is a guarantee that must be pressured by a **model-derived,
adversarial** workflow **on the real v2 config**. Status:
✅ pressured (model-derived) on real config · 🟡 partial (pressured but
legacy-path-only, or confirms-fires rather than tries-to-break) · ⬜ empty.

### Security models
| Guarantee | Status | On real config? | Where / gap |
|---|---|---|---|
| Reference monitor (total mediation) | 🟡 | implicit | every call gates; no explicit "unmediated path" test |
| Bell-LaPadula (read-up containment) | 🟡 | no (legacy) | egress-deny proven; clearance/tier-resolution + write-down untested |
| Biba (one-direction integrity) | ⬜ | — | no write-up / no-read-down attempt; "most under-served" |
| Brewer-Nash (conflict) | 🟡 | no (legacy) | invariants fire; no boundary/adversarial probe |
| Clark-Wilson (gated txn, sep-of-duty) | 🟡 | partial | destructive-op gate + override demo + certified-declassification txn (slice #2); dual-control e2e still demo-only |
| Object-capability (confused-deputy) | ✅ | no (legacy) | no-cap + out-of-scope (pressure suite); override pinned-destination (trust-profile B/D) + ③ redirection (slice #3) |
| IFC / sticky labels (Denning) | ✅ | no (legacy) | multi-step taint + accumulation (pressure suite) |
| Noninterference (per-tier) | ⬜ | — | Pattern ③ restricted floor (#52) never exercised e2e |

### Flow patterns
| Pattern | Status | Where / gap |
|---|---|---|
| ① turn-level | ✅ | everywhere |
| ② dual-LLM (quarantine / declassify) | ✅ | **slice #4** — e2e safe-disclosure (data-blind + label-non-propagation) was already in `test_quarantined_extractor`; added the ADVERSARIAL half: injection in the confidential content can't escalate (tool-call refused), can't add exfil fields (schema-stripped), can't bulk-smuggle (length-capped), planner never sees raw/injection. `test_pattern2_dual_llm_adversarial.py` |
| ③ reference-handle (redirection-resist) | ✅ | **slice #3** — adversarial redirect attempt: forged handle binds nothing, cross-session theft discloses nothing (end-to-end via dispatcher), value frozen at issue (no repoint path), data-blind planner. `test_pattern3_redirection_resistance.py` |
| ⑤ sealed (containment) | ⬜ | **no test** — containment ≠ declassification footgun |

### AI-safety principles
| Principle | Status | Where / gap |
|---|---|---|
| P1 least authority | 🟡 | capability scope/attenuation (confused-deputy) |
| P2 trusted/untrusted separation | ✅ | injection-content test |
| P3 confidentiality / controlled flow | ✅ | BLP/IFC pressure tests |
| P4 purpose limitation | ⬜ | **no purpose set anywhere**; purpose-contamination unbuilt |
| P5 human oversight | 🟡 | approval-as-decision; inspector relax/tighten (real config) |
| P6 accountability / traceability | ⬜ | **no audit-replay assertion** |
| P7 fail-safe defaults | ✅ | fail-closed: no-cap, malformed config, F2 default |
| P8 containment / blast-radius | ⬜ | sealed untested |

### Contingencies & the refinement layer
| Item | Status | Where / gap |
|---|---|---|
| Labeling oracle (correct labels) | ✅ | oracle tests incl. the honest GAP (F3) |
| Labeling oracle — content-scan tier | ⬜ | content-regex rules not exercised e2e |
| Substrate trust (sandbox isolation) | ⬜ | podman/sealed not exercised |
| Decision-inspector layer (Starlark) | ✅ | `policy_inspectors` on real config |
| Bounded-relax floor | ✅ | F1 guard |
| Operator policy DSL (`rules.yaml`) | ⬜ | no test asserts a RulePredicate decision |
| Envelope dial (FR-030) | ⬜ | demo only; no adversarial real-config test |
| Frequency / aggregation at scale | 🟡 | one inspector unit test |
| Certified declassification | ✅ | **RESOLVED (slice #2)** — certified declassifier lowers untrusted taint so a previously-denied read can egress; uncertified taint-removal refused (Constitution VI). Surfaced + fixed F9. `test_sanctioned_declassification_lowers_taint_enabling_egress`, `test_uncertified_taint_removal_is_refused`. |
| **Real-config legitimate egress (F2)** | ✅ | **RESOLVED (slice #1)** — irreversible egress DENIES by default, allowed via single-use human override grant; `test_v2_legitimate_egress_allowed_via_override_grant`. See F8 for the UX question. |

---

## 2. Vertical-slice pipeline — how one cell gets filled

Do ONE workflow class end-to-end (not all-unblock-then-all-build):

1. **Unblock** — wire any dormant machinery the cell needs (precedent: #46
   wired the inspector layer before it could be tested). A cell you can't
   activate is a *feature task*, not a test task.
2. **Build** — a faithful-stub, multi-step, adversarial, model-derived
   workflow **on the real v2 config**. (Faithful stub = realistic
   shapes/labels/payloads, enough to run the real security machinery.)
3. **Score** — against §3. If it doesn't clear the bar, it doesn't count.
4. **Decide real-tool** — does proving this need a real integration the
   stub can't fake? Record it for Gate B. (Most won't.)
5. **Update the matrix** — flip the cell; the next highest-risk empty cell
   is the next slice. Findings surfaced re-rank the backlog.

**Risk-ordered backlog** (the empty/partial cells, highest first):
1. ~~F2 — real-config legitimate egress~~ — **DONE (slice #1)**: production
   path works via single-use override grant; deny-by-default is by design
   (FR-019). See F8.
2. ~~Certified declassification~~ — **DONE (slice #2)**: certified
   declassifier lowers taint so a denied read can egress; uncertified removal
   refused. Surfaced + fixed F9.
3. ~~Pattern ③ redirection-resistance (adversarial)~~ — **DONE (slice #3)**:
   forged-handle / cross-session-theft / frozen-binding / data-blind all proven
   end-to-end through the dispatcher. No bug found — confirms the guarantee
   holds adversarially.
4. **Pattern ② dual-LLM declassify e2e.** ← *next slice.* Confidential read →
   quarantined extract → schema-declassified summary egresses; planner never
   sees raw.
4. **Pattern ② dual-LLM declassify e2e.** Confidential read → quarantined
   extract → schema-declassified summary egresses; planner never sees raw.
5. **Operator DSL + envelope dial on real config.** RulePredicate decisions,
   bounded-relax envelope cell.
6. **Biba write-up + Noninterference (Pattern ③ per-tier e2e).**
7. **Pattern ⑤ sealed containment** (needs substrate — feature + test).
8. **P4 purpose-contamination, P6 audit-replay, P8 containment.**
9. **Frequency/aggregation at scale.**

---

## 3. Scorecard — per-workflow definition of *good*

A workflow *counts* only if it clears these. Used for keep / refactor /
**delete** (yes — pruning is required; the 1126 collapse to one property
test).

1. **Untested mechanism** — pressures a guarantee not already covered.
2. **Model-derived** — expectation from the security model, not probed from
   the engine (non-circular).
3. **Adversarial** — tries to *break* the guarantee, not just confirm it
   fires.
4. **Regression/design-gap catcher** — a realistic change would turn it red.
5. **Real config** — runs on `build_policy_context_from_configs`, not the
   legacy `policy_context=None` path.
6. **Stub↔real fidelity** — would still pass with the real tool (or a
   contract test pins the stub to the real tool's observable shape).

---

## 4. The two gates (the only real sequencing)

- **Gate A — Assurance baseline.** F2 resolved + every matrix cell ✅ on the
  real config → then one hard evaluate-and-prune pass (collapse the
  low-signal mass, publish the filled matrix as the coverage report).
- **Gate B — Real-tool justification.** Before wiring *any* real
  integration: a demonstrated **assurance-need** (a property the stub can't
  fake — real OAuth for vault echo-resistance, real subprocess for sealed,
  real file content for content-scan) **or product-need**, plus a stub↔real
  contract test, plus the safety rule: real-tool tests opt-in, read-only by
  default, **never send/buy in CI** (precedent: the email-disable work).

Everything else is iteration between the gates.

---

## 5. Findings log (the evidence that justifies the rows)

- **F1** bounded-relax floor bug (FIXED) → guards the inspector-layer rows.
- **F2** v2 denies irreversible egress by default → the #1 backlog item;
  the reason "real config?" is a matrix column.
- **F3** labeling-oracle gap (standing test) → the labeling-oracle row.
- **F4** 25 demos silently broken, never in CI → why "real config + CI" is
  a scorecard criterion.
- **F5** outbound email was enabled (FIXED) → the safety rule in Gate B.
- **F6** provenance masking → why category guarantees need a non-egress or
  declassification path to isolate.
- **F7** catalogue circularity → why "model-derived" is criterion #2 and the
  1126 are slated for prune.
- **F9 — declassifier only lowered `inherent_tags`, not `additional_tags`
  (FIXED).** The certified declassifier removed taint from a tool's *inherent*
  label but left the same tag on the *propagated* `additional_tags`, so a
  declassified read still tainted the session EXTERNAL_UNTRUSTED and egress was
  still denied — the trust hinge was silently inert. Fix: apply `_remove(...)`
  to `additional_tags` and to the propagation set in `tools/client.py`.
  Probe before: `provenance after read={'external-untrusted'}`; after: `set()`.
  This is *why* slice #2 was the highest residual risk — the cell wasn't just
  untested, the machinery underneath it didn't work.
- **F8 — irreversible egress override-vs-approval (RESOLVED by design
  change).** Operator decision: most irreversible **communication** egress
  (email) routes to human **APPROVAL** by default; operator-configured
  super-sensitive data escalates to **OVERRIDE_REQUIRED**; **purchases**
  keep the stricter DENY→override. Implemented as an FR-019 amendment +
  `egress_escalation.yaml`. Structural floors (BLP/Biba/conflict invariants)
  still DENY health/financial/untrusted egress regardless — verified. Note:
  this exposed two demos (clinical, override) that were *accidentally*
  relying on the old reversibility-DENY rather than the rule they claimed —
  both fixed to use their real mechanism.

---

## Slice log

- **#1 — F2 / real-config egress — DONE.** Proved irreversible egress was
  override-gated by default; surfaced F8.
- **#1b — FR-019 amendment (F8 resolution) — DONE.** Communication egress →
  APPROVAL by default, OVERRIDE for operator-configured super-sensitive,
  purchases unchanged. `policy/egress_escalation.py` + config; engine +
  PolicyContext + loader wired; sensitive-egress floors verified intact.
  Tests: approval default, purchase-still-deny, super-sensitive→override,
  override-resolves-super-sensitive (single-use). Matrix cell ✅.

- **#2 — Certified declassification — DONE.** Real-config workflow: a raw
  external read taints the session → egress DENIED (`untrusted-meets-egress`);
  routed through a certified `SchemaProjector` declassifier the taint is
  lowered → egress is no longer untrusted-blocked (drops to the ordinary
  approval default). Adversarial half: an uncertified `TagTransfer` that tries
  to remove taint is refused (`LabelError`, Constitution VI), while a certified
  transfer lowers it. **Surfaced + fixed F9** — the declassifier was only
  lowering `inherent_tags`, leaving the propagated `additional_tags` tainted,
  so the hinge was silently inert. Matrix cell ✅; Clark-Wilson 🟡→partial.

- **Trust-profile arc (FR-049, A–D) — DONE, shipped in v0.17.0.** Not a single
  matrix cell but a cross-cutting capability: `managed`|`personal` profile;
  operator-root solo override; structural floors override-targetable; standing
  rules cross own-data floors; grouped override. Hardened the untrusted-egress
  ceiling and proved **override pinned-destination redirection-resistance**
  (B/D) — which fed the object-capability row. The hard line: untrusted content
  can at most raise an override request, never auto-trigger/redirect.

- **#3 — Pattern ③ reference-handle redirection-resistance — DONE.** The
  adversarial half the demo lacked, end-to-end through the dispatcher: a forged
  handle binds nothing (only the opaque token flows), a cross-session-stolen
  handle discloses nothing, the handle→value binding is frozen at issue (no
  planner-reachable repoint), and the planner only ever holds an opaque UUID.
  No bug found — confirms the guarantee holds adversarially.
  `test_pattern3_redirection_resistance.py`. Matrix cell ✅.

- **#4 — Pattern ② dual-LLM declassify — DONE.** The e2e safe-disclosure path
  (data-blind planner + label-non-propagation) was already covered by
  `test_quarantined_extractor`; this slice added the **adversarial** half — a
  prompt injection embedded in the confidential CONTENT cannot weaponize the
  quarantined extractor: (A) a tool-call emit is refused (no tools → no
  escalation), (B) injected extra/exfil fields are stripped by schema
  validation, (C) bulk smuggling into a string field hits the schema length cap
  and is rejected, (D) end-to-end the planner never sees the raw payload or the
  injection instruction and the extracted summary carries no taint (egress-safe).
  No bug found. `test_pattern2_dual_llm_adversarial.py`. Matrix cell ✅.

## Immediate next: slice #5 = operator DSL + envelope dial on real config

Pressure the operator's `rules.yaml` RulePredicate decisions and the
bounded-relax envelope cell on the REAL v2 config (the refinement layer the
1126 never touch): a human-authored AUTO rule fires for the exact cell it
declares and NOT a neighbor; the dial selects a point within the envelope and
never crosses `strictest`. Adversarial: a greedy rule/dial can't escape the
cell's `{strictest, loosest}` bounds.
