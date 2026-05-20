---
description: "Spec 004 design note — DefenseClaw integration: complementary, competing, or composable?"
---

# DefenseClaw + CapableDeputy: Integration Assessment

## TL;DR

DefenseClaw and CapableDeputy are **substantially complementary** but
**target the same buyer**. They can be composed in ways that strictly
improve DefenseClaw's security claims; the architectural question is
whether to ship the composition as a CD-side plugin or push to be CD-as-
the-policy-backend inside DefenseClaw.

**Recommendation**: ship a thin DefenseClaw plugin that replaces
DefenseClaw's runtime-guardrail decision path with CD's deterministic
four-axis engine. The plugin's audit trail flows out via DefenseClaw's
existing OTLP/Splunk pipeline. We get:
- Distribution leverage (DefenseClaw users acquire CD).
- Substrate leverage (DefenseClaw's sandbox + scanner stack are real today;
  CD's are mostly port-only).
- Decision-model differentiation (CD's deterministic engine vs DefenseClaw's
  "regex + optional LLM judge").

The cost: a 1-file CD plugin + a thin Go adapter on DefenseClaw's gateway.

## Where they overlap

| Capability | DefenseClaw | CapableDeputy |
|---|---|---|
| Admission scanning (skills/MCP/code) | CodeGuard scanner; secrets, dangerous exec, unsafe deserialization, weak crypto, injection patterns, risky file access | Risk register + label citations (FR-015); structural T012 declarations refuse malformed tools |
| Runtime guardrails | Regex + policy + optional LLM judge | Deterministic four-axis decision engine (Principle I — no LLM in path) |
| Sandbox controls | OS-level isolation (Landlock/seccomp/netns) | Port-only stub; spec 004 ships Podman/Modal/Firecracker |
| Audit & observability | SQLite/JSONL/OTLP/Splunk/webhooks | Replay-deterministic event taxonomy (SC-002) |
| Policy language | YAML + Rego | 11 YAML/JSON config files: labels, profiles, rules, bindings, envelopes, purposes, overrides, expectations, relationship groups, risk register, risk preference |
| Identity model | Per-agent identity mapped to a human sponsor | Capability model with delegation chains; principal-driven via Axis-D |

## Where they genuinely differ

These are the load-bearing differences that motivate composition rather than
replacement.

### 1. Decision model — deterministic vs probabilistic

DefenseClaw's runtime guardrails use **regex + optional LLM judge**. This
is operationally pragmatic but violates Principle I (no LLM in the decision
path) and gives up SC-002 (replay determinism). CD's engine is a pure
function over its inputs; two calls with identical inputs are guaranteed
byte-identical outcomes.

**Composition value**: replace DefenseClaw's runtime-guardrail decision
with `engine.decide()`. DefenseClaw's scanner + audit + sandbox stack is
unchanged.

### 2. Authority model — declarative vs capability-based

DefenseClaw: scanners flag findings; policies block. The agent has whatever
permissions the host gives it; DefenseClaw says "no" to bad uses.

CD: the agent has **no authority** at session start. Every capability is
operator-granted, narrowable, delegatable, revocable. The composition with
Brewer-Nash conflict rules + four-axis labels means *what* the agent can
even propose to do is structurally bounded.

**Composition value**: DefenseClaw's gateway becomes the dispatcher,
forwarding every call to CD's engine for the actual authority decision.
DefenseClaw's identity-mapped audit still works (the sponsor field maps
to the CD principal).

### 3. Information flow — none vs four-axis

DefenseClaw has no information-flow labels. CD's four-axis
(category × provenance × effect × decision context) is monotone-composed
across operations. A `health`-tagged read taints subsequent decisions even
without re-declaration.

**Composition value**: CD's labels become DefenseClaw's "what changed
about this agent's state" signal. DefenseClaw can fire a `policy.escalate`
event when Axis-B taint crosses an operator-defined threshold.

### 4. Override workflow — block/allow vs typed FSM

DefenseClaw: "observe mode" (log) vs "action mode" (block). Resolving a
block requires editing the policy.

CD: override-distinct-from-approval (FR-038) with `single-authorized` /
`dual-control` / `disallowed` FSM, friction confirmation, distinct
capability origin, persistent storage, dedicated CLI surface.

**Composition value**: DefenseClaw block events that operators want to
selectively allow flow to CD's override path instead of demanding a policy
edit. Each override is audit-loud and time-bounded.

### 5. Data-blind planning — none vs Pattern (3)

DefenseClaw inspects content but doesn't change what the planner sees.
CD's Pattern (3) ReferenceHandle keeps raw labeled values out of the
planner entirely.

**Composition value**: DefenseClaw's plugin into OpenClaw can route
labeled values through CD's handle store before they reach OpenClaw's
brain layer. The planner gets UUIDs; the substrate binds at the boundary.

## Integration patterns — three options

### Option A — DefenseClaw plugin that calls CD as policy backend

A thin Go module that DefenseClaw's gateway loads as a policy backend.
DefenseClaw's existing admission scanners, sandbox controls, and audit
sinks are unchanged; only the **decision** is delegated to CD over a
loopback HTTP(S) interface.

**Pros**:
- DefenseClaw users opt in without ripping out their existing pipeline.
- CD's deterministic engine becomes a selectable upgrade.
- Distribution leverage: DefenseClaw's marketing reach.

**Cons**:
- DefenseClaw's policy language (YAML + Rego) and CD's policy language
  (11 config files) coexist; operators must understand both.
- Network hop for every decision (CD runs as a sidecar).

**Verdict**: **Recommended**. Ship as `src/capabledeputy/integrations/
defenseclaw_plugin/` per task **U058**.

### Option B — CD absorbs DefenseClaw's scanner stack

CD adds an admission-scanning phase that runs CodeGuard-equivalents
(secrets, dangerous exec, etc.) at MCP-tool registration time.

**Pros**:
- One operator-facing surface.
- Scanner verdicts feed CD's risk_register as additional axis-A risk_ids.

**Cons**:
- Duplicates Cisco's investment in CodeGuard rather than composing with it.
- We don't have Cisco's scanner-rules corpus; reimplementing is months of
  work.

**Verdict**: not recommended as primary path. **A subset is worth doing**:
adapt DefenseClaw's CodeGuard rules as additional fixture entries for our
existing risk register, so a CD-only deployment gets a starter scanner
without us having to maintain it.

### Option C — DefenseClaw absorbs CD's engine

The inverse: Cisco picks up CD and embeds the deterministic engine in
DefenseClaw's runtime-guardrail step.

**Pros**:
- Maximum distribution of CD's IP.
- Enterprise backing (Cisco's brand).

**Cons**:
- Requires Cisco partnership / licensing arrangement.
- Loses CD's standalone identity.
- Sets up Cisco as upstream for everyone we'd want to sell to.

**Verdict**: a longer-term conversation. Not blocked by Option A.

## Recommended architecture

```
                                    ┌───────────────────────┐
                                    │   DefenseClaw         │
   ┌──────────┐         ┌──────────┐│   Gateway (Go)        │
   │ Agent    │ ──MCP──▶│ OpenClaw │├───────────────────────┤
   │ (OpenClaw│         │ TS plug- ││ Admission scanners    │
   │  brain)  │ ◀───────│ in       ││ (CodeGuard)           │
   └──────────┘         └──────────┘│                       │
                                    │ Runtime guardrails    │
                                    │ ┌───────────────────┐ │
                                    │ │ Policy backend    │ │──RPC──▶ CapableDeputy
                                    │ │  (decision)       │ │         engine.decide()
                                    │ └───────────────────┘ │         (four-axis)
                                    │                       │
                                    │ Sandbox controls      │
                                    │ (Landlock/seccomp)    │
                                    │                       │
                                    │ Audit (OTLP/Splunk)   │ ◀── CD audit events
                                    └───────────────────────┘     also flow here
```

DefenseClaw owns: scanners, sandbox, audit pipeline, operator UX, identity
mapping, observability.

CD owns: the deterministic decision, the four-axis labels, the capability
model, the override FSM, replay determinism.

**Both operators benefit**: DefenseClaw users get a principled engine;
CD users gain access to Cisco's scanner corpus + production-grade sandbox.

## Risks of NOT integrating

If we don't integrate:
- DefenseClaw consolidates the enterprise "claw" security market.
- CD's distinctive IP (four-axis IFC, capability narrowing, override FSM,
  Pattern 3) sits in a separate codebase that enterprises don't deploy.
- We compete head-to-head against Cisco's marketing engine for the same
  buyer.

## Open questions

1. **Does DefenseClaw's policy language interpret CD's four-axis labels?**
   The plugin would expose CD's decision outputs as DefenseClaw-policy-
   visible attributes; DefenseClaw operators can still write Rego against
   them.
2. **How are CD audit events deduplicated against DefenseClaw's?**
   Recommendation: CD events flow into DefenseClaw's audit pipeline as
   structured fields under a `cd.*` namespace. DefenseClaw is the
   canonical sink; CD's JSONL store is a local backup.
3. **What happens to CD's standalone CLI when running under DefenseClaw?**
   `capdep override` etc. remain operational against the daemon's own
   store. Either deployment shape works; the plugin is opt-in.

## Action items (also tracked in tasks.md U058)

- [ ] U058 — Ship the DefenseClaw plugin (`src/capabledeputy/integrations/
  defenseclaw_plugin/`). Includes:
  - A Go gateway adapter that calls CD's engine over loopback.
  - A YAML schema mapping DefenseClaw policy fields to CD's PolicyContext.
  - An integration test that drives a recorded DefenseClaw policy through
    the plugin and asserts the CD-side decision is what DefenseClaw's
    operator-curated policy would have produced.
  - A write-up `integrations/defenseclaw_plugin/README.md` framing the
    plugin as "DefenseClaw + CD deterministic decision backend."
