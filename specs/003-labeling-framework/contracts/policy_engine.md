# Contract: Policy Engine `decide()` (003)

The in-TCB, pure deterministic decision function (Constitution Principle I). Extends the v0.7 signature with axis-D inputs and a distinct `OverrideRequired` outcome variant.

## Signature

```python
def decide(
    *,
    # Axis A
    axis_a: AxisA,                       # category set + tier set, per FR-002/007
    # Axis B
    axis_b: AxisB,                       # provenance lattice position(s), integrity floor (FR-004)
    # Axis C — supplied via the capability + tool def, not as a separate arg
    capability: Capability,              # kind, pattern, expiry, origin, audit_id, depth, ...
    tool: ToolDefinition,                # effect_class, default_reversibility, ..., accepts_handles, surfaces_destination_id
    # Axis D
    axis_d: AxisD,                       # initiator+auth, counterparty/relationship groups, expectedness, reversibility
    # Action + targets
    action: Action,                      # what is being attempted
    target: TargetRef,                   # canonical destination id surfaced by the adapter (FR-048)
    # Session context
    purpose_handle: PurposeHandle,       # structured purpose (FR-046)
    risk_preference: RiskPreference,     # frozen snapshot from session.risk_preference_at_spawn
    # Operator state (all human-declared, AI-read-only)
    bindings: SourceBindingResolver,     # FR-043
    rules: DecisionRuleSet,              # FR-010/026
    envelopes: OutcomeEnvelopeMap,       # FR-030
    override_policies: OverridePolicyMap,# FR-036
    relationship_groups: RelationshipGroupMap,  # FR-033
    expectation_bindings: ExpectationBindingSet,# FR-029
    purposes: PurposeRegistry,           # FR-009/046
    risk_register: RiskRegister,         # FR-015/028
    # Replayable temporal state
    now: datetime,
    used_kinds: frozenset[CapabilityKind],
    cap_uses: dict[UUID, int],
    revoked_audit_ids: frozenset[UUID],
) -> Decision: ...
```

**Outcome type:**
```python
class Decision(StrEnum):
    AUTO            = "auto"
    SUGGEST         = "suggest"
    REQUIRE_APPROVAL = "require-approval"   # within-envelope, ordinary approval (FR-038)
    DENY            = "deny"

@dataclass(frozen=True)
class OverrideRequired:
    """Distinct return path (NOT a Decision value): the action crosses a hard floor;
    nothing in {AUTO, SUGGEST, REQUIRE_APPROVAL, DENY} fits — an Override Grant
    governed by the configured Override Policy is the only sanctioned crossing."""
    floor: HardFloor                # prohibited | admissibility-exclusion | max-tier-clearance | integrity-floor
    policy: OverridePolicy          # disallowed | single-authorized | dual-control
    friction_level: FrictionLevel   # low | medium | maximal
    rationale_risk_ids: list[str]

DecideResult = Decision | OverrideRequired
```

## Invariants (Principle I, III, VI)

1. **Pure function.** `decide(args) → same args` always returns the same `DecideResult`; never reads ambient state; never calls an LLM; never has a side effect (audit is emitted at the *caller* dispatcher around `decide`).
2. **Fail-closed.** Any unbound source, unidentifiable destination, unmapped tool effect class, missing purpose, or unclassifiable input → `DENY` or `OverrideRequired(floor=...)` per the relevant hard-floor rule; **never** a permissive default.
3. **Asymmetry (FR-031).** No non-deterministic input may relax. The signature has no model-output parameter; an advisor proposal is a *separate* mechanism that must produce a human-ratified rule before its outcome can affect `decide`.
4. **Replayable.** Every argument is structured + serializable; calling `decide` with replayed inputs from an audit record reproduces the outcome bit-identically (SC-002).
5. **Audit object shape (FR-021).** The dispatch wrapper emits one event per call: `{decision_id, session_id, decide_result, axis_a, axis_b, axis_c=tool.effect_class, axis_d, target_canonical_id, source_binding?, destination_binding?, rule_matched?, envelope_cell?, risk_preference, risk_ids, mode_selected, override_grant_id?, residual_risk_exception?, audit_id}`. **Model self-narration is never recorded as rationale** (Principle VIII).

## CI invariant tests required (Principle III)

- `test_enforcement_llm_independence` (existing, EXTENDED): assert `decide()` produces no LLM-related side effect; removing any "advisor"/"introspection" path leaves outcomes unchanged.
- `test_decide_is_pure_function` (NEW): property-based with Hypothesis — same inputs → same outputs across permutations.
- `test_failclosed_unbound_inputs` (NEW): every unbound source/destination/tool/purpose/category produces `DENY` or `OverrideRequired`, never `AUTO`/`SUGGEST`.
- `test_no_terminal_unlock_via_rule_dial_ai` (NEW): no rule/envelope/dial/heuristic input can produce `AUTO` for a `prohibited`/hard-floor cell; the only path is `OverrideRequired` honoring the policy.
- `test_pattern3_required_for_restricted` (NEW): a session whose effective tier is `restricted` and whose available tools cannot route through ③ or ⑤ → spawn refused before `decide` is ever called.
