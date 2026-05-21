# CapableDeputy Policy Language + Programmatic Primitives Reference

**Audience:** operators configuring their CapableDeputy installation,
and AI models loaded with CapableDeputy as context that need to
understand the policy surface to assist with configuration.

**Status:** consolidating reference. Brings together the existing
policy language (caps, bindings, envelopes, overrides, profiles,
T012 fields) with the new programmatic primitives (inspectors,
decision inspectors, declassifying transformers, hooks) into one
coherent reference.

**Scope:** describes WHAT can be configured and HOW. Does not
duplicate the design-rationale discussion in `mcp-protocol-fit.md`
and `mcp-policy-integration.md`; references those for the
why-questions.

---

## Table of Contents

1. [Overview](#1-overview)
2. [The decision flow](#2-the-decision-flow)
3. [Policy language elements](#3-policy-language-elements)
   - 3.1 [Capabilities](#31-capabilities)
   - 3.2 [Source-Location Label Bindings](#32-source-location-label-bindings)
   - 3.3 [Outcome Envelopes + Risk Preference Dial](#33-outcome-envelopes--risk-preference-dial)
   - 3.4 [Override Policies](#34-override-policies)
   - 3.5 [Clearance Profiles](#35-clearance-profiles)
   - 3.6 [Tool Definitions (T012 fields)](#36-tool-definitions-t012-fields)
   - 3.7 [Brewer-Nash Conflict Rules](#37-brewer-nash-conflict-rules)
   - 3.8 [Approval Bundles](#38-approval-bundles)
   - 3.9 [Relationship Groups](#39-relationship-groups)
4. [Programmatic Policy Primitives](#4-programmatic-policy-primitives)
   - 4.1 [RaiseOnlyInspector](#41-raiseonlyinspector)
   - 4.2 [DecisionInspector](#42-decisioninspector)
   - 4.3 [DeclassifyingTransformer](#43-declassifyingtransformer)
   - 4.4 [Per-arg Payload Labels](#44-per-arg-payload-labels)
   - 4.5 [Per-server Policy Modules](#45-per-server-policy-modules)
5. [The Hook System](#5-the-hook-system)
   - 5.1 [Ingress hooks](#51-ingress-hooks)
   - 5.2 [LLM-boundary hooks](#52-llm-boundary-hooks)
   - 5.3 [Policy-boundary hooks](#53-policy-boundary-hooks)
   - 5.4 [Egress hooks](#54-egress-hooks)
   - 5.5 [Session-lifecycle hooks](#55-session-lifecycle-hooks)
   - 5.6 [Bundle-lifecycle hooks](#56-bundle-lifecycle-hooks)
   - 5.7 [Storage hooks](#57-storage-hooks)
   - 5.8 [Per-hook eligibility matrix](#58-per-hook-eligibility-matrix)
   - 5.9 [Composition and ordering](#59-composition-and-ordering)
6. [Audit](#6-audit)
7. [Trust Tiers](#7-trust-tiers)
8. [Declassification Floors](#8-declassification-floors)
9. [Shadow Mode](#9-shadow-mode)
10. [Worked Examples](#10-worked-examples)
11. [Configuration File Shapes (Reference)](#11-configuration-file-shapes-reference)
12. [Glossary of Annotations](#12-glossary-of-annotations)

---

## 1. Overview

CapableDeputy's policy engine mediates every action through a single
deterministic chokepoint (`engine.decide()`). Around that chokepoint
sits a layered policy language:

- **Static policy** — capabilities, bindings, envelopes, profiles,
  override policies, conflict rules. Declared in YAML/JSON config and
  loaded at daemon start.
- **Tool-declared policy** — T012 fields on each tool definition
  (effect_class, default_reversibility, social_commitment, etc.).
  Declared in code (for native tools) or in MCP server mappings
  (for upstream tools).
- **Programmatic policy** — Python modules + YAML DSL that hook into
  named data-flow boundaries. Three primitives: `RaiseOnlyInspector`,
  `DecisionInspector`, `DeclassifyingTransformer`. These let the
  operator encode their standing intent so the chokepoint can
  resolve more decisions without operator interaction.

Who configures what:

| Layer | Authored by | Loaded from |
|---|---|---|
| Static policy | Operator | `configs/*.yaml` |
| Tool definitions | Native: code; MCP: mapping config | `src/capabledeputy/tools/native/*.py` or `configs/upstream_servers/*.yaml` |
| Programmatic primitives | Operator | `configs/policies/*.yaml` or `configs/policies/*.py` |

**FR-031 asymmetry:** all policy is operator-authored. The agent
cannot author policy. This is structural — policy directories are
operator-controlled; the agent never has write access to them.

---

## 2. The decision flow

Every `engine.decide()` call follows a deterministic sequence. The
operator's policy plugs in at named points along this flow.

```
INCOMING data (tool result, resource read, etc.)
                │
                ▼
   ┌──────────────────────────┐
   │  ingress hooks fire      │  RaiseOnlyInspector(s) only
   │  (per-server inspectors, │  Operator config selects which run
   │   regex DSL, Python)     │  Hooks: §5.1, §5.5 (on_label_propagation)
   └────────────┬─────────────┘
                ▼
        Session label set updated (axes raised; never lowered)
                │
                ▼
        Agent decides next action: tool call
                │
                ▼
   ┌──────────────────────────┐
   │  pre_chokepoint hook     │  RaiseOnlyInspector + DeclassifyingTransformer
   │  fires                   │  Hook: §5.3
   └────────────┬─────────────┘
                ▼
   ┌──────────────────────────┐
   │  engine.decide() runs    │
   │   1. Cap match           │  §3.1
   │   2. Brewer-Nash rules   │  §3.7
   │   3. BLP clearance check │  §3.5
   │   4. Reversibility gate  │  §3.6
   │   5. Destructive-op gate │  §3.6
   │   6. Envelope dial       │  §3.3
   │   7. Optimistic-auto     │  §3.6
   │   8. DecisionInspector   │  §4.2 — relax/tighten the outcome
   │      hook fires          │  Hook: §5.3 (at_chokepoint.decision)
   └────────────┬─────────────┘
                ▼
        Decision: ALLOW / REQUIRE_APPROVAL / DENY / OVERRIDE_REQUIRED
                │
                ▼
        If REQUIRE_APPROVAL → approval queue (operator reviews)
        If OVERRIDE_REQUIRED → override grant FSM
        If ALLOW → continue
                │
                ▼
   ┌──────────────────────────┐
   │  pre_dispatch hook fires │  DeclassifyingTransformer only
   │                          │  Hook: §5.3
   └────────────┬─────────────┘
                ▼
        Tool handler runs; produces ToolResult
                │
                ▼
   ┌──────────────────────────┐
   │  on_tool_result hook     │  RaiseOnlyInspector + DeclassifyingTransformer
   │  fires (incoming flow)   │  Hook: §5.1
   └────────────┬─────────────┘
                ▼
        ToolCallOutcome returned to caller
                │
                ▼
        Session label set updated with any new inherent labels
```

Each rectangle is an extension point. Operator config registers
primitives at hooks; the engine runs them in declared order.

---

## 3. Policy language elements

### 3.1 Capabilities

A capability is an operator-granted authorization for a specific
kind of action against a target pattern.

**Schema (Python dataclass; declared in code or YAML):**

```python
@dataclass(frozen=True)
class Capability:
    kind: CapabilityKind         # READ_FS, SEND_EMAIL, QUEUE_PURCHASE, etc.
    pattern: str                 # glob pattern over targets; "*" for all
    origin: CapabilityOrigin     # USER_APPROVED, DELEGATED, OVERRIDE_GRANTED
    expiry: CapabilityExpiry = CapabilityExpiry.PERSISTENT
    expires_at: datetime | None = None
    max_amount: int | None = None        # for QUEUE_PURCHASE: dollars cap
    allows_destructive: bool = False     # bypass destructive-op gate
    rate_limit: RateLimit | None = None  # sliding-window cap
    revoked_by: frozenset[CapabilityKind] = frozenset()
    override_grant_id: UUID | None = None # set when minted via override
    audit_id: UUID = field(default_factory=uuid4)
```

**Capability kinds** (`src/capabledeputy/policy/capabilities.py:CapabilityKind`):

| Kind | Action | Destructive-op gate? |
|---|---|---|
| `READ_FS` | Read local/memory data | No |
| `WRITE_FS` | Write local/memory data | No (use CREATE/MODIFY instead) |
| `CREATE_FS` | Create new entity | No |
| `MODIFY_FS` | Modify existing entity | **Yes** — requires `allows_destructive` |
| `DELETE_FS` | Delete entity | **Yes** |
| `SEND_EMAIL` | Outgoing social-commitment | No (gate via FR-019) |
| `QUEUE_PURCHASE` | Queue purchase | No (gate via FR-019) |
| `WEB_FETCH` | Network read | No |
| `CALENDAR_READ` | Read calendar | No |
| `CREATE_CAL` | Create calendar event | No |
| `MODIFY_CAL` | Modify calendar event | **Yes** |
| `DELETE_CAL` | Delete calendar event | **Yes** |

**`CapabilityOrigin`:**
- `USER_APPROVED` — operator-granted in config
- `DELEGATED` — granted via session-graph delegation (attenuation
  preserved)
- `OVERRIDE_GRANTED` — minted via override path (single-use)

**YAML config example:**

```yaml
sessions:
  default:
    capabilities:
      - kind: READ_FS
        pattern: "*"
        origin: USER_APPROVED
      - kind: SEND_EMAIL
        pattern: "*@example.com"
        origin: USER_APPROVED
        rate_limit:
          max_calls: 50
          window_seconds: 3600
      - kind: MODIFY_FS
        pattern: "/home/me/notes/**"
        origin: USER_APPROVED
        allows_destructive: true
      - kind: QUEUE_PURCHASE
        pattern: "*"
        origin: USER_APPROVED
        max_amount: 5000
```

**Capability matching:** at `decide()` time, the engine finds the
first capability where:
- `kind` matches the action's CapabilityKind
- `pattern` glob-matches the action's target
- `max_amount` ≥ action.amount (when relevant)
- not expired
- rate limit window not exhausted
- not revoked by a previously-used kind

A miss returns `Decision.DENY` with `rule="no-matching-capability"`.

### 3.2 Source-Location Label Bindings

Bindings map operator-curated URI patterns to compartment labels.
Used to derive labels on data read from a source (file paths, web
URLs, MCP resource URIs).

**Schema (`policy/bindings.py:SourceLocationLabelBinding`):**

```python
@dataclass(frozen=True)
class SourceLocationLabelBinding:
    name: str
    scope_pattern_canonical: str        # glob over canonical URI
    category: str                       # e.g., "work", "clinical"
    default_tier: Tier                  # NONE/SENSITIVE/REGULATED/RESTRICTED/PROHIBITED
    reversibility: ReversibilityLabel | None = None
    mutability: MutabilityLabel | None = None
    write_discipline: WriteDiscipline = WriteDiscipline.IN_PLACE
    risk_ids: tuple[str, ...] = ()
    assignment_provenance: str = "operator-declared"
```

**Composition rules:**
- Most-specific binding wins for category + tier (longest literal
  prefix in the pattern)
- Reversibility / mutability / write_discipline compose
  most-restrictive across all matched bindings (FR-043)
- `risk_ids` set-union across all matched bindings

**YAML config example:**

```yaml
bindings:
  - name: my-work-notion
    scope_pattern_canonical: "notion://workspace/work/**"
    category: work
    default_tier: sensitive
    reversibility: {degree: reversible-with-friction, agent: human}
    risk_ids: [r-employee-confidential]

  - name: my-finance-folder
    scope_pattern_canonical: "file:///home/me/finance/**"
    category: finance
    default_tier: restricted
    risk_ids: [r-financial-data]

  - name: my-public-research
    scope_pattern_canonical: "file:///home/me/public-research/**"
    category: research
    default_tier: none
```

### 3.3 Outcome Envelopes + Risk Preference Dial

An envelope declares the `{strictest, loosest}` range of outcomes for
a specific decision cell. The operator's `risk_preference` dial
picks a point within each cell's envelope.

**Cell key:** `(category, effect_class, decision_context_canonical, reversibility)`

**Schema:**

```python
@dataclass(frozen=True)
class OutcomeEnvelope:
    cell: CellKey
    strictest: RuleOutcome  # DENY / REQUIRE_APPROVAL / SUGGEST / AUTO
    loosest: RuleOutcome    # must be ≥ strictest in autonomy order
```

**Dial values** (`RiskPreference`):
- `cautious` — pick `strictest` for every cell
- `balanced` — pick the midpoint (round toward stricter)
- `permissive` — pick `loosest` for every cell

**SC-010 invariant:** hard-floor cells have `strictest == loosest`.
The dial cannot move them. Operator config establishes the floor.

**YAML config example:**

```yaml
risk_preference: balanced

envelopes:
  - cell:
      category: work
      effect: data.create_local
      decision_context_canonical: "principal:alice"
      reversibility: reversible
    strictest: require_approval
    loosest: auto

  - cell:
      category: finance
      effect: social.send_email
      decision_context_canonical: "principal:alice"
      reversibility: irreversible
    strictest: deny     # hard floor — operator-locked
    loosest: deny
```

### 3.4 Override Policies

Per hard-floor escape hatches. Each hard floor type has an override
policy declaring who can request, who can attest, and what attestation
shape (single-control vs. dual-control).

**Schema:**

```python
@dataclass(frozen=True)
class OverridePolicyEntry:
    floor: HardFloor                       # MAX_TIER_CLEARANCE, etc.
    policy: OverridePolicy                 # SINGLE_CONTROL or DUAL_CONTROL
    authorized_principal_ids: frozenset[str]
    attester_principal_ids: frozenset[str] # required for DUAL_CONTROL
    expiry_seconds: int = 300
```

**Hard floor types:**
- `MAX_TIER_CLEARANCE` — BLP clearance ceiling crossing
- `SOCIAL_COMMITMENT` — irreversible egress
- `BREWER_NASH_DENY` — conflict-rule firing with `Decision.DENY`
- `DESTRUCTIVE_OP_NO_DESTRUCTIVE_CAP` — modify/delete without
  `allows_destructive`

**Override grant FSM:** `pending_attestation` → `active` → `consumed`.
Single-use; consumed on first apply.

**YAML config example:**

```yaml
override_policies:
  - floor: max-tier-clearance
    policy: dual-control
    authorized_principal_ids: [alice]
    attester_principal_ids: [security-officer, manager]
    expiry_seconds: 300

  - floor: social-commitment
    policy: single-control
    authorized_principal_ids: [alice]
    expiry_seconds: 600
```

### 3.5 Clearance Profiles

A profile is an operator-declared role with a `max_tier` ceiling
(FR-008 BLP). The session inherits the profile's ceiling.

**Schema:**

```python
@dataclass(frozen=True)
class ContextProfile:
    id: str
    user_pattern: str               # "*" matches all users
    use_case: str                   # "general", "compliance", etc.
    max_tier: Tier | None = None    # ceiling for read-up
    category_overrides: tuple[CategoryOverride, ...] = ()
```

**Tiers** (ordered):
- `NONE` — public/unmarked
- `SENSITIVE` — internal/personal
- `REGULATED` — protected by regulation (PHI, etc.)
- `RESTRICTED` — highly restricted
- `PROHIBITED` — never readable

**YAML config example:**

```yaml
profiles:
  - id: auditor
    user_pattern: "*"
    use_case: compliance
    max_tier: restricted

  - id: intern
    user_pattern: "*"
    use_case: general
    max_tier: none
```

Apply to a session via `clearance_profile_id`.

### 3.6 Tool Definitions (T012 fields)

Each tool declares its policy-relevant properties. For native tools,
declared in Python. For MCP-mapped tools, declared in mapping config
or inferred via heuristic + `io.joneslaw/capabilitydeputy/*` annotations.

**Schema (`ToolDefinition`):**

| Field | Type | Purpose |
|---|---|---|
| `name` | str | Tool identifier |
| `capability_kind` | CapabilityKind | What cap kind authorizes calling this |
| `target_arg` | str | Which arg supplies the `action.target` for cap match |
| `amount_arg` | str | Which arg supplies `action.amount` (for purchase-like tools) |
| `effect_class` | str | T012 effect class (`data.read_local`, `social.send_email`, etc.) |
| `default_reversibility` | dict | `{degree, agent}` — reversibility class |
| `social_commitment` | bool | If true, forces irreversible/external regardless of declared |
| `surfaces_destination_id` | bool | If true, target arg is the canonical destination |
| `accepts_handles` | bool | If true, supports Pattern ③ ReferenceHandle |
| `handle_arg_names` | tuple[str, ...] | Which args may be handle UUIDs |
| `payload_args` | tuple[str, ...] | Which args carry data payloads (vs. command params) — **NEW** |
| `inherent_labels` | frozenset[Label] | Labels that propagate when this tool returns |
| `tool_provenance` | str | `operator-curated` / `vendor-vetted` / `curated-mcp` |
| `approval_route` | ApprovalRoute | How approval renders for this tool |
| `parameters_schema` | dict | JSON schema for tool args |

**Reversibility degrees:**
- `reversible` — agent of reversal can undo
- `reversible-with-friction` — undo possible but requires human action
- `irreversible` — cannot be undone

**Reversal agents:**
- `system` — the runtime can undo (e.g., delete a memory key)
- `human` — a human can undo (e.g., revoke a sent email is messy)
- `external` — beyond our control (e.g., money sent)

**Reversibility gate** (in `decide()`):
- `reversible + system` + non-egressing → AUTO (FR-034 optimistic-auto)
- `reversible-with-friction + *` OR `reversible + non-system` →
  REQUIRE_APPROVAL
- `irreversible + *` → DENY (escalate to override)

**`io.joneslaw/capabilitydeputy/*` annotations on MCP tools:**

For MCP servers, T012 fields can be declared via the `_meta`
namespace per MCP spec convention.

| Annotation key | Type | Maps to |
|---|---|---|
| `io.joneslaw/capabilitydeputy/effect_class` | str | `effect_class` |
| `io.joneslaw/capabilitydeputy/default_reversibility` | `{degree, agent}` | `default_reversibility` |
| `io.joneslaw/capabilitydeputy/social_commitment` | bool | `social_commitment` |
| `io.joneslaw/capabilitydeputy/category_hint` | str | suggested label category (operator may map or ignore) |
| `io.joneslaw/capabilitydeputy/tier_hint` | str | suggested tier |
| `io.joneslaw/capabilitydeputy/flow_pattern_preferred` | enum | pattern_1 / pattern_2 / pattern_3 / pattern_4 / pattern_5 |
| `io.joneslaw/capabilitydeputy/handle_arg_names` | `[str]` | which args are ReferenceHandle |
| `io.joneslaw/capabilitydeputy/payload_args` | `[str]` | which args are data payloads |
| `io.joneslaw/capabilitydeputy/batch_kind` | str | batching group |
| `io.joneslaw/capabilitydeputy/idempotent` | bool | retry-safe |
| `io.joneslaw/capabilitydeputy/operator_ratifiable` | bool | can be saved as a ritual |
| `io.joneslaw/capabilitydeputy/safe_to_forward` | bool | (prompts) safe to auto-forward to LLM |

### 3.7 Brewer-Nash Conflict Rules

Static rules declaring which combinations of labels conflict and
what the outcome is.

**Schema:**

```python
@dataclass(frozen=True)
class ConflictRule:
    name: str
    triggers: frozenset[Label]   # labels that must be present
    conflicts: frozenset[Label]  # labels that must also be present
    decision: Decision            # DENY or REQUIRE_APPROVAL
```

**Default rules** (`policy/rules.py:CONFLICT_RULES`):

| Rule | Triggers | Conflicts | Outcome |
|---|---|---|---|
| `untrusted-meets-egress` | `UNTRUSTED_EXTERNAL`, `UNTRUSTED_USER_INPUT` | `EGRESS_EMAIL`, `EGRESS_PURCHASE` | DENY |
| `health-meets-egress` | `CONFIDENTIAL_HEALTH` | `EGRESS_EMAIL`, `EGRESS_PURCHASE` | DENY |
| `financial-meets-email` | `CONFIDENTIAL_FINANCIAL` | `EGRESS_EMAIL` | DENY |
| `financial-meets-purchase` | `CONFIDENTIAL_FINANCIAL` | `EGRESS_PURCHASE` | REQUIRE_APPROVAL |

Custom rules can be added via config (loaded at engine init).

### 3.8 Approval Bundles

A bundle is a dry-run-collected impact tree of an entire workflow,
reviewed by the operator as a unit.

**Schema:**

```python
@dataclass
class WorkflowImpact:
    bundle_id: UUID
    program_hash: str
    created_at: datetime
    steps: list[WorkflowStep]
    gates: list[BundledApproval]
    parse_error: str | None = None
    runtime_error: str | None = None
```

**Lifecycle:**
1. `dry_run_for_bundle(source, registry)` — produces a
   `WorkflowImpact` with all gates the workflow would trip
2. Operator reviews; calls `impact.approve_all()` or
   `impact.deny_all()` or `impact.approve_subset(...)`
3. `execute_with_approved_bundle(source, approved_impact, ...)` —
   re-runs the source with pre-applied gates

**Source-hash pinning:** if the source changes between preview and
execution, `BundleMismatchError` is raised. The operator approved a
specific program; that's what executes.

### 3.9 Relationship Groups

Brewer-Nash compartments expressed as named groups. Sessions can
belong to relationship groups; rules can require/forbid membership.

**Schema:** see `policy/relationships.py`.

---

## 4. Programmatic Policy Primitives

These are operator-authored Python modules (or YAML DSL for simple
cases) that hook into named data-flow boundaries. Three distinct
primitives, each with strict composition rules.

### 4.1 RaiseOnlyInspector

**Purpose:** label data flowing INTO a session based on its content.
Monotone-only — can RAISE labels but never lower.

**Port** (`substrate/inspector_port.py`):

```python
@dataclass(frozen=True)
class InspectorDelta:
    axis_a_raise: AxisA = field(default_factory=AxisA)
    axis_b_raise: AxisB = field(default_factory=AxisB)

class RaiseOnlyInspector(Protocol):
    def inspect(
        self,
        *,
        value: object,
        current_axis_a: AxisA,
        current_axis_b: AxisB,
    ) -> InspectorDelta: ...
```

**Composition guarantee:** the runtime's `most_restrictive_inherit_axis_*`
is monotone by construction. A buggy or compromised inspector that
tries to lower labels has its lowering attempt structurally discarded.

**Where it can run:** any ingress hook (§5.1); `on_label_propagation`
(§5.5); `pre_chokepoint` (§5.3).

**YAML DSL form (regex-based):**

```yaml
inspectors:
  - name: detect_ssn
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    on_match:
      raise_axis_a:
        - category: pii
          tier: restricted
      raise_axis_b:
        level: external-untrusted

  - name: detect_credit_card
    pattern: '\b(?:\d{4}[-\s]?){3}\d{4}\b'
    on_match:
      raise_axis_a:
        - category: finance
          tier: restricted
```

**Python module form:**

```python
# configs/inspectors/phi_detector.py
import re
from capabledeputy.substrate.inspector_port import (
    RaiseOnlyInspector, InspectorDelta,
)
from capabledeputy.policy.labels import (
    AxisA, AxisACategory, AxisB, AxisBEntry, ProvenanceLevel,
)
from capabledeputy.policy.tiers import Tier

class PHIDetector(RaiseOnlyInspector):
    _PATIENT_ID = re.compile(r'\bMRN[-\s]?\d{6,}\b')
    _DATE_OF_BIRTH = re.compile(r'\bDOB:\s?\d{1,2}/\d{1,2}/\d{2,4}\b')

    def inspect(self, *, value, current_axis_a, current_axis_b):
        text = str(value)
        if self._PATIENT_ID.search(text) or self._DATE_OF_BIRTH.search(text):
            return InspectorDelta(
                axis_a_raise=AxisA(categories=(
                    AxisACategory(category="clinical", tier=Tier.REGULATED),
                )),
                axis_b_raise=AxisB(entries=(
                    AxisBEntry(level=ProvenanceLevel.EXTERNAL_UNTRUSTED),
                )),
            )
        return InspectorDelta()
```

**Registration (YAML, per-server scoped):**

```yaml
upstream_servers:
  - id: notion-mcp
    incoming_inspectors:
      - configs/inspectors/phi_detector.py:PHIDetector
      - dsl:
          name: detect_ssn
          pattern: '\b\d{3}-\d{2}-\d{4}\b'
          on_match:
            raise_axis_a:
              - {category: pii, tier: restricted}
```

### 4.2 DecisionInspector

**Purpose:** at `decide()` time, examine the proposed outcome plus
full context and optionally relax or tighten the decision. **Does
not change labels.** Operates on the decision only.

**Port:**

```python
@dataclass(frozen=True)
class DecisionRelax:
    to: Decision                  # the new outcome
    rule: str                     # rule identifier for audit
    rationale: str                # human-readable

@dataclass(frozen=True)
class DecisionTighten:
    to: Decision
    rule: str
    rationale: str

class DecisionInspector(Protocol):
    name: str

    def inspect(
        self,
        *,
        action: Action,
        session: Session,
        proposed_outcome: PolicyDecision,
    ) -> DecisionRelax | DecisionTighten | None: ...
```

**Composition rules:**

1. **Hard floors are immovable.** A DecisionInspector cannot relax:
   - `SC-010` hard-floor envelopes (`strictest == loosest`)
   - `FR-008` BLP read-up refusal
   - `FR-031` AI-authored asymmetry (inspectors must load from
     operator-controlled directory)
   - `FR-036` distinct-attester requirement for dual-control
   - Brewer-Nash rules with `Decision.DENY` outcomes (the
     no-declassifier rules — health-meets-egress,
     financial-meets-purchase as configured)

2. **Monotone with envelope.** The final outcome is:
   ```
   compose = most_restrictive_of(
     envelope_strictest,
     standard_proposed_outcome,
     inspector_outcome,
   )
   ```
   Inspectors can relax UP TO the envelope's strictest, never below.

3. **Pure functions.** No I/O. Deterministic. Testable.

4. **Operator-authored only.** Loaded from
   `configs/decision_inspectors/`. Never agent-authored.

5. **Audited.** Every relax/tighten fires
   `DECISION_RELAXED`/`DECISION_TIGHTENED` with the inspector name
   and rationale.

**YAML DSL form:**

```yaml
decision_rules:
  - name: self-email-auto-approve
    relax:
      from: require_approval
      to: allow
    when:
      tool: email.send
      target_in: ["marc@joneslaw.io", "marc.t.jones@gmail.com"]
      only_blocker: reversibility-irreversible
    rationale: "Self-correspondence; not third-party egress."

  - name: late-night-purchase-tighten
    tighten:
      from: allow
      to: require_approval
    when:
      tool: purchase.queue
      time_window: {hour_of_day: [22, 6]}
    rationale: "Operator wants extra confirmation on late-night purchases."

  - name: monthly-accountant-relaxation
    relax:
      from: deny
      to: require_approval
    when:
      tool: email.send
      target: accountant@firm.example
      session_labels_include: [confidential.financial]
      time_window: {day_of_month: [1, 30], hour_of_day: [9, 17]}
    rationale: "Pre-approved monthly statement window."
```

**Python module form:**

```python
# configs/decision_inspectors/self_egress.py
from capabledeputy.policy.decision_inspector import (
    DecisionInspector, DecisionRelax,
)
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.rules import Decision

class SelfEgressAutoApprove(DecisionInspector):
    name = "self-egress-auto-approve"

    SELF_ADDRESSES = frozenset({
        "marc@joneslaw.io",
        "me@personal.example",
        "marc.t.jones@gmail.com",
    })

    def inspect(self, *, action, session, proposed_outcome):
        if action.kind is not CapabilityKind.SEND_EMAIL:
            return None
        if action.target not in self.SELF_ADDRESSES:
            return None
        if proposed_outcome.rule != "reversibility-irreversible":
            return None
        return DecisionRelax(
            to=Decision.ALLOW,
            rule="self-egress-auto-approved",
            rationale=(
                "Recipient is in operator's self-reference set; the "
                "egress is internal correspondence, not a third-party "
                "social commitment."
            ),
        )
```

### 4.3 DeclassifyingTransformer

**Purpose:** transform data AND lower labels by construction.
Distinct from inspectors — declassifiers CHANGE THE DATA, and the
new label is justified by the transformation, not asserted.

**Port:**

```python
@dataclass(frozen=True)
class DeclassifyContext:
    hook: str                     # which hook fired
    action: Action | None = None  # if at pre_chokepoint/pre_dispatch
    session_id: UUID | None = None

@dataclass(frozen=True)
class DeclassifyResult:
    transformed_value: object
    new_axis_a: AxisA              # may be lower than input
    new_axis_b: AxisB              # may be lower than input
    audit_diff: str                # human-readable description
    structural_proof_kind: str     # "schema-validated" / "regex-redacted" / etc.

class DeclassifyingTransformer(Protocol):
    name: str
    rationale: str

    def declassify(
        self,
        *,
        value: object,
        current_axis_a: AxisA,
        current_axis_b: AxisB,
        context: DeclassifyContext,
    ) -> DeclassifyResult | None: ...
```

**Safety constraints:**

1. **Operator-authored only.** Loaded from
   `configs/declassifiers/`. Never agent-authored.

2. **Pure functions.** No I/O. Deterministic.

3. **Transformation is the proof.** If a declassifier claims to remove
   sensitive content, the returned value must not contain it. The
   audit diff records exactly what was transformed.

4. **`structural_proof_kind` mandatory.** Declares HOW the
   transformation justifies the lower label:
   - `"schema-validated"` — output conforms to a bounded schema
   - `"regex-redacted"` — pattern-matched content was redacted
   - `"field-extracted"` — specific fields extracted from larger
     structure
   - `"recipient-context"` — sensitivity depends on the recipient

5. **Declassification floors hold.** Operator config declares a
   minimum tier per category that no declassifier can go below
   (see §8).

6. **Forbidden at untrusted-ingress hooks.** Cannot run at hooks
   receiving data from untrusted sources (would let a malicious
   server feed crafted data that "looks safe" and get its taint
   lowered).

7. **Audited.** Every declassification fires
   `DECLASSIFICATION_APPLIED` with input/output hashes, diff,
   before/after labels, structural_proof_kind, and rationale.

**YAML DSL form (simple cases):**

```yaml
declassifiers:
  - name: redact_ssn
    type: regex-redact
    pattern: '\b\d{3}-\d{2}-\d{4}\b'
    replacement: '[SSN-REDACTED]'
    if_clean:
      lower_axis_a:
        category: pii
        from_tier: restricted
        to_tier: sensitive
    structural_proof_kind: regex-redacted
    rationale: "SSNs are redacted; remaining text is sensitive but not PII-restricted."

  - name: self_recipient_relabel
    type: recipient-context
    when:
      tool: email.send
      target_in: ["marc@joneslaw.io", "me@personal.example"]
    transform: identity   # don't change the data
    lower_axis_a:
      category: confidential.personal
      to_category: personal.self
    structural_proof_kind: recipient-context
    rationale: "Self-correspondence isn't third-party egress."
```

**Python module form (richer logic):**

```python
# configs/declassifiers/financial_summary.py
from capabledeputy.policy.declassifier import (
    DeclassifyingTransformer, DeclassifyResult,
)
from capabledeputy.quarantined.schemas import FinancialSummary
from pydantic import ValidationError

class FinancialSummaryValidator(DeclassifyingTransformer):
    name = "financial-summary-validator"
    rationale = (
        "If output conforms to FinancialSummary schema, sensitivity "
        "is bounded by the schema's field types and lengths."
    )

    def declassify(self, *, value, current_axis_a, current_axis_b, context):
        try:
            FinancialSummary.model_validate(value)
        except (ValidationError, TypeError):
            return None  # not applicable

        new_axis_a = lower_category(
            current_axis_a,
            from_category="finance",
            to_category="finance.summary",
            to_tier=Tier.SENSITIVE,
        )
        return DeclassifyResult(
            transformed_value=value,  # unchanged; schema is the proof
            new_axis_a=new_axis_a,
            new_axis_b=current_axis_b,
            audit_diff="schema-validated: matches FinancialSummary",
            structural_proof_kind="schema-validated",
        )
```

### 4.4 Per-arg Payload Labels

**Purpose:** distinguish "command parameters" (small, structured)
from "data payloads" (large, possibly sensitive) on a tool. Lets the
chokepoint gate on the LABELS of payload args independently of the
session's label set.

**Mechanism:** the tool declaration includes
`payload_args: ["body", "content", ...]`. At `decide()`, the engine
inspects the LABELS of each payload-arg value (not just the session's
labels) and applies Brewer-Nash rules to them.

**Per-tool YAML config (overrides T012):**

```yaml
tool_overrides:
  email.send:
    payload_args: ["body"]
    hard_refuse_on_payload_label:
      - secrets
      - confidential.financial.restricted

  api.post:
    payload_args: ["body"]
    hard_refuse_on_payload_label:
      - secrets
```

**`io.joneslaw/capabilitydeputy/payload_args`:** when set as an MCP
tool annotation by an operator-curated server, applied as the
declared payload args.

### 4.5 Per-server Policy Modules

**Purpose:** for popular MCP servers (Notion, GitHub, Slack, Gmail),
encapsulate richer mapping + labeling logic than YAML alone can
express.

**Port:**

```python
class ServerPolicy(Protocol):
    server_id: str

    def map_tool(self, mcp_tool: dict) -> ToolMapping | None: ...

    def label_propagation(
        self, mcp_tool: dict, args: dict, result_content: list,
    ) -> frozenset[Label]: ...

    def map_resource(self, mcp_resource: dict) -> ResourceMapping | None: ...

    def map_prompt(self, mcp_prompt: dict) -> PromptMapping | None: ...
```

**Resolution order at adapter registration:**
1. Per-server policy module (first match wins)
2. Operator YAML overrides
3. `io.joneslaw/capabilitydeputy/*` annotations
4. Heuristic inference (last resort)

Example: `configs/upstream_policies/notion.py`.

---

## 5. The Hook System

Hooks are named data-flow boundaries where operator-authored
primitives run. Each hook declares which primitive types can register,
what context the primitive sees, and what it can return.

The hook list is part of the daemon's public API. New hooks are
added as use cases emerge.

### 5.1 Ingress hooks

Run when data flows INTO a session. Eligible primitives:
RaiseOnlyInspector + (for some) DeclassifyingTransformer.

| Hook | Fires when | Inspector | Declassifier |
|---|---|---|---|
| `on_tool_result` | After any tool handler returns | ✓ | ✓ (with schema proof) |
| `on_resource_read` | After `resources/read` returns | ✓ | ✗ (untrusted-source) |
| `on_inbox_read` | After `inbox.read` returns | ✓ | ✗ |
| `on_fs_read` | After `fs.read`/`fs.read_pdf` returns | ✓ | ✓ (operator-curated source) |
| `on_web_fetch` | After `web.fetch` returns | ✓ | ✗ |
| `on_memory_read` | After memory store read | ✓ | ✗ |
| `on_mcp_incoming` | Generic catch-all for any MCP incoming flow | ✓ | ✗ |
| `on_prompt_get` | After `prompts/get` returns server-supplied messages | ✓ | ✓ (with schema or recipient context) |
| `on_sampling_message_received` | Server provides message to the sampling LLM | ✓ | ✓ |

### 5.2 LLM-boundary hooks

Run when data crosses the LLM boundary. Eligible primitives: both.

| Hook | Fires when | Inspector | Declassifier |
|---|---|---|---|
| `pre_llm_call.orchestrator` | Before orchestrator LLM call | ✓ | ✓ |
| `post_llm_call.orchestrator` | After orchestrator response | ✓ | ✓ (with schema validation) |
| `pre_llm_call.quarantined` | Before quarantined LLM call | ✓ | ✓ |
| `post_llm_call.quarantined` | After quarantined response | ✓ | ✓ |

### 5.3 Policy-boundary hooks

Run around the policy chokepoint.

| Hook | Fires when | Inspector | DecisionInspector | Declassifier |
|---|---|---|---|---|
| `pre_chokepoint` | Action prepared, before `decide()` | ✓ | — | ✓ |
| `at_chokepoint.decision` | Inside `decide()` after standard policy | — | ✓ | — |
| `pre_dispatch` | After decide approves, before handler | — | — | ✓ |
| `pre_approval_queue` | Before action enters approval queue | ✓ | — | ✓ (for the operator-view preview) |
| `on_approval_decided` | After operator decides in queue | — | — | — (audit only) |
| `pre_override_request` | Before override request processed | — | ✓ | — |
| `on_override_granted` | After override grant becomes active | — | — | — (audit only) |

### 5.4 Egress hooks

Run before data leaves the system. Eligible primitives:
DecisionInspector + DeclassifyingTransformer.

| Hook | Fires when | DecisionInspector | Declassifier |
|---|---|---|---|
| `pre_email_send` | Before email dispatch | ✓ | ✓ |
| `pre_purchase` | Before purchase queue | ✓ | ✓ |
| `pre_mcp_outgoing` | Generic before any MCP tool call to a server | ✓ | ✓ |
| `pre_fs_write` | Before fs.create/fs.modify | ✓ | ✓ |
| `pre_calendar_write` | Before calendar.create_event/update_event | ✓ | ✓ |
| `pre_sampling_response` | Before sampling output returns to server | — | ✓ (schema-validated) |

### 5.5 Session-lifecycle hooks

| Hook | Fires when | Inspector | Declassifier |
|---|---|---|---|
| `on_session_spawn` | New top-level session | ✓ | — |
| `on_session_fork` | Child session created from parent | ✓ | ✓ (parent labels → child labels) |
| `on_session_pause` | Session paused | — | — (audit only) |
| `on_session_terminal` | Session ending | — | ✓ (residual state → saved summary) |
| `on_label_propagation` | Whenever labels flow into a session | ✓ | — |

### 5.6 Bundle-lifecycle hooks

| Hook | Fires when | DecisionInspector | Declassifier |
|---|---|---|---|
| `on_bundle_dry_run_complete` | After dry-run, before operator review | ✓ | ✓ (for preview redaction) |
| `pre_bundle_execute` | After operator approves | — | — (audit only) |
| `on_bundle_gate_apply` | Per gate during execution | — | — (audit only) |

### 5.7 Storage hooks

| Hook | Fires when | Inspector | Declassifier |
|---|---|---|---|
| `on_memory_write` | Memory store write | ✓ (re-classify based on content) | ✓ |
| `on_audit_emit` | Before audit event written | — | ✓ (redact sensitive fields from audit payload) |

### 5.8 Per-hook eligibility matrix

A compact summary:

| Primitive | Can run at |
|---|---|
| `RaiseOnlyInspector` | All ingress hooks; `pre_llm_call.*`, `post_llm_call.*`; `pre_chokepoint`; `pre_approval_queue`; `on_session_spawn`/`on_session_fork`; `on_label_propagation`; `on_bundle_dry_run_complete`; `on_memory_write` |
| `DecisionInspector` | `at_chokepoint.decision` (primary); `pre_override_request`; egress hooks (`pre_*_send`, `pre_*_write`); `on_bundle_dry_run_complete` |
| `DeclassifyingTransformer` | Operator-curated ingress only (`on_fs_read`, `on_tool_result`, `on_prompt_get`, `on_sampling_message_received`); `pre_llm_call.*`/`post_llm_call.*`; `pre_chokepoint`; `pre_dispatch`; egress hooks; `pre_sampling_response`; `on_session_fork`; `on_session_terminal`; `on_memory_write`; `on_audit_emit`; `pre_approval_queue`; `on_bundle_dry_run_complete` |

### 5.9 Composition and ordering

**Within a single hook, multiple primitives may register.** Order
matters and is operator-declared.

- **Inspectors compose monotonically** — order doesn't change the
  final raise-only result. Run them all.
- **Declassifiers compose by chaining** — A's output is B's input.
  Order in config = execution order.
- **DecisionInspectors compose monotonically with envelope** — they
  propose relaxations; most-restrictive wins.

**Composition with hard floors:**

The final outcome of any decision is:

```
compose = most_restrictive_of(
  envelope_strictest_for_cell,
  standard_chokepoint_outcome,
  decision_inspector_outcome,
  declassification_floor_constraint,
)
```

Hard floors are immovable. Inspectors and declassifiers can only
operate WITHIN operator-declared envelopes.

---

## 6. Audit

Every primitive run produces audit events. The operator's audit
dashboard surfaces:

| Event type | Emitted by | Payload |
|---|---|---|
| `LABEL_RAISED_BY_INSPECTOR` | RaiseOnlyInspector | `{inspector_name, hook, before_axes, after_axes, session_id}` |
| `DECISION_RELAXED` | DecisionInspector | `{inspector_name, from_outcome, to_outcome, rule, rationale, action_descriptor}` |
| `DECISION_TIGHTENED` | DecisionInspector | `{inspector_name, from_outcome, to_outcome, rule, rationale, action_descriptor}` |
| `DECLASSIFICATION_APPLIED` | DeclassifyingTransformer | `{declassifier_name, structural_proof_kind, input_hash, output_hash, diff_summary, axes_before, axes_after, hook, rationale}` |
| `DECLASSIFICATION_REFUSED_BY_FLOOR` | engine | `{declassifier_name, attempted_to_lower_to, floor_constraint, hook}` |
| `HOOK_FIRED` | engine | `{hook, primitives_run, primitives_applied}` |

Every event includes session_id, timestamp, and (when relevant)
action descriptor. The audit log is append-only.

---

## 7. Trust Tiers

Per-server config declaring how much of the server's self-description
to honor.

| Tier | Annotation trust | Heuristic | Per-tool override |
|---|---|---|---|
| `unvetted` | Ignored | Sole signal; strict; unmapped tools refused | Required for any non-trivial tool |
| **`operator-curated`** (default) | Honored when consistent with heuristic | Backup signal; `io.joneslaw/capabilitydeputy/*` honored | Optional per-tool refinement |
| `vendor-vetted` | Authoritative | Sanity check (warn on disagreement) | Rare |

**Disagreement handling:**
- Severe (heuristic says READ_FS but server says `destructiveHint=true`)
  → refuse registration; emit `MCP_HEURISTIC_DISAGREEMENT_REFUSED`
- Mild (heuristic looser than annotation): honor the heuristic; emit
  `MCP_ANNOTATION_OVERRIDDEN` warning

---

## 8. Declassification Floors

Operator-declared minimum tiers per category that no declassifier
can go below.

```yaml
declassification_floors:
  pii: sensitive             # PII never goes below sensitive after redaction
  clinical: regulated        # PHI never below regulated
  financial.payment: restricted  # Payment data operator-locked
  secrets: prohibited        # Secrets never declassified at all
```

A declassifier that returns a `DeclassifyResult` with labels below
the floor is silently capped to the floor; emits
`DECLASSIFICATION_REFUSED_BY_FLOOR` audit event.

---

## 9. Shadow Mode

Any inspector / decision inspector / declassifier can run in shadow
mode: the primitive executes and produces audit events, but its
output is NOT applied. Operator reviews shadow audit to verify the
primitive works as expected, then enables.

```yaml
declassifiers:
  - name: new_redactor
    module: configs/declassifiers/new_redactor.py:NewRedactor
    shadow: true   # run + audit, but don't apply
```

Shadow mode is the recommended onboarding pattern for any new
primitive. Run for a week; review the audit; then flip to active.

---

## 10. Worked Examples

### Example 1: Self-recipient email auto-approval

**Scenario:** the operator wants to email themselves an HR document.
Without policy, the reversibility gate would force REQUIRE_APPROVAL.

**Configuration:**

```yaml
# configs/decision_inspectors/self_email.yaml
decision_rules:
  - name: self-email-auto-approve
    relax:
      from: require_approval
      to: allow
    when:
      tool: email.send
      target_in: ["marc@joneslaw.io", "marc.t.jones@gmail.com"]
      only_blocker: reversibility-irreversible
    rationale: "Self-correspondence; not third-party egress."
```

**Result:** email to self goes through without prompting; audit
event `DECISION_RELAXED` records the rationale.

### Example 2: PHI redaction before LLM call

**Scenario:** operator runs a clinical-summary workflow. Source has
patient identifiers; the orchestrator LLM should see redacted text.

**Configuration:**

```yaml
declassifiers:
  pre_llm_call.orchestrator:
    - module: configs/declassifiers/phi_redactor.py:PHIRedactor
      applies_when:
        session_axis_a_includes: ["clinical"]
      rationale: "Strip patient identifiers before orchestrator LLM call."

declassification_floors:
  clinical: sensitive   # never below sensitive even after redaction
```

**`configs/declassifiers/phi_redactor.py`:**

```python
import re
from capabledeputy.policy.declassifier import (
    DeclassifyingTransformer, DeclassifyResult,
)
from capabledeputy.policy.labels import AxisA, AxisACategory
from capabledeputy.policy.tiers import Tier

class PHIRedactor(DeclassifyingTransformer):
    name = "phi-redactor"
    rationale = "MRN and DOB patterns redacted; rest is summary-tier."

    _MRN = re.compile(r'\bMRN[-\s]?\d{6,}\b')
    _DOB = re.compile(r'\bDOB:\s?\d{1,2}/\d{1,2}/\d{2,4}\b')

    def declassify(self, *, value, current_axis_a, current_axis_b, context):
        text = str(value)
        redacted = self._MRN.sub('[MRN]', text)
        redacted = self._DOB.sub('[DOB]', redacted)
        if redacted == text:
            return None  # nothing to redact
        new_axis_a = AxisA(categories=tuple(
            AxisACategory(category=c.category, tier=Tier.SENSITIVE)
            if c.category == "clinical" else c
            for c in current_axis_a.categories
        ))
        return DeclassifyResult(
            transformed_value=redacted,
            new_axis_a=new_axis_a,
            new_axis_b=current_axis_b,
            audit_diff=f"{len(self._MRN.findall(text))} MRN, "
                       f"{len(self._DOB.findall(text))} DOB redacted",
            structural_proof_kind="regex-redacted",
        )
```

**Result:** the orchestrator LLM call receives text with identifiers
replaced; the session retains awareness that the original was
clinical-tagged; the floor prevents going below sensitive.

### Example 3: Per-server inspector chain for Notion

**Configuration:**

```yaml
upstream_servers:
  - id: notion-mcp
    trust_tier: operator-curated
    bindings:
      - uri_pattern: "notion://workspace/work/**"
        category: work
        tier: sensitive
      - uri_pattern: "notion://workspace/finance/**"
        category: finance
        tier: restricted

    incoming_inspectors:
      - configs/inspectors/ssn_detector.py:SSNDetector
      - configs/inspectors/cc_detector.py:CreditCardDetector
      - dsl:
          name: detect_api_keys
          pattern: '(?i)(api[_-]?key|secret|token)["\']?\s*[:=]\s*["\']?[a-z0-9]{20,}'
          on_match:
            raise_axis_a:
              - {category: secrets, tier: restricted}

    risk_preference: balanced
```

**Result:** every read from Notion routes through the three
inspectors; SSNs, credit cards, and API keys in the content raise
the session labels.

---

## 11. Configuration File Shapes (Reference)

Directory layout:

```
configs/
├── capabilities.yaml          # Session capabilities
├── bindings.yaml              # SourceLocationLabelBindings
├── envelopes.yaml             # Outcome envelopes + risk_preference
├── override_policies.yaml     # Override policies per hard floor
├── profiles.yaml              # ContextProfiles
├── relationship_groups.yaml   # Brewer-Nash compartments
├── conflict_rules.yaml        # Custom conflict rules (extends defaults)
├── declassification_floors.yaml
├── upstream_servers/          # Per-server MCP config
│   ├── notion-mcp.yaml
│   ├── github-mcp.yaml
│   └── ...
├── upstream_policies/         # Per-server Python policy modules
│   ├── notion.py
│   └── github.py
├── inspectors/                # Operator-authored RaiseOnlyInspector modules
│   ├── ssn_detector.py
│   └── phi_detector.py
├── decision_inspectors/       # Operator-authored DecisionInspectors
│   ├── self_egress.yaml
│   ├── self_egress.py
│   └── ...
├── declassifiers/             # Operator-authored DeclassifyingTransformers
│   ├── phi_redactor.py
│   └── ...
└── hooks.yaml                 # Per-hook primitive registration
```

`hooks.yaml` is the top-level orchestrator:

```yaml
# configs/hooks.yaml
hooks:
  on_tool_result:
    - configs/inspectors/ssn_detector.py:SSNDetector

  pre_llm_call.orchestrator:
    - configs/declassifiers/phi_redactor.py:PHIRedactor

  at_chokepoint.decision:
    - configs/decision_inspectors/self_egress.py:SelfEgressAutoApprove

  pre_chokepoint:
    - configs/declassifiers/recipient_relabel.py:RecipientRelabel

  pre_email_send:
    - configs/declassifiers/email_ssn_redact.py:EmailSSNRedact
```

---

## 12. Glossary of Annotations

The full `io.joneslaw/capabilitydeputy/*` annotation namespace, by
target object:

### On MCP tools

| Annotation | Type | Effect |
|---|---|---|
| `effect_class` | str | Sets ToolDefinition.effect_class |
| `default_reversibility` | `{degree, agent}` | Sets default_reversibility |
| `social_commitment` | bool | Sets social_commitment |
| `category_hint` | str | Suggested compartment category |
| `tier_hint` | str | Suggested tier |
| `flow_pattern_preferred` | str | `pattern_1`/`pattern_2`/`pattern_3`/`pattern_4`/`pattern_5` |
| `handle_arg_names` | `[str]` | Args that should be ReferenceHandle-bound |
| `payload_args` | `[str]` | Args carrying data payloads vs. command params |
| `batch_kind` | str | Group identifier for batch approval |
| `idempotent` | bool | Tool is retry-safe |
| `operator_ratifiable` | bool | Tool can be saved as a ritual |

### On MCP resources

| Annotation | Effect |
|---|---|
| `category_hint` | Compartment category |
| `tier_hint` | Sensitivity tier |
| `reversibility_hint` | Reversibility class |
| `pattern_2_schema` | Quarantined-extract schema for declassification |
| `retention_hint_seconds` | Suggested cache TTL |

### On MCP prompts

| Annotation | Effect |
|---|---|
| `flow_pattern_preferred` | Same enum as tools |
| `operator_ratifiable` | Saveable as ritual |
| `embeds_untrusted_content` | If true, taint session on forward |
| `safe_to_forward` | If true, auto-forward to LLM without operator click |

### On MCP elicitation (when enabled)

| Annotation | Effect |
|---|---|
| `bundleable_with` | List of other elicitation names to group |
| `operator_prefill` | Config-path to read default value from |

---

## Related documents

- `mcp-protocol-fit.md` — security audit + decisions per MCP surface
- `mcp-policy-integration.md` — design rationale for the policy
  integration positions (incoming-labeling pipeline, outgoing
  payload-args, OAuth flow-pattern-session, etc.)
- `tasks.md` (when written) — implementation breakdown ordered by
  priority and dependency
