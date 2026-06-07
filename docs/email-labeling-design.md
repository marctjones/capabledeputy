# Design: Email labeling — IFC labels for incoming Gmail content (#34)

How incoming email acquires Axis-A (category) and Axis-B (provenance)
labels so the IFC engine can reason about it. This is the email analogue
of dynamic filesystem labeling (#5) and a core piece of the labeling
oracle (#42).

## Today (baseline)

`configs/google-workspace-local.yaml` labels the *entire* Gmail read
surface server-wide:

```yaml
inherent_labels:
  - untrusted.external        # Axis B: inbound mail is third-party content
  - confidential.personal     # Axis A: mailbox content is personal
```

This is correct and fail-safe — every read taints the session as
untrusted+personal, so exfiltration is blocked by construction. But it is
**coarse**: a newsletter and a bank statement get identical labels, so the
engine can't distinguish "personal" from "financial/health," and can't
relax the prompt for genuinely low-stakes mail.

## Target: three-layer labeling

### Layer 1 — provenance from the sender (Axis B)

The provenance level should reflect *who sent it*, resolved against the
RelationshipGroups registry (canonical sender identity, #51):

- unknown / external sender → `EXTERNAL_UNTRUSTED` (today's default)
- a vetted contact (family/work group) → still external, but the
  relationship is available to decision inspectors for relax decisions
- the operator's own address (self-sent) → `PRINCIPAL_DIRECT`

Provenance never drops below `EXTERNAL_UNTRUSTED` for anything that
arrived over SMTP — sender identity is spoofable, so this layer *informs*
relax decisions, it does not *clear* taint.

### Layer 2 — category from content (Axis A), raise-only

A declarative, raise-only labeler (same shape as fs labeling, #5) keyed on
message fields:

```yaml
email_label_rules:
  - match: { from_domain: ["chase.com", "fidelity.com"] }
    labels: [confidential.financial]
  - match: { subject_regex: "statement|invoice|payment" }
    labels: [confidential.financial]
  - match: { body_regex: "MRN|diagnosis|prescription" }
    labels: [confidential.health]
  - match: { list_unsubscribe: true }     # bulk/newsletter
    labels: []                            # stays personal-only, low stakes
```

Raise-only (FR-025): a rule can only escalate a message above the baseline
`confidential.personal`; it can never remove it. Composition is
`most_restrictive_inherit`, identical to the fs labeler.

### Layer 3 — the raise-only LLM labeler (optional, opt-in)

For mail the declarative rules can't classify, an opt-in **quarantined**
LLM pass (Pattern ②) emits a *category suggestion* that is applied
raise-only. The planner never sees the raw body for this — only the
quarantined extractor does — and the LLM can only *raise* the tier, never
lower it. This closes the long tail without trusting the model for safety
(a wrong "raise" is conservative; it can't under-classify because the
baseline floor already applies).

## Canonical message identity (anti-confused-deputy)

Each message's label set binds to its immutable `Message-ID` via a Gmail
`SourcePort` (#51), so "reply to the invoice email" resolves to the real
message and its labels — an injected instruction can't redirect the reply
to a different thread, and the audit records the true id.

## Implementation path

1. **Per-message labeling hook** — apply Layer 1 + Layer 2 at the Gmail
   read adapter (the MCP `inherent_labels` become a *floor*; the labeler
   adds on top per message), mirroring `make_fs_tools(labeler)`.
2. **`configs/email_label_rules.yaml`** — declarative rules, loaded like
   `fs_label_rules.yaml`, reusing the `confidential.<category>` →
   catalog-tier resolution (#50).
3. **Gmail `SourcePort`** (#51) — canonical `Message-ID` binding.
4. **Quarantined labeler** (Layer 3) — opt-in, raise-only, behind the
   existing dual-LLM extractor.

## Invariants

- **Floor preserved.** Every inbound message keeps at least
  `untrusted.external` + `confidential.personal` — labeling only raises.
- **Raise-only.** No layer can lower a message's labels; sender identity
  informs *relax of the decision* (via inspectors), never *clearing of
  taint*.
- **Fail-closed config.** Malformed rules / unknown labels refuse start.

## Relationship to other work

- Same machinery as [[fs-labeling-rfc]] (#5) — share the labeler shape.
- Sender/recipient identity needs #51 (SourcePort) + RelationshipGroups.
- Category-aware labels make the [[google-workspace-capability-mapping]]
  (#33) egress gates meaningfully tier-sensitive.
