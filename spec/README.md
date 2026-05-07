# TLA+ specification

`CapableDeputy.tla` is a formal model of the v0.1 session graph and
policy engine. It exists so the safety properties from DESIGN.md §3
(label monotonicity, no-silent-egress on PHI, capability-required
ALLOW) are claims a model checker can verify, not just claims in the
prose.

## What's modeled

- **Sessions** with label sets and capability sets (`Labels` and
  `CapabilityKinds × Targets`).
- **Actions**:
  - `GrantCapability(s, kind, target)` — runtime grants a capability
    to a session.
  - `PropagateLabels(s, newLabels)` — a tool result's labels fold
    into the session.
  - `AttemptCall(s, kind, target)` — the agent loop tries to use a
    capability; the policy decision is recorded.
  - `Declassify(s, lbl)` — an explicit, Clark-Wilson-gated removal
    of a label, modeling the approval queue's purpose-session
    declassification path.
- **Policy decision function** (`PolicyDecide`) — pure, total. Mirrors
  `src/capabledeputy/policy/engine.decide()` byte-for-byte at the
  level of conflict rule matching.

## What's checked

The `Inv` invariant bundles the two structural properties:

| Property | Predicate | What it rules out |
|---|---|---|
| `PolicyDecisionTotal` | every recorded decision is `ALLOW`, `DENY`, or `REQUIRE_APPROVAL` | a decision tree gap silently producing some other outcome |
| `NoSilentEgressOnPHI` | every decision tagged `health-meets-egress` is `DENY` | the rule firing but the runtime accepting it as ALLOW |

`CapabilityRequired` is documented in the spec; its operational
content is enforced by the early-return in `PolicyDecide`. TLC's
exhaustive exploration over `Init /\ [][Next]_vars` covers every
interleaving of grants, label propagations, calls, and
declassifications, so any state reachable in the implementation
that violates the rules would be reachable in the model too.

## Running TLC

The spec is checked with [TLC](https://lamport.azurewebsites.net/tla/tools.html).

Easiest path — install the [TLA+ Toolbox](https://github.com/tlaplus/tlaplus/releases),
open `CapableDeputy.tla`, and run a model with the constants in
`CapableDeputy.cfg`.

Command-line:

```bash
# Once tla2tools.jar is on the path:
java -jar tla2tools.jar -config CapableDeputy.cfg CapableDeputy.tla
```

The default model uses 2 sessions × 4 targets × 4 turns; this
finishes in seconds. Increase `MaxTurns` for deeper coverage at the
cost of state space size.

## What this is not

This is a model of the **policy decision** and the **state
transitions** the runtime makes around it — i.e. the architectural
guarantees from DESIGN.md §3. It is not a model of the LLM, of MCP
transport, of the SQLite store, or of any other implementation detail
that lies outside the security boundary.

A future v0.5+ direction is mechanized proofs of the same properties
in Coq/Lean/Isabelle. TLA+ model-checks finitely; mechanized proofs
generalize to all sizes. Both are valuable; we ship the cheaper one
first.
