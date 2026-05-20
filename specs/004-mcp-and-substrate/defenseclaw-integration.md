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

## Integration patterns — six directions, all composable

After a follow-up research pass on DefenseClaw's actual extension surface
(REST API on port 18970, custom scanner plugin workflow, WebSocket tap on
OpenClaw gateway, LiteLLM-compatible guardrail proxy on port 4000,
webhook + OTLP + Splunk HEC audit sinks, external catalog ingestion with
SSRF guards, YAML + Rego policy in `policies/scanners/` and
`policies/guardrail/`), the integration story is richer than the
original three-option framing. DefenseClaw is genuinely built to be
extended, and CD can plug in at six distinct points — most of them
composable rather than mutually exclusive.

### Direction 1 — CD as DefenseClaw policy backend (the runtime decision)

DefenseClaw's runtime guardrails today use regex + optional LLM judge.
Replace that path with CD's deterministic engine via a Go gateway module
that calls CD over loopback HTTP. DefenseClaw's scanners, sandbox, and
audit pipeline are unchanged.

**Task**: `U058` (existing).
**Tradeoff**: a network hop per decision; CD must run as a sidecar.

### Direction 2 — CD as a DefenseClaw custom scanner

DefenseClaw's plugin workflow lets operators register custom scanners
alongside `cisco-ai-skill-scanner`, `cisco-ai-mcp-scanner`, and CodeGuard.
DefenseClaw invokes them during admission and combines verdicts via Rego.
**CD's `engine.decide()` registers as a custom scanner** — DefenseClaw
calls CD during admission, CD returns a verdict, DefenseClaw's policy
engine composes it with the other scanners' findings.

**Task**: `U058D` (new).
**Tradeoff**: only fires at admission, not at every tool dispatch. Pairs
well with Direction 1 for full coverage.

### Direction 3 — CD calls DefenseClaw's scanners as tools

CD's native tools (`security.scan_code`, `security.scan_skill`,
`security.scan_mcp`) dispatch HTTP calls to DefenseClaw's REST API on
port 18970. Operator can scan a candidate ToolDefinition or MCP server
from inside `capdep chat` before installing it.

**Task**: `U058A` (existing).
**Tradeoff**: requires DefenseClaw running locally. Useful for operators
already using DefenseClaw; useful as a standalone scanner integration
even when CD isn't running under DefenseClaw.

### Direction 4 — CD as the LiteLLM-compatible guardrail proxy

DefenseClaw exposes a LiteLLM-compatible guardrail proxy on port 4000
that fronts upstream LLMs. CD can BE that proxy — every model call is
intercepted, axis-B taint is composed onto the planner's context, and
the resulting tool calls flow through CD's engine.

**Task**: `U058E` (new).
**Tradeoff**: this is a deeper integration that re-routes ALL model
traffic through CD; high value for "no LLM in the policy path" (Principle
I) but operationally invasive.

### Direction 5 — CD consumes DefenseClaw audit as taint signals

DefenseClaw fans out scanner verdicts + policy decisions to webhooks /
OTLP / Splunk. CD subscribes to that fan-out as an additional audit-
event source. A CodeGuard finding becomes an axis-B taint signal that
composes into CD's session state — making CD's decisions richer with
DefenseClaw's static-analysis evidence.

**Task**: `U058F` (new).
**Tradeoff**: introduces an asynchronous data path between two security
oracles; requires careful deduplication.

### Direction 6 — CD shares DefenseClaw's catalog ingestion

DefenseClaw pulls capability catalogs from clawhub, smithery, skills.sh,
git, HTTPS YAML, and file with SSRF guards + scanner-driven verdicts.
CD's MCP adapter can reuse that catalog ingestion instead of
reimplementing the SSRF-guarded fetcher.

**Task**: `U058G` (new).
**Tradeoff**: introduces a dependency on DefenseClaw being installed for
catalog operations; would need a fallback for CD-standalone deployments.

### Recommendation

Ship Direction 1 (U058) + Direction 3 (U058A) as the **anchor pair** —
these are the most operationally useful and least invasive. Direction
2 (U058D) is a small follow-on once U058 is stable. Directions 4, 5, 6
are **opportunistic** — implement when an operator specifically needs
them.

The original options A/B/C from the earlier version of this document
collapsed too many distinctions. The actual integration surface is much
richer; design accordingly.

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

## Action items (tracked in tasks.md U058 / U058A / U058D-G)

Anchor pair (recommended first):

- [ ] **U058** — Ship the DefenseClaw plugin (CD-as-policy-backend).
  `src/capabledeputy/integrations/defenseclaw_plugin/` with Go gateway
  adapter, YAML schema mapping, integration test, README framing the
  plugin as "DefenseClaw + CD deterministic decision backend."
- [ ] **U058A** — Ship the DefenseClaw scanner tools (CD-calls-scanners).
  `src/capabledeputy/tools/native/security.py` with `security.scan_code`,
  `security.scan_skill`, `security.scan_mcp` calling DefenseClaw's REST
  API on port 18970.

Small follow-on:

- [ ] **U058D** — Register CD as a DefenseClaw custom scanner.
  CD's `engine.decide()` exposed as a scanner endpoint DefenseClaw
  invokes during admission. Pairs with U058 for full coverage
  (admission + runtime).

Opportunistic — implement when an operator needs them:

- [ ] **U058E** — CD as the LiteLLM-compatible guardrail proxy on port
  4000. Re-routes all model traffic through CD's policy engine; honors
  Principle I (no LLM in policy path) end-to-end.
- [ ] **U058F** — CD consumes DefenseClaw audit fan-out (webhooks / OTLP /
  Splunk) as additional taint signals. CodeGuard finding becomes an
  axis-B raise on the affected session.
- [ ] **U058G** — CD's MCP adapter reuses DefenseClaw's catalog
  ingestion (clawhub, smithery, skills.sh, git, HTTPS YAML, file with
  SSRF guards). Avoids re-implementing the fetcher.
