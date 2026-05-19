# v0.9 — Labeling Framework (design, not yet specced)

Status: **design captured + reconciled 2026-05-19; not yet
`/speckit-specify`'d.** 002 is frozen at its US1 MVP. This file
survives until v0.9 is formally specced (constitution: architectural
decisions MUST be captured where future readers find them). The
**§Reconciliation** section at the foot is authoritative where it
refines anything above it; it folds in the decision-layer, scope, and
model-fidelity clarity from `docs/trust-model.md`,
`docs/governance-scope.md`, and `docs/security-models.md`. Read this
doc top-to-bottom, then treat Reconciliation as the override.

## Why

Today's 8-label enum conflates data sensitivity, provenance, and
action-effect (the `egress.*` "labels" are effects). To address risks
named by NIST AI RMF, ISO 23894/42001, EU AI Act, OWASP LLM/Agentic,
MITRE ATLAS, Cisco, and InfoSec data-classification (FIPS 199 / SP
800-60 / 800-122, GDPR/HIPAA/PCI, FAIR), the model must split into
orthogonal axes while staying human-legible. Robust > comprehensive.

## Locked decisions (user-confirmed this session)

1. **Open registry, stable core** — a small frozen core label set plus
   an extensible registry; not a closed enum.
2. **Layered per-user + per-use-case context profiles** — sensitivity
   is *resolved* `(data-category, user, use-case, purpose) → tier`,
   engine-side, outside LLM control. Distinct sensitive categories
   stay distinct (health ≠ financial ≠ credential); collapsing is a
   policy-rule choice, never a labeling choice. **Purpose also gates
   category *admissibility*, not only tier** (trust-model §6): some
   categories are inadmissible *inputs* to a purpose regardless of
   sensitivity (`health ⊄ inputs(employee-evaluation)`) — the
   inappropriate-context defense, enforced by purpose-scoped session
   exclusion, not model restraint.
3. **Tiered, approval-gated EXECUTE** — `EXECUTE.sandbox` allow,
   `EXECUTE.host/remote/deploy` human-approval (blast-radius weighted).
4. **Curated + admission-controlled MCP** — no open marketplace;
   vetted built-ins + external MCP behind static admission that feeds
   the label registry (realized partially by WI-1/WI-2).

## Phase 0 — Risk → Label requirements (the deliverable)

Three axes + a resolution layer; every label traces to ≥1 framework
risk (orphan labels pruned):

- **Axis A — data category** (stable core ~12, open registry):
  `health, financial, credential, identifier_pii, special_category,
  location, comms_content, legal_privileged, proprietary_work,
  personal_life, public, unclassified` — each with **separate C and I
  impact** (FIPS 199), default tier, and a resolution mode
  (fixed-high / context-up / context-resolved).
- **Axis B — provenance/integrity**: `principal-direct`,
  `system-internal`, `external-untrusted` (mechanically derived;
  monotone lattice, single sanctioned declassifier).
- **Axis C — effect class** (declared on the tool, no runtime
  classifier): `OBSERVE, FETCH, MUTATE_LOCAL, DESTROY, COMMUNICATE,
  TRANSACT, EXECUTE{.sandbox|.host|.remote|.deploy}, ADMINISTER,
  ACTUATE_PHYSICAL`.
- **Axis D — decision context** (first-class scopes, human-declared,
  AI-read-only; trust-model §2/§7): `initiator + authentication`
  (cron-you-configured / authenticated-you / unauthenticated-inbound /
  AI's-own-idea), `counterparty/relationship` (spouse / sibling /
  someone-the-principal-replied-to / colleague / unknown),
  `context-expectedness` (matches a configured job vs. anomalous),
  `reversibility/recoverability` (human-declared per resource;
  FAIR loss-weighted). These gate outcome ∈ {auto, suggest,
  require-approval, deny}; absent a matching human-authored rule the
  outcome is suggest/deny — never auto.
- **Sensitivity-resolution layer**: context profiles map
  `(category, user, use-case, purpose) → {none, sensitive, regulated,
  restricted, prohibited}`, **plus a per-profile max-tier clearance
  with read-up refusal** (dynamic-BLP target, security-models.md).

Risk register gated: OWASP LLM01/02/06/10, OWASP Agentic, MITRE ATLAS
agent techniques (exfil-via-tool, modify-agent-config), Cisco
agentic/MCP-abuse, EU AI Act tiers, NIST AI RMF Map/Measure, FIPS 199
C/I/A, GDPR/HIPAA/PCI, FAIR loss-weighting.

## Design-principle audit — 7 robustness deltas to fold into Phase 1

1. `risk-id` on every label and decision (NIST Measure rollup).
2. Threshold-crossing ALLOWs become auditable exception objects
   (ISO 23894 residual-risk acceptance).
3. Add a terminal **`prohibited`** tier no approval can unlock
   (EU AI Act).
4. Provenance is a monotone lattice with exactly one audited
   declassifier (OWASP launderable-taint).
5. Control-plane reflexivity: label/cap/profile/audit ops are
   `ADMINISTER`-class, unreachable from untrusted-tainted sessions
   (MITRE ATLAS modify-agent-config).
6. Tool-provenance is a core axis; wrapper skills inherit the union
   of wrapped effects (Cisco supply-chain; ClawHavoc-validated).
7. Loss-weighting on effect classes (reversibility/blast-radius)
   drives approve-vs-allow (FAIR) — makes approvals rarer.
   **Sharpened (trust-model §7.4):** this *replaces* the binary
   destructive-op gate with reversibility-weighted gating over
   *human-declared* recoverability metadata (AI may never assert
   recoverability); add a **social-commitment effect class**
   (third-party commitment is reputationally irreversible even when
   "just an email").

## Positioning vs the "claws"

NemoClaw (NVIDIA, Apache-2.0): sandbox + YAML access ACLs — *access
control*. DefenseClaw (Cisco, Apache-2.0): admission scan + runtime
inspect (regex + optional LLM judge) — *heuristic detection*. Both
**retrofit OpenClaw**. CapableDeputy is categorically different:
deterministic information-flow + capability lattice, LLM-isolated, the
agent itself (not a retrofit). Per Constitution VII the policy TCB is
owned/reimplemented; OpenShell-class sandbox and CodeGuard-class
admission are leveraged only behind ports (`SandboxActuator`,
`AdmissionLabeler`) — deferred substrate, never the decision plane.
DefenseClaw-style content inspection may later be a **raise-only
labeler** (may add taint, never clear it).

## Reconciliation (2026-05-19) — authoritative refinements

Folds the session's decision-layer, scope, and model-fidelity clarity
into the v0.9 scope. Where this conflicts with anything above, this
wins. Each item cites its source doc so the forthcoming
`/speckit-specify` carries the rationale.

**Decision-layer (from `trust-model.md`):**

1. **Multi-axis human-authored rule** — outcome is decided over the
   cross-product of Axis A–D (added above), not data/provenance/effect
   alone (§2). Generalizes pattern-approval rules; absent a matching
   rule ⇒ suggest/deny, never auto.
2. **Purpose gates category admissibility** (§6) — folded into Locked
   Decision 2; purpose-scoped sessions structurally exclude
   inadmissible categories (the only defense pattern ① cannot give).
3. **Hard AI-read-only invariants** (§3) — trust-graph edges,
   recoverability metadata, purpose-admissibility, and
   initiator-authentication are human-declared; the AI may read/propose
   but never mint or assert them. Violating this is the
   confused-deputy hole (Principle VIII reviewable defect).
4. **Safe learning loop** (§4) — v0.9 labels/profiles/rules are learned
   only via *AI-suggests → human-ratifies → engine applies*; never
   model-authored. The only flexibility knob is deny-vs-ask, never
   ask-vs-AI-allow.

**Model-fidelity targets v0.9 unlocks (from `security-models.md`):**

5. Context-profile **max-tier clearance + read-up refusal** → completes
   *dynamic BLP* (added to the resolution layer above).
6. **First-class flow-pattern ③ / sealed-effect** → true NI for
   `restricted` (today it falls back to intransitive ②).
7. **Integrity floor + no-read-down** on Axis B provenance → the
   under-served *Biba* half; the confidentiality tiers do **not**
   cover it (do not assume otherwise).

**Scope honesty (from `governance-scope.md`):**

8. **The labeling oracle is the load-bearing assumption.** v0.9 *is*
   the labeling story, so the spec MUST state explicitly how a label
   is assigned, what happens to mislabeled data (silent loss of all
   downstream protection), and bound this honestly — label
   provenance/assurance is in scope to *address*, not to hand-wave.
9. **Deliberate non-goals stay non-goals** — v0.9 does not add model
   accuracy/bias/eval/content-safety, privacy lawful-basis/consent/
   DSAR, or substrate security. Secure-by-reduction (Principle VII):
   cut a category/effect rather than ship it unenforceable.
10. **Preserve decision/flow explainability** — every v0.9 resolution
    (tier, admissibility, outcome) is a pure function of logged inputs;
    no model self-narration recorded as rationale.

**Next step:** `/speckit-specify` v0.9 from this reconciled doc →
clarify → plan → tasks → analyze → implement. Do not implement ahead
of tasks.md (Spec Kit discipline; Principle VIII).
