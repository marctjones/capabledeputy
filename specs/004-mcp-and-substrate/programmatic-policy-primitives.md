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

## 13. Related Work — what exists and where CapableDeputy fits

The question naturally arises: aren't there established policy
languages we should be using? Yes — and we use them where they fit.
This section maps the landscape, explains where CapableDeputy
overlaps with existing standards (and integrates with them), and
where it deliberately occupies new ground (information flow for
agents) because no existing standard addresses that layer.

**Source basis:** the assessments below cite primary-source reading
of the Cedar paper, the OPA/Rego documentation, NIST OSCAL technical
materials, and language specifications (Lua, Starlark, WebAssembly,
eBPF). Where a claim says "no support for X," that's drawn from the
language's own design documents — not inferred.

### 13.1 Authorization policy languages

These answer the question: **"is this principal allowed to do this
action on this resource under these conditions?"** They are well-
suited for cap-style permit/deny decisions on individual actions.

| Language | Steward | Style | Strengths | Limits for CapableDeputy |
|---|---|---|---|---|
| **Rego** (Open Policy Agent) | CNCF | Datalog-derived | Most mature; broad ecosystem; flexible | Steep learning curve; benchmarks show error-prone runtime exceptions; not designed for stateful info flow |
| **Cedar** | AWS / Apache | Functional, typed, formally modeled in Lean | 42-60× faster than Rego; formal verification; deny-by-default; readable | Explicitly stateless; PAR-model (principal/action/resource); extensions limited to evaluator level |
| **OpenFGA** | CNCF | Relation-based (Google Zanzibar-inspired) | Excellent for "is X a member of Y's team?" relationships | Less general; not designed for cross-action information flow |
| **XACML** | OASIS | XML-based, ABAC | Architectural concepts (PEP/PDP/PAP/PIP) widely influential | Verbose; declining adoption; same auth-not-info-flow limitation |

**Primary-source citations** for the limits column:

- **Cedar paper (arXiv 2403.04651):** the paper describes Cedar as
  explicitly stateless: *"No accumulation of historical decisions
  or facts across requests. Each policy evaluation is isolated. No
  built-in support for information flow tracking or label
  propagation. The language makes no provision for taint analysis,
  data lineage, or label-based confidentiality tracking."*
  Extensions are "validator-defined" at the evaluator level — the
  core language is closed.

- **OPA/Rego documentation:** *"Rego has no mutable state. Policies
  cannot perform side effects or maintain state across invocations.
  Each evaluation is stateless and deterministic."* Information
  flow expressible "but cannot automatically track information flow
  or lazily evaluate side effects triggered by data access" — must
  be modeled via explicit rule composition against external state.

**What CapableDeputy could do with these:**

Our **cap match + Brewer-Nash + BLP** layer is the part that overlaps
with what these languages do well. An operator who already knows
Rego or Cedar could (optionally, in a future spec) author rules in
those languages, and CapableDeputy would compile them into the
internal evaluator. That's an interop play, not a replacement —
because the information-flow layer (label propagation,
declassification with structural proof, programmatic primitives at
named hooks) can't be expressed in their models without a parallel
state machine outside the language.

**The industry is starting to see this gap.** From the 2026 Permit.io
analysis of policy languages for MCP and agentic systems:

> *"Enforcing policies for agentic systems requires tracking
> information flow across agents, which linear message histories
> cannot capture, suggesting this is an emerging consideration in
> policy language design."*

CapableDeputy is one of the systems making information flow
first-class in this emerging space.

### 13.2 Compliance documentation languages

These answer a different question: **"can a tool/auditor
mechanically verify that we implement specific security controls?"**

| Language | Steward | Purpose | Status |
|---|---|---|---|
| **OSCAL** (Open Security Controls Assessment Language) | NIST | Machine-readable expression of control catalogs, baselines, system security plans, assessment plans, assessment results | Active; supersedes OpenControl in most contexts; growing adoption |
| **OpenControl** | community | Earlier YAML-based control documentation | Largely dormant; OSCAL has the momentum |

**OSCAL is not policy enforcement.** You don't gate an action with
OSCAL; you describe in OSCAL HOW you gate that action so auditors
can verify your implementation maps to NIST 800-53 / FedRAMP / SOC 2
/ ISO 27001 controls.

**What CapableDeputy will do with OSCAL:**

For operators who need to demonstrate compliance (regulated
industries, public-sector, enterprise customers), CapableDeputy will
emit OSCAL-shaped documents:

- **Control implementation evidence** — for example, "we implement
  AC-3 Access Enforcement via the capability + chokepoint
  mechanism," pointing at audit events as evidence.
- **Continuous assessment results** — streaming audit events as
  OSCAL assessment results suitable for ingestion into compliance
  dashboards.
- **System security plan (SSP)** — describe our architecture in
  OSCAL SSP format.

This means CapableDeputy plugs into the broader compliance ecosystem
without our policy engine needing to change. Auditors get the
artifacts they need in the format they expect.

Roadmap: see `tasks.md` Phase P2 (`U200`-`U206`).

### 13.3 Information-flow research lineage

This is where CapableDeputy's actual technical lineage lives. These
systems implement information-flow primitives at various layers:

| System | Layer | Concepts inherited |
|---|---|---|
| **Asbestos, HiStar, Flume** (MIT/Stanford, 2000s) | OS-level DIFC | Labels first-class; taint propagates; declassification requires explicit authority; read-up/read-down semantics |
| **Jif** (Cornell) | Programming language | Static information-flow types; explicit declassification primitives |
| **Bell-LaPadula** (foundational, 1973) | Theoretical | "no read-up, no write-down"; the model we use for clearance enforcement |
| **Brewer-Nash** (foundational, 1989) | Theoretical | Conflict-of-interest compartments; what we use for category/category rules |
| **Clark-Wilson** (1987) | Theoretical | Well-formed transactions; separation of duty — what FR-036 dual-control implements |
| **Denning** (1976) | Theoretical | Lattice model of information flow; foundational |
| **CaMeL** (Google DeepMind, 2025) | LLM-agent flow | Dual-LLM (planner + quarantined extractor) with schema-bound declassification — direct inspiration for our Pattern ② |
| **Dromedary** (Microsoft Research) | LLM-agent flow | Flow patterns for agent safety; influences our pattern taxonomy |

None of these is a "policy language" you can directly adopt for an
agent runtime — they're either OS-kernel systems (Asbestos, HiStar)
or language type systems (Jif) or theoretical frameworks (BLP,
Brewer-Nash, Clark-Wilson, Denning). What we've done is take their
foundational concepts and operationalize them in a configurable
policy language at the agent runtime layer.

### 13.4 Positioning

Combining the surveys above, the honest claim:

> **CapableDeputy implements information-flow control for AI
> agents — a problem domain that mainstream authorization
> languages (Rego/OPA, Cedar, OpenFGA, XACML) do not address.**
>
> The authorization-layer subset of our policy (capability
> matching, Brewer-Nash conflict rules, BLP clearance enforcement)
> overlaps with what those languages do well, and we can interop
> with them as an alternative syntax. The information-flow layer
> (label propagation, declassification with structural proof,
> programmatic primitives at named hooks) is sui generis at the
> agent runtime layer; the closest analogs are research DIFC
> systems and the dual-LLM patterns from CaMeL / Dromedary.
>
> For compliance documentation, CapableDeputy emits OSCAL-shaped
> artifacts compatible with NIST 800-53, FedRAMP, SOC 2, and
> ISO 27001 frameworks.

This is a defensible position. We're not duplicating existing
standards — we're filling a gap, with clear interop where
standards apply.

### 13.5 What this means for an operator

- **You DO NOT need to know Rego or Cedar to use CapableDeputy.**
  The native policy language (capabilities, bindings, envelopes,
  override policies, profiles, the three programmatic primitives)
  is the primary surface.
- **If you DO know Rego or Cedar**, future work will let you
  author the cap + conflict-rule subset in those languages as an
  alternative syntax. This is P4 (optional interop), not core.
- **If you need to produce compliance evidence** for a regulated
  context, the OSCAL emission feature (P2 roadmap) maps
  CapableDeputy's mechanisms to NIST 800-53 / FedRAMP / SOC 2 /
  ISO 27001 controls and produces machine-readable assessment
  results from the audit log.
- **If you're evaluating CapableDeputy academically or for
  research**, the information-flow primitives are the novel
  contribution. The closest prior work is CaMeL (Google DeepMind,
  2025) and the DIFC OS research from the 2000s; the difference
  is that we operationalize these for an agent runtime as
  configurable policy, not as a one-off OS or compiler.

---

## 14. Runtime hosts for policy authoring

The three programmatic primitives (`RaiseOnlyInspector`,
`DecisionInspector`, `DeclassifyingTransformer`) need a **language
and execution environment** in which operators author them. The
current design says YAML DSL or Python module; this section
surveys the broader space and recommends a layered approach with
Starlark added between the two.

### 14.1 Requirements for the host

- **Determinism** — same inputs produce same outputs (required for
  audit replay, regression testing)
- **Bounded execution** — primitives run inside `decide()`; can't
  hang the chokepoint
- **No ambient I/O** — primitives are pure functions; the operator
  declares which external state, if any, is available
- **Resource limits** — CPU + memory caps per call
- **Authorship clarity** — operator-readable, version-controllable,
  testable
- **Performance** — microsecond-scale per call (the chokepoint
  fires on every action)

### 14.2 Candidates surveyed

| Host | Origin | Fit for CapDep |
|---|---|---|
| **YAML DSL (our own)** | n/a | ✓ Common cases — declarative rules, pre-compiled |
| **Starlark** | Bazel (Google) | ✓ Sweet spot — Python-like, hermetic, no while/recursion/classes, deterministic, parallelizable |
| **Lua** | embedded language standard | ✓ Production-grade — sandboxable, battle-tested (Roblox, Redis, nginx, WoW), but operator learning curve |
| **WebAssembly (Wasmtime)** | W3C / Bytecode Alliance | ✓ For community-shared modules — multi-language source, capability-based imports, but per-call overhead ms not µs |
| **Python module** | Python | ✓ For rich logic; operator trust at config time |
| **TinyScheme** | GIMP/Apple sandbox heritage | △ Battle-tested in sandboxing (Apple MacOSX sandbox config uses it!), but operator learning curve |
| **Red (modern REBOL)** | Nenad Rakočević | △ Research direction — alpha status, but dialecting genuinely interesting for DSLs |
| **eBPF-style verified bytecode** | Linux kernel | △ Strongest verification, but building our own verifier is months of engineering |
| **RestrictedPython** | Zope | ✗ Per its own docs: "not a sandbox system or a secured environment" |

### 14.3 Primary-source notes on the leading candidates

**Starlark** (Bazel's restricted-Python config language):

From Bazel's documentation: function declaration prohibited in BUILD
files; top-level `for`/`if` disallowed (only `if` expressions); no
`while` loops, no `class` definitions, no recursion; no `*args` /
`**kwargs`; integers limited to 32-bit signed; no `float` or `set`
types; strings aren't iterable. Two mutable types (lists, dicts).
"Each `.bzl` file and `BUILD` file operates within isolated contexts,
preventing unintended state sharing across parallel execution
environments." Hermetic execution is a primary design goal.

For CapDep policy use: this is the **right fit between YAML and
full Python**. The "removed Python features" are exactly the
features we don't want operators to use in policy (loops that
might not terminate, recursion that might overflow, classes with
mutable state that defeats determinism).

Reference implementations: `starlark-go`, `starlark-rust`, and
Python via `bazel-starlark-py`.

**Lua** (general-purpose embedded scripting):

From the official Lua materials: ~388K binary, simple C API, "fast"
benchmarks. Embedded in Roblox, Redis, nginx, World of Warcraft,
Adobe Lightroom, and many other production systems. Sandboxing
patterns are well-established (restrict global env; remove `io`,
`os`, `debug.dump`, `loadstring`; instruction counting via
`debug.sethook`). LuaJIT for hot-path performance (separate
implementation).

For CapDep policy use: viable production option. The trade-off
vs. Starlark is operator familiarity (Python developers know
Python syntax; Lua's syntax is different).

**WebAssembly** (Wasmtime / WASI):

Multi-language source: Rust, AssemblyScript, Go, C, Python (via
Pyodide), more. Wasmtime is "fast and secure," with "fine-grained
control over CPU and memory consumption." Imports are
capability-based: the host explicitly declares which functions
wasm modules can call. Per-call overhead is millisecond-scale (not
microsecond) due to instance setup, so wasm is best for **loaded
once at startup, called many times** patterns. Module size and
sandbox isolation are both strong.

For CapDep policy use: best fit for **community-shared policy
modules** — package once in any source language, distribute as
.wasm, run in any CapDep install with capability-bound imports.
Not the right tool for per-call in-chokepoint primitives where
microsecond performance matters.

**eBPF** (Linux kernel's verified bytecode):

The verifier checks: "Programs always run to completion (no
infinite loops). No uninitialized variables or out-of-bounds
memory access. Programs fit within size requirements and
complexity limits." Programs written in C or Rust, compiled to
eBPF bytecode via LLVM/Clang. "The verifier is meant as a safety
tool, checking that programs are safe to run. It is not a security
tool inspecting what the programs are doing."

For CapDep: the **model** is exactly what we'd want for verified
in-process policy execution — a verifier that proves termination
+ memory safety before allowing execution. But eBPF itself is
tightly kernel-coupled; building a userspace equivalent (or
extracting the verifier) is months of engineering. **WebAssembly
covers ~80% of the same value proposition with much less custom
work.**

**TinyScheme** (small Scheme implementation):

Used by GIMP and **Apple's MacOSX sandbox configuration**. That's
a strong production-sandboxing signal: Apple chose embedded Scheme
for their security boundary configuration. Small (compiles to
~50KB), simple semantics, easy to bound. Operator learning curve
is the trade-off — Scheme syntax is less familiar than Python-like.

**Red** (modern REBOL):

Started 2011 by Nenad Rakočević. Alpha status (32-bit only as of
2026); single-file ~1MB executable; native compilation;
homoiconic; supports **dialecting** (defining DSLs within Red
syntax). Active development; small but committed community.
"Highly embeddable."

For CapDep: dialecting is a natural fit for policy DSL design —
you could define a "CapDep policy dialect" that compiles via
Red's machinery. But alpha status + small community makes this a
research-direction candidate, not a production choice. Revisit
post-1.0.

**RestrictedPython**:

From the official RestrictedPython docs themselves:
*"RestrictedPython is not a sandbox system or a secured
environment. This is a crucial distinction—it's a policy
enforcement tool, not a true security boundary."*

For CapDep: **rejected**. The maintainers explicitly disclaim
sandboxing properties.

### 14.4 Recommended layered approach

Use the right tool at each tier of operator-authoring complexity:

| Tier | Host | When operator chooses this |
|---|---|---|
| **1** | **YAML DSL** | Common cases: regex inspector, simple decision rules with `when` / `relax` / `tighten`, recipient-list matching. ~80% of operator authoring. |
| **2** | **Starlark** | Rule-shaped logic beyond YAML — multi-clause matching with computed predicates, declarative decision tables, custom small functions. Hermetic + deterministic + bounded. |
| **3** | **Python module** | Rich logic — multi-line transformations, schema validation against Pydantic models, stateful inspectors. Operator-authored; trust at config time. |
| **4** | **WebAssembly module** | Community-shared policy modules — packaged once in any source language, distributed as `.wasm`, run with capability-bound imports. Future work. |

Rejected at any tier:
- **RestrictedPython** — per its own docs, not a sandbox
- **Custom eBPF-style verifier** — too much engineering for too
  little gain over wasm
- **Red** — alpha status; revisit later

Tier 2 (Starlark) is the addition from the language survey. Today's
design specifies Tier 1 and Tier 3; adding Starlark gives operators
a sandbox-friendly option for rule-shaped logic without committing
to full Python modules.

### 14.5 Implementation cost

Adding Starlark support:

| Item | Effort |
|---|---|
| Embed `starlark-go` (via FFI) OR write Python-side Starlark interpreter | 5 days |
| Wire Starlark hooks to the three primitive Protocols (Inspector, DecisionInspector, DeclassifyingTransformer) | 3 days |
| Per-call resource limits (instruction count, memory) | 2 days |
| Configuration loader for `*.star` files | 1 day |
| Test harness + fixtures | 2 days |
| Operator documentation | 2 days |
| **Total** | **~15 days** |

This becomes new Phase P3 task `U210-U215` in `tasks.md`.

Adding WebAssembly support (future):

| Item | Effort |
|---|---|
| Embed `wasmtime-py` | 3 days |
| Capability-based import surface | 4 days |
| WASI subset for policy use | 3 days |
| Module signing / verification | 3 days |
| Test harness | 3 days |
| Documentation | 2 days |
| **Total** | **~18 days** (P4 community-policy work) |

---

## 15. Architectural decision record — language host strategy

**Decision (2026-05-20):** **Option A + Starlark via starlark-rust + PyO3.**

CapDep's policy language is stateless (§14.4 discussion); the state
is in CapDep's data structures (Session, OverrideGrantStore,
ApprovalQueue, etc.) and the policy is invoked with snapshots. This
keeps the policy a pure function of inputs.

For authoring the primitives (RaiseOnlyInspector, DecisionInspector,
DeclassifyingTransformer), we adopt a four-tier strategy:

1. **YAML DSL** — declarative rules, regex inspectors, simple decision
   tables. Covers ~80% of operator authoring.
2. **Starlark** — rule-shaped logic beyond YAML; hermetic + bounded;
   embedded via `starlark-rust` crate + PyO3 bindings.
3. **Python module** — rich logic, schema validation, operator-trusted
   at config time. Existing design.
4. **WebAssembly** — future; for community-shared portable policy
   modules.

The **`engine.decide()` itself remains hand-coded Python** —
orchestrating the policy evaluation through the named hooks,
managing state transitions (label propagation, override FSM, bundle
lifecycle), and dispatching to primitives.

### 15.1 Why Starlark via starlark-rust + PyO3

Among the language hosts surveyed (§14.2), Starlark fits the
"safer, structured logic" tier best:

- **Determinism, bounded execution, hermetic** by language design
- **Python-familiar syntax** for our operator base
- **Multi-implementation portability** — same Starlark code runs on
  Go, Rust, and Python interpreters; not locked to one runtime
- **No operator-authored-code security surface** — Starlark sandbox
  is structural, not trust-based

For the embedding, **starlark-rust** is the production-grade Rust
implementation (Facebook battle-tested in Buck2); PyO3 gives us
mature Python bindings. The combination is:

- **Performance**: sub-millisecond per primitive call (target <200µs)
- **Build complexity**: adds Rust toolchain dependency, mitigable
  via published wheels
- **Maintenance**: starlark-rust is actively maintained; PyO3 is the
  standard Python-Rust bridge

Alternatives considered and rejected:

| Alternative | Why rejected |
|---|---|
| `starlark-go` via subprocess | Process spawn per evaluation = 10ms+ overhead |
| `starlark-go` via FFI | Requires Go toolchain; FFI overhead similar to PyO3 |
| Pure-Python Starlark interpreter | Slower; less mature; tracking the spec is operator burden |
| Custom in-Python implementation | Months of work; reinventing what starlark-rust already does |

### 15.3 OPA sidecar — P3 opt-in, default off

**Decision update (2026-05-20):** OPA sidecar integration **is part
of the P3 commitment**, not "deferred until demand." Cost ~7 days
given the hooks architecture; default-off in operator config.
Operators with existing OPA infrastructure can opt in; everyone
else ignores it. Implementation tasks `U230-U237` in `tasks.md`.

**Why ship it as opt-in instead of waiting:**

- The hooks architecture makes the adapter small (~7 days), not the
  weeks I initially estimated
- Composes cleanly with Starlark via the `opa_query()` host function
  pattern (operator writes a Starlark inspector that selectively
  consults OPA for higher-stakes decisions)
- Provides an enterprise on-ramp without forcing anyone
- No runtime dependency on OPA for operators who don't enable it
- Audit + compliance value for the operators who do enable it

**The Starlark+OPA composition pattern** is the recommended idiom:

```python
# configs/decision_inspectors/cisco_corporate.star

def inspect(action, session, proposed_outcome):
    # Fast path: routine reads never need to bother OPA
    if action["kind"] == "READ_FS" and proposed_outcome["decision"] == "allow":
        return None

    # Higher-stakes: consult Cisco's central policy
    opa_input = {
        "action": action,
        "session": {"labels": session["labels"]},
        "proposed": proposed_outcome,
    }
    opa_result = opa_query(
        package = "cisco.capdep.authorization",
        input = opa_input,
    )
    if opa_result.get("tighten"):
        return tighten(
            to = opa_result["tighten"]["to"],
            rule = opa_result["tighten"].get("rule", "opa-corporate-tighten"),
            rationale = opa_result["tighten"].get("rationale", ""),
        )
    if opa_result.get("relax"):
        return relax(
            to = opa_result["relax"]["to"],
            rule = opa_result["relax"].get("rule", "opa-corporate-relax"),
            rationale = opa_result["relax"].get("rationale", ""),
        )
    return None
```

Three wins from this pattern:

1. **Fast path in Starlark, slow path to OPA** — most decisions
   resolve locally; only the ones requiring corporate policy
   consultation make the OPA round trip
2. **Operator chooses the seam** — what goes to OPA vs. what
   resolves locally is operator-authored, not hard-coded
3. **Local + remote policy compose** — local Starlark can ADD
   strictness; central OPA can enforce the baseline; both run in
   the same hook with composable outputs

**Adapter primitives we'd implement** (see `tasks.md` U230-U237):

1. **Input serializer** — marshal action + session + capabilities +
   proposed_outcome to JSON
2. **OPA HTTP client** — async POST with timeout, fail-closed
3. **Output translator** — parse OPA response into
   `DecisionRelax | DecisionTighten | None`
4. **`OpaConsultingInspector`** — registers at
   `at_chokepoint.decision` hook
5. **Configuration surface** — operator YAML for endpoint, package,
   timeout, hooks
6. **Schema documentation** — `docs/opa-input-schema.md` so
   operators have a stable contract to write Rego against
7. **Test harness** — mock OPA + live OPA integration tests
8. **Operator examples** — Cisco-style baseline, regulated-data
   restrictions, time-window policies as ready-to-use templates

**What we DON'T get from OPA support:**

- OPA does NOT see CapDep actions on its own — we serialize and
  send them
- OPA does NOT enforce anything — CapDep is the enforcement point;
  OPA just returns yes/no/relax/tighten
- OPA does NOT modify CapDep state — label propagation, override
  FSM, etc. all stay in CapDep host code

So "OPA support" really means "we wrote an adapter that lets
operators delegate the decision-refinement step to OPA when they
want." The adapter is the work; OPA is the engine.

**Bundle export option** (cheaper alternative for audit-only use
cases) — still on the table:

| Option | What | Effort | When to do |
|---|---|---|---|
| **Bundle export** | Generate OPA bundle from our static rules; doesn't use OPA at runtime | ~1 week | Audit-only requirement |
| **Sidecar (this commit)** | Live OPA consultation via DecisionInspector | ~1 week | Operator wants runtime policy authoring in Rego |
| **Embedded Wasm** | Compile policies to Wasm; embed wasmtime | ~3 weeks | If we want zero external process; revisit if anyone asks |

We're committing to sidecar at P3. Bundle export and embedded Wasm
remain "if asked" follow-ups.

### 15.2 OPA reference — what it offers (background)

OPA (Open Policy Agent) is the dominant authorization policy engine
in the cloud-native ecosystem. CNCF graduated; deployed at Netflix,
Pinterest, Shopify, and most large engineering orgs running
Kubernetes. Uses Rego as its policy language.

**What OPA is, structurally:**

- A standalone Go daemon (or embedded library, or compiled to Wasm)
- Receives JSON input → returns JSON decision
- Pulls signed bundle from a central server (policy distribution)
- Streams decision logs to a SIEM (auditability)
- Three deployment modes:
  - **Sidecar** (separate process; HTTP loopback; ms-scale)
  - **Embedded library** (in-process; sub-ms)
  - **Wasm** (compiled policies, embedded in any host; µs-scale)

**How enterprises use it:**

1. **Kubernetes admission control** (Gatekeeper — gate pod creation
   against organizational policy)
2. **API gateway authorization** (Envoy/Istio external auth)
3. **Service-to-service mesh auth** (combined with mTLS identity)
4. **CI/CD policy gates** (Conftest — block Terraform plans with
   overly-permissive IAM, etc.)
5. **Database row-level security** (proxy + OPA rewriting queries)
6. **Cloud resource provisioning** (pre-deployment policy validation)
7. **Network device config policy** (network shops validate switch/
   router configs against organizational rules before push)

**How OPA could be used with CapDep:**

If an enterprise customer (e.g., a 5000-developer org standardizing
on OPA) required CapDep to integrate, three options:

| Option | Description | Effort |
|---|---|---|
| **OPA-A**: Bundle export | CapDep generates OPA bundles from its own static rules; doesn't use OPA at runtime. Audit teams verify policy with `opa test`. | ~1 week |
| **OPA-B**: Embedded Wasm | Operator authors rules in Rego; compiled to Wasm; CapDep embeds wasmtime and calls compiled policies in-process. ~µs eval. | ~3 weeks |
| **OPA-C**: OPA sidecar | CapDep daemon calls OPA over loopback HTTP. Full OPA ecosystem (bundles, decision logs, central management). ~1-5ms per call. | ~2 weeks |

**Concrete enterprise scenario:** Cisco rolls out CapDep to 5000
developers. Central security team writes baseline Rego policies for
"no internal-Cisco data egresses to non-Cisco email domains,"
distributes via signed bundle from `policies.cisco.com/capdep/`,
each developer's CapDep pulls the bundle every 60s, decision logs
stream to Cisco Splunk for audit. Compliance auditors run
`opa test` against the active bundle to verify properties — much
faster than reviewing 800 lines of Python.

**When to revisit OPA integration:**

- An enterprise customer specifically requires Rego authoring or
  OPA bundle distribution
- A regulator requires policy expressed in a standard external
  language for compliance assessment
- We want to publish CapDep's static rules as a shareable artifact
  for security teams to inspect/customize

Until one of those happens, OPA-A (bundle export) is the cheapest
entry point if asked. OPA-B is the technically cleanest if
adopted. OPA-C adds operational complexity that mostly benefits
enterprises with existing OPA infrastructure.

### 15.4 Implementation roadmap update

Committed:
- **P2 U200-U206** (~7d): OSCAL emission for compliance
- **P3 U210-U215** (~15d): Starlark host conceptual wiring
- **P3 U220-U228** (~12d): Starlark integration via starlark-rust
  + PyO3 (concrete steps)
- **P3 U230-U237** (~7d): OPA sidecar adapter (opt-in, default off)

**Committed P3 total: ~34 days for Starlark + OPA sidecar.**

Deferred / on-demand:
- OPA bundle export (audit-only) — ~1 week if asked
- OPA embedded-Wasm — ~3 weeks if needed (most operators won't)
- WebAssembly host for community policy modules (P4)
- Red language host (revisit post-Red-1.0)
- TinyScheme host (revisit if a vertical demands it)
- Custom eBPF-style verifier (probably never — Wasm covers it)
- Cedar integration (no operator demand path identified)

### 15.5 Open issues for the implementation phase

- **starlark-rust PyO3 binding maturity**: verify the bindings handle
  Starlark error propagation, value marshaling, and call latency
  acceptably before committing the build dependency.
- **Wheel distribution**: building a CapDep wheel that bundles
  starlark-rust requires Rust toolchain in CI; need to evaluate
  manylinux wheel feasibility.
- **Operator UX for "Starlark vs. Python"**: the per-primitive choice
  needs a clear default ("write your inspectors in Starlark unless
  you need a Python library").
- **Test harness fidelity**: Starlark primitives must replay
  deterministically; need a fixture format that captures input
  exactly.
- **OPA decision-log dedup with CapDep audit**: when OPA is enabled,
  OPA emits its own decision logs and CapDep emits audit events.
  Decide canonical record (probably CapDep audit; OPA logs as
  side stream); define a correlation ID linking them.
- **OPA timeout policy**: default 100ms fail-closed (treats as "no
  opinion"). Verify this is right; operator may need to tune per
  workload.
- **Starlark + OPA composition examples**: ship at least one
  end-to-end example showing the recommended pattern so operators
  can copy/adapt.

---

## Related documents

- `mcp-protocol-fit.md` — security audit + decisions per MCP surface
- `mcp-policy-integration.md` — design rationale for the policy
  integration positions (incoming-labeling pipeline, outgoing
  payload-args, OAuth flow-pattern-session, etc.)
- `tasks.md` — implementation breakdown ordered by priority and
  dependency (OSCAL emission tasks `U200-U206` in Phase P2;
  Starlark host `U210-U215` + `U220-U228` in P3; WebAssembly host
  and OPA integration deferred until demand)
