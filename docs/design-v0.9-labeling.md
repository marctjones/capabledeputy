# v0.9 — Labeling Framework (design, not yet specced)

Status: **design captured, not the active Spec Kit feature.** The
active feature is `specs/002-capability-delegation-chains`. This file
exists so the analysis below survives until v0.9 is formally
`/speckit-specify`'d (constitution: architectural decisions MUST be
captured where future readers find them, not only in conversation).

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
   policy-rule choice, never a labeling choice.
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
- **Sensitivity-resolution layer**: context profiles map
  `(category, user, use-case, purpose) → {none, sensitive, regulated,
  restricted, prohibited}`.

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
