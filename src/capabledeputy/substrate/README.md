# CapableDeputy substrate ports

The `substrate/` package holds the **ports** — Protocol interfaces
operators implement (or pick from the built-ins) to refine
CapableDeputy's behaviour without touching the chokepoint code.

Every port is a pure-function contract: same inputs → same outputs,
no I/O inside, no mutation of inputs. That's what makes them
**replayable, auditable, and safe to register from operator config**.

## The five primitive ports

### 1. `RaiseOnlyInspector` (FR-025) — at `at_ingest.value_in`

Looks at a freshly-ingested tool result and returns a label delta
that can only RAISE session axes. Use for tainting based on content
(SSN detector, injection-pattern catcher, multi-vendor disclosure
flagger).

```python
class RaiseOnlyInspector(Protocol):
    def inspect(
        self, *, value, current_axis_a, current_axis_b,
    ) -> InspectorDelta: ...
```

See: `inspector_port.py`

### 2. `DecisionInspector` (spec 004 P0) — at `at_chokepoint.decision`

Runs AFTER the standard policy decision; may RELAX (loosen) or
TIGHTEN (strengthen) the proposed outcome. Composes monotonically:
TIGHTEN beats RELAX, most-restrictive wins.

```python
class DecisionInspector(Protocol):
    name: str
    def inspect(
        self, *, action, session, proposed_outcome,
    ) -> DecisionRelax | DecisionTighten | None: ...
```

Built-ins: `SelfEgressRelaxer`, `AfterHoursPurchaseTightener`.

See: `decision_inspector_port.py` + `decision_inspectors_builtin.py`

### 3. `DeclassifyingTransformer` (spec 004 P0) — at `at_ingest.declassifier_chain`

Transforms a tool's output AND emits a structural-proof so labels
attached to THAT result are reduced. Session label_set still grows
monotonically; declassifier reduces per-result label propagation.

```python
class DeclassifyingTransformer(Protocol):
    name: str
    def declassify(
        self, *, value, current_axis_a, current_axis_b, context=None,
    ) -> DeclassifyResult | None: ...
```

Built-ins: `RegexRedactor` (PII → [REDACTED]), `SchemaProjector`
(drop unknown fields).

See: `declassifier_port.py` + `declassifiers_builtin.py`

### 4. `SamplingMediator` (spec 004 P1) — upstream MCP sampling/createMessage

When an upstream MCP server requests sampling (delegated inference),
the mediator routes via the daemon's LLM under chokepoint policy.

Built-ins:
  - `LiteLLMSamplingMediator` — route to the main LLM
  - `RefuseAllSamplingMediator` — default-safe; refuses
  - `AllowlistSamplingMediator` — delegates only for declared servers

See: `sampling_port.py` + `sampling_mediators_builtin.py`

### 5. `ElicitationMediator` (spec 004 P1) — upstream MCP elicitation/*

When an upstream server prompts the user mid-flow, the mediator
decides whether to route the prompt through the approval queue
(operator-facing prompt) or refuse outright.

Built-ins:
  - `RefuseAllElicitationMediator` — default-safe
  - `AllowlistElicitationMediator` — gate by server
  - `ApprovalQueueElicitationMediator` — route to operator approval

See: `elicitation_port.py` + `elicitation_mediators_builtin.py`

## Substrate utilities

### `policy_script_host.py` — operator-authored policy in a script

`PolicyScriptHost` is the runtime that compiles + executes
operator-written policy scripts. Two implementations:

- `SafePythonScriptHost` — pure-Python reference; AST-validated,
  step-counted, hermetic. NOT a security boundary; useful for
  prototyping the operator's policy language.
- Starlark host via `starlark-rust + PyO3` — deferred but the port
  is in place; drop-in replacement when needed.

Operator scripts implement the `inspect(action, session,
proposed_outcome)` shape:

```python
def inspect(action, session, proposed_outcome):
    if action.get("kind") == "QUEUE_PURCHASE":
        return tighten(to="require_approval", rule="audit-purchases",
                       rationale="all purchases need explicit ack")
    return None
```

The `relax(...)`, `tighten(...)`, `abstain()` helpers are injected
into the script's globals automatically.

### `hook_registry.py` — named lifecycle hooks (T020)

Operators register primitives at named hooks rather than poking
PolicyContext tuple fields directly. The 9 standard hooks:

| Hook | What runs here |
|---|---|
| `at_ingest.value_in` | RaiseOnlyInspectors |
| `at_ingest.declassifier_chain` | DeclassifyingTransformers |
| `at_chokepoint.pre_decide` | (reserved) |
| `at_chokepoint.decision` | DecisionInspectors |
| `at_chokepoint.post_decide` | post-decision audit |
| `at_dispatch.pre_dispatch` | about to invoke a handler |
| `at_dispatch.post_dispatch` | handler returned |
| `at_session.spawn` | new session created |
| `at_session.terminate` | session ending |

Typo-defensible: registering at an unknown hook raises HookError
listing the valid options.

```python
registry = HookRegistry()
registry.register("at_chokepoint.decision", SelfEgressRelaxer(...))
registry.register("at_ingest.value_in", PIIRaiseInspector())

# Chokepoint queries:
for inspector in registry.get("at_chokepoint.decision"):
    ...
```

## Composition guarantees

- **Inspector composition** (raise-only): monotone-inherit. Multiple
  inspectors compose by `most_restrictive_inherit_axis_*`; the final
  axes are never less restrictive than any single inspector returned.
- **Decision inspector composition**: TIGHTEN beats RELAX. Among
  tightens, strictest wins. Among relaxes, loosest wins. Non-monotone
  outcomes (relax→stricter, tighten→looser) are rejected as protocol
  violations.
- **Declassifier composition**: sequential. Each declassifier sees the
  previous one's output. The session's label_set still grows
  monotonically; declassifiers only reduce PER-RESULT propagation.

## Audit trail

Each primitive's activation emits a typed audit event:

| Primitive | Event |
|---|---|
| RaiseOnlyInspector raised axes | `inspector.applied` |
| DecisionInspector adjusted decision | `decision_inspector.applied` |
| DeclassifyingTransformer transformed value | `declassifier.applied` |

Each event carries structural metadata (rule names, audit_diff,
structural_proof_kind) so auditors can reconstruct WHY a primitive
fired.

## Where to look next

- `src/capabledeputy/policy/engine.py` — the chokepoint that
  consumes these ports
- `src/capabledeputy/tools/client.py` — `LabeledToolClient.call_tool`
  is the orchestrator that invokes the inspectors / decision
  inspectors / declassifiers at the right lifecycle moments
- `src/capabledeputy/compliance/oscal.py` — the chokepoint rules
  primitives gate against → NIST 800-53 mapping (operators publish
  to compliance teams via `capdep compliance-emit-oscal`)
