# Practical Daily-Use Agent Design

**Status:** active delivery design, refreshed 2026-10-03. This document
supersedes optimistic historical descriptions of a "Google connection" as an
account-readiness claim. A configured credential is not a completed workflow.

## Product promise

CapableDeputy is a local, daily executive assistant whose model can propose
and summarize but never authorize an effect. The daemon is the trusted
reference monitor: every tool call, label transition, capability use,
declassification, approval, standing grant, policy decision, and audit record
passes through daemon-owned code.

The practical default is local MLX inference with the 8B daily profile. All
personal content stays local unless a deterministic policy allows a specific
egress. Draft-first is the usability default; it is not a substitute for an
authorization decision.

## Daily workflows and honest readiness

| Workflow | Current state | Required completion evidence | Authority for external effects |
|---|---|---|---|
| Inbox triage | Gmail IMAP/SMTP connector can be configured locally; daemon currently registers IMAP read tools. | Live, read-only inbox-header and message-read acceptance with source labels, provenance, cancellation, and reconnect coverage. | Reading follows source/flow policy; archive/mark-read need their declared mutation decision; send is approval-gated. |
| Calendar planning | Native calendar is an in-memory deterministic demo, not a real Google calendar. | Select and connect a real provider; prove read, proposed mutation, cancel, and recovery flows. | Create/modify/delete/respond require approval unless an exact bounded standing authorization matches. |
| Drive/document work | No Google Drive connector is registered. | Least-privilege Drive OAuth/provider, resource labels, reference-handle routing, and read/write acceptance tests. | Drive mutations and sharing are effect-gated; sharing/egress defaults to approval. |
| Research briefing | Local files and web read paths are available. | Real workflow evidence for citations, taint propagation, safe drafting, and egress refusal. | Read-only by default; external fetch/egress is destination- and label-gated. |
| Email drafting and sending | Drafting is the normal path; send-capable tools exist but are policy-controlled. | Exact rendered approval payload, recipient binding, cancellation, and audited post-approval redispatch. | Human approval by default; only a bounded, revocable standing grant or ratified rule may automate an exact scope. |
| GitHub work | Curated integration exists but account readiness is not yet proven. | Least-privilege PAT or OAuth setup, visible scope/readiness, and read/mutation acceptance tests. | Repository mutations, comments, PR actions, and token-scoped effects are individually gated. |

## Mandatory boundaries

1. `decide()` remains the deterministic, always-invoked chokepoint. Model
   confidence, prompt text, and model-selected tools are never authority.
2. The five flow patterns remain available: tainted context, quarantined
   extraction, reference handles, programmatic processing, and sealed
   execution. The deterministic mode selector chooses the minimum permitted
   exposure; restricted data requires handles or sealed execution and otherwise
   fails closed.
3. Sandboxing is containment and reversibility, not declassification. Sandbox
   outputs retain labels until a certified declassifier or one-shot human path
   applies.
4. Every real external or irreversible effect has exactly one authorization
   path: focused human approval, a bounded/revocable capability or standing
   grant, or a human-reviewed deterministic policy rule. Hard denials are not
   bypassed by ordinary approval.
5. Starlark is an optional, sandboxed decision-inspector escape hatch for
   operator-authored computational predicates. It has no I/O or ambient
   authority and its result is clamped by the engine's hard floors, capability
   bounds, and policy envelope; it does not write audit records or grant
   authority itself.

## Ownership split

The daemon owns credentials, source labels, flow-mode selection, capabilities,
policy and Starlark loading, approvals, standing grants, exact effect dispatch,
audit/provenance, recovery, and connector health. GUI, CLI, and TUI are
untrusted clients: they render authoritative daemon state, collect focused
human approval, and relay requests. They must not decide or execute effects
locally.

## Delivery sequence and exit criteria

1. **Reliable daily session:** complete #319 with live daemon-restart evidence
   for CLI, TUI, and CapDepMac.
2. **Usable authorization:** complete #331--#334 so every client can reliably
   surface the daemon's approval and override state without becoming an
   authority path.
3. **Real account workflows:** complete #325 and #328. Gmail, a selected
   calendar/Drive provider, and GitHub must each have a least-privilege
   readiness check and explicit effect map.
4. **Local-model promotion:** complete #311/#338 with latency, peak-memory,
   task-success, valid-tool/schema-output, and fallback measurements. The
   local 8B profile is an operational baseline, not an evidence-based winner.
5. **Acceptance:** complete #346 with allowed, denied, approval, standing
   authorization, taint/egress denial, cancellation, and daemon-recovery cases
   for every workflow, then collect seven days of dogfood evidence.

Use `scripts/test-capdep.sh core` for deterministic checks and
`scripts/test-capdep.sh live` for an intentional host-owned daemon and GUI
parity smoke. Live tests may use local credentials for read-only readiness but
must never perform unattended account mutations.
