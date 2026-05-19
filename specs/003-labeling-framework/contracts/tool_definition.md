# Contract: ToolDefinition Extension (003)

Axis C (effect class) lives on the tool/source declaration (FR-005); EXECUTE tiering (FR-042) and the canonical-destination-id contract (FR-048) attach here as well. A `ToolDefinition` missing any required field MUST cause registry validation failure at daemon startup (Principle VI fail-closed).

## Fields (additions to the existing `ToolDefinition`)

```python
class EffectClass(StrEnum):
    OBSERVE         = "OBSERVE"
    FETCH           = "FETCH"
    MUTATE_LOCAL    = "MUTATE_LOCAL"
    DESTROY         = "DESTROY"
    COMMUNICATE     = "COMMUNICATE"
    TRANSACT        = "TRANSACT"
    EXECUTE_SANDBOX = "EXECUTE.sandbox"   # FR-042 preferred tier, contained
    EXECUTE_HOST    = "EXECUTE.host"
    EXECUTE_REMOTE  = "EXECUTE.remote"
    EXECUTE_DEPLOY  = "EXECUTE.deploy"
    ADMINISTER      = "ADMINISTER"
    ACTUATE_PHYSICAL = "ACTUATE_PHYSICAL"

@dataclass(frozen=True)
class ToolProvenance:
    source: Literal["builtin", "curated-mcp", "operator-declared"]
    server: str | None               # for MCP tools
    package_hash: str | None         # for curated MCP servers

@dataclass(frozen=True)
class ToolDefinitionV2:
    # ...existing fields (name, capability_kind, description, params_schema, ...)
    effect_class: EffectClass                          # FR-005
    default_reversibility: ReversibilityLabel          # (degree, agent) — FR-037
    default_mutability_target_facets: MutabilityLabel  # (degree, agent) — FR-039
    social_commitment: bool                            # FR-019 (e.g., emailing a third party = True)
    tool_provenance: ToolProvenance                    # FR-005
    accepts_handles: bool                              # Pattern ③ (FR-047)
    handle_arg_names: tuple[str, ...]                  # which named args may be ReferenceHandles
    surfaces_destination_id: bool                      # FR-048 attestation
    risk_ids: tuple[str, ...]                          # ≥1 internal risk-register id — FR-015
```

## Validation rules (registry-load time)

1. All new fields above are **required**. A `ToolDefinition` missing any → registry refuses to register the tool; daemon refuses to start if a built-in is malformed (Principle VI).
2. `effect_class ∈ {EXECUTE_HOST, EXECUTE_REMOTE, EXECUTE_DEPLOY}` MUST also carry `social_commitment=False` (these are mechanical effects, not social); social commitment lives on `COMMUNICATE`/`TRANSACT`.
3. `accepts_handles == True` ⇒ `handle_arg_names` non-empty AND those args appear in `params_schema`.
4. `surfaces_destination_id == False` ⇒ the tool's effect_class MUST be in `{OBSERVE, FETCH}` (no write/egress allowed without canonical destination id); any other declaration is refused.
5. `risk_ids` MUST be a non-empty subset of the loaded `RiskRegister` ids (FR-015/028); orphans refuse.
6. Effect-class union for wrappers (edge case in spec): a wrapper-tool composed over sub-tools MUST declare the union of sub-tools' effect classes; a wrapper that *under-declares* is rejected at registry-load time.

## Honest scope note

The actual jailed-EXECUTE implementation behind `EXECUTE.sandbox` (the `SandboxActuator` port) is **spec 004**. 003 carries:
- the effect-class enum value (`EXECUTE_SANDBOX`),
- the declaration that `EXECUTE.sandbox` is the preferred tier (FR-042),
- the labeling consequence (contained, egress-free ⇒ reversible/system; FR-040),
- the containment ≠ declassification rule (FR-041).

Until 004 ships, tools declared as `EXECUTE.sandbox` MUST behave as `OverrideRequired` (no actuator available) — never as best-effort host execution. This is the Principle-VI fail-closed treatment of an unfinished substrate.

## CI invariant tests required

- `test_tool_definition_missing_field_refused`: removing any new required field from a builtin tool → daemon start fails.
- `test_effect_class_consistency`: wrapper tools must declare union of sub-tools' effect classes; under-declaration refused.
- `test_unknown_risk_id_refused`: a tool citing a risk id absent from the register fails registration.
- `test_execute_sandbox_without_actuator_fail_closed`: until 004 ships, an `EXECUTE.sandbox` invocation produces `OverrideRequired` rather than running on the host.
- `test_surfaces_destination_id_for_writes`: any tool whose `effect_class` writes/egresses MUST have `surfaces_destination_id=True` (FR-048).
