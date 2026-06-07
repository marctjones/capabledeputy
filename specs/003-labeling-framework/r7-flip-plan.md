# R7 Flip Plan: Delete the Flat Label Enum

**Status**: Planning doc only — no source/test edits. Authoritative spec for future R7 "delete the flat Label enum" atomic branch.

**Scope**: The four-axis `LabelState` model is already AUTHORITATIVE, ENFORCING in production (checkpoints R4c/R5/R6, all green+tagged). The flat `Label` enum (policy/labels.py:31–43) now powers only a redundant, proven-equivalent duplicate. R7 deletes it across ~15 src subsystems + ~40 test files (~335 `Label.X` usages total).

**No behavior change**: The four-axis engine leg (`_conflict_invariant_outcome` + `add_tags`) already enforces the exact same outcomes the flat leg did. R7 is pure mechanical deletion.

---

## 0. Invariants & Ground Rules

### No behavior change
The four-axis enforcement leg (engine._conflict_invariant_outcome + R5's add_tags wiring) already enforces equivalently. R7 removes the redundant duplicate — tests pass both before and after, with identical decision outcomes.

### Value map (flat Label → four-axis)
The forward map `_LABEL_TO_TAGS` (policy/labels.py:314–341) is the **only** source of truth for what each flat enum member meant:

```
Label.CONFIDENTIAL_HEALTH      → LabelState(a={CategoryTag("health",REGULATED)})
Label.CONFIDENTIAL_FINANCIAL   → LabelState(a={CategoryTag("financial",REGULATED)})
Label.CONFIDENTIAL_PERSONAL    → LabelState(a={CategoryTag("personal",REGULATED)})
Label.UNTRUSTED_EXTERNAL       → LabelState(b={ProvenanceTag(EXTERNAL_UNTRUSTED)})
Label.UNTRUSTED_USER_INPUT     → LabelState(b={ProvenanceTag(EXTERNAL_UNTRUSTED)})
Label.TRUSTED_USER_DIRECT      → LabelState(b={ProvenanceTag(PRINCIPAL_DIRECT)})
Label.EGRESS_EMAIL             → LabelState()  # Axis-C effect, not propagating
Label.EGRESS_PURCHASE          → LabelState()  # Axis-C effect, not propagating
```

This map is deleted in §6, after all call sites are re-typed.

### Atomic flip
Do **not** commit intermediate states where the flat enum is partially-deleted or the label_set param is partially-removed. The flip must be **one atomic branch**: flat-present → flat-deleted, verified green once, then committed. Sub-commits are OK *within* that atomic branch (e.g., engine-leg deletion, then tool plumbing, then test migration) as long as the final tree has zero `Label` references.

---

## 1. decide() Signature Change — Highest Ripple

### Current signature
```python
def _decide_impl(
    label_set: frozenset[Label],                    # ← TO DELETE
    capabilities: frozenset[Capability],
    action: Action,
    rules: tuple[ConflictRule, ...] = CONFLICT_RULES,  # ← TO DELETE
    used_kinds: frozenset[CapabilityKind] = frozenset(),
    now: datetime | None = None,
    cap_uses: dict[str, tuple[datetime, ...]] | None = None,
    *,
    axis_a: AxisA | None = None,
    axis_b: AxisB | None = None,
    ...
    labels: LabelState | None = None,              # ← ALREADY PROVIDED (R4b.2)
) -> PolicyDecision:
```

### New signature
Delete the positional `label_set` and `rules` params entirely. The four-axis enforcement lives in the kw-only zone via `labels: LabelState | None`:

```python
def _decide_impl(
    capabilities: frozenset[Capability],
    action: Action,
    used_kinds: frozenset[CapabilityKind] = frozenset(),
    now: datetime | None = None,
    cap_uses: dict[str, tuple[datetime, ...]] | None = None,
    *,
    axis_a: AxisA | None = None,
    axis_b: AxisB | None = None,
    ...
    labels: LabelState | None = None,
) -> PolicyDecision:
```

### Per-caller edits

All **5 direct src callers** + **~40 test files** that invoke `decide()` or `_decide_impl()` must move `label_set` from position 0 to the new `labels=` kwarg (and use `tags_for_labels(label_set)` if still accepting flat input, which tests do).

| File | Signature call | Change |
|------|---|---|
| **src/capabledeputy/tools/client.py:317** | `decide(session.label_set, session.capability_set, action, ...)` | `decide(session.capability_set, action, ..., labels=session.label_state)` (or compose from `axis_a/axis_b` if not yet unified) |
| **src/capabledeputy/daemon/tool_handlers.py** | (search for decide call) | Same reorder |
| **src/capabledeputy/daemon/policy_handlers.py:result = decide(labels, caps, action, ...)** | Already uses `labels` var — check if it's flat or four-axis; if flat, use `tags_for_labels(labels)` |
| **src/capabledeputy/policy/multi_tenant_engine.py:decide(label_set, per_tenant, ...)** | Reorder + flatten from per-tenant flat set |
| **src/capabledeputy/tools/native/policy_preview.py** | (if it calls decide) | Reorder |
| **tests/** (~40 files, ~335 usages) | `decide(label_set={Label.X}, caps, action)` | See §7 for uniform transform |

### Deprecation path (if needed)
If intermediate testing requires back-compat, provide a **temporary shim**:
```python
def decide(*args, **kwargs):
    # Shim: if positional args[0] is frozenset[Label], convert to labels= kwarg
    if args and isinstance(args[0], frozenset) and all(isinstance(x, Label) for x in args[0]):
        labels_arg = tags_for_labels(args[0])
        args = args[1:]  # drop label_set
        kwargs['labels'] = labels_arg
    return _decide_impl(*args, **kwargs)
```
**Delete this shim once all tests are migrated** (do not commit with shim in place).

---

## 2. engine.py & rules.py — Decision-Leg Deletion

### engine.py deletions

**Delete from engine.py**:

1. **Line 50–53: `_EGRESS_LABEL_FOR_KIND` dict** — maps `CapabilityKind.SEND_EMAIL` → `Label.EGRESS_EMAIL`. No longer needed; egress is detected by the action kind alone in `_conflict_invariant_outcome`.

2. **Line 243–249: `egress_label_for(kind)` function** — returns `Label | None` for an action kind. Called nowhere after flat leg deletion.

3. **Lines 558–606 in `_decide_legacy` function** — the entire flat conflict-rule firing loop:
   ```python
   effective_labels = label_set
   if egress_label := egress_label_for(action.kind):
       effective_labels = effective_labels | {egress_label}
   for rule in rules:
       if rule.fires(effective_labels):
           return PolicyDecision(
               decision=rule.decision,
               rule=rule.name,
               reason=...,
               effective_labels=effective_labels,
           )
   ```
   **Replace with**: (nothing — the four-axis `_conflict_invariant_outcome` gate already runs.)

4. **Lines 413–423 function signature of `_decide_legacy`** — remove `label_set` (pos 0), `rules` (default param).

5. **Line 161: `PolicyDecision.effective_labels` field** — `effective_labels: frozenset[Label] = frozenset()` is set in ~15 places (463, 506, 511, 538, 555, 558, 573, 600, 606, 863, 883, 942, ...). After deletion, every `PolicyDecision(...)` call in engine.py **drops the `effective_labels=` kwarg**. The field itself is deleted from the dataclass.

   **Before**:
   ```python
   return PolicyDecision(
       decision=Decision.DENY,
       rule=CAPABILITY_EXPIRED_RULE,
       reason=...,
       matched_capability=expired_match,
       effective_labels=label_set,  # ← DELETE THIS LINE
   )
   ```

   **After**:
   ```python
   return PolicyDecision(
       decision=Decision.DENY,
       rule=CAPABILITY_EXPIRED_RULE,
       reason=...,
       matched_capability=expired_match,
   )
   ```

6. **Line 41: `from capabledeputy.policy.labels import ... Label ...`** — remove `Label` from the import.

7. **Line 48: `from capabledeputy.policy.rules import CONFLICT_RULES, ConflictRule, Decision`** — remove `CONFLICT_RULES, ConflictRule` from the import.

**Keep in engine.py**:
- The four-axis `_conflict_invariant_outcome` (lines 252–308) — this becomes the *only* conflict gate and is already proven equivalent to the flat rules.
- The `_compose_with_conflict_invariant` wrapper (lines 311–324).
- Every other gate (reversibility, envelope, override, etc.).

### rules.py deletions

**Delete from rules.py**:

1. **Lines 17–18: `from capabledeputy.policy.labels import Label` import.**

2. **Lines 32–40: `ConflictRule` dataclass** — the flat conflict-rule type (name, triggers, conflicts, decision, fires() method).

3. **Lines 43–68: `CONFLICT_RULES` tuple** — the four global rules (untrusted-meets-egress, health-meets-egress, financial-meets-email, financial-meets-purchase).

**Keep in rules.py**:
- **Lines 20–29: `Decision` enum** — `ALLOW, DENY, REQUIRE_APPROVAL, OVERRIDE_REQUIRED`. This is still used by decision_rules.py and engine.py.

---

## 3. Tool Taint-Return Plumbing — Re-type to Four-Axis

### registry.py (ToolContext / ToolResult / ToolDefinition)

**ToolContext (lines 28–30)**:
```python
# Before
@dataclass(frozen=True)
class ToolContext:
    session_id: UUID
    label_set: frozenset[Label]         # ← DELETE THIS

# After
@dataclass(frozen=True)
class ToolContext:
    session_id: UUID
    label_state: LabelState              # ← ADD THIS
```

Audit: grep for `ToolContext(` to find all construction sites (likely tools/client.py).

**ToolResult (lines 34–36)**:
```python
# Before
@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any]
    additional_labels: frozenset[Label] = field(default_factory=frozenset)  # ← RENAME

# After
@dataclass(frozen=True)
class ToolResult:
    output: dict[str, Any]
    additional_tags: LabelState = field(default_factory=LabelState)  # ← RENAMED
```

All native tool **handlers that return** `ToolResult(output=..., additional_labels={...})` must change to `additional_tags=LabelState(...)`.

**ToolDefinition (lines 43–97)**:
```python
# Before (line 53)
inherent_labels: frozenset[Label] = field(default_factory=frozenset)

# After (replace with)
inherent_tags: LabelState = field(default_factory=LabelState)

# Also before (lines 96–97)
arg_inherent_labels: dict[str, frozenset[Label]] = field(default_factory=dict)

# After (replace with)
arg_inherent_tags: dict[str, LabelState] = field(default_factory=dict)
```

All tool registrations that pass `inherent_labels={Label.X, ...}` must change to `inherent_tags=LabelState(...)`.

Remove the line `from capabledeputy.policy.labels import Label, LabelState` (line 24); add back `LabelState` only if not already present.

### tools/client.py — Propagation rewrite

**Lines ~189–210 (label propagation after tool execution)**:

```python
# Before
if tool_result.additional_labels:
    labels_to_add = (
        tool.inherent_labels | tool_result.additional_labels
    )
    await self._session_graph.add_labels(
        session_id,
        labels_to_add,
    )

# After
tags_to_add = most_restrictive_inherit(
    tool.inherent_tags,
    tool_result.additional_tags,
)
if tags_to_add.a or tags_to_add.b:
    await self._session_graph.add_tags(
        session_id,
        tags_to_add,
    )
```

**Lines 317–346 (decide call)**:

```python
# Before
policy_decision = decide(
    session.label_set,
    session.capability_set,
    action,
    used_kinds=...,
    ...,
)

# After
policy_decision = decide(
    session.capability_set,
    action,
    used_kinds=...,
    labels=session.label_state,  # ← OR compose from axis_a/b if not yet unified
    ...,
)
```

**ToolContext construction (line ~309)**:

```python
# Before
ToolContext(session_id=session_id, label_set=session.label_set)

# After
ToolContext(session_id=session_id, label_state=session.label_state)
```

Import `most_restrictive_inherit` from `capabledeputy.policy.labels` at the top.

### Native tool handlers — ~6 tool files

These files return `ToolResult` with `additional_labels=...`:

| File | Pattern | Change |
|------|---------|--------|
| **src/capabledeputy/tools/native/fs.py** | `return ToolResult(..., additional_labels={Label.X})` | `additional_tags=LabelState(...)` |
| **src/capabledeputy/tools/native/inbox.py** | Same | Same |
| **src/capabledeputy/tools/native/web.py** | Same | Same |
| **src/capabledeputy/tools/native/tasks.py** | Same | Same |
| **src/capabledeputy/tools/native/memory.py** | Same | Same |
| **src/capabledeputy/tools/native/resources.py** | Same | Same |

Grep: `grep -n "additional_labels" src/capabledeputy/tools/native/*.py` to find exact lines.

Each replacement follows the value map from §0:
```python
# Before
additional_labels=frozenset({Label.CONFIDENTIAL_PERSONAL})

# After
additional_tags=LabelState(
    a=frozenset({CategoryTag("personal", Tier.REGULATED)})
)
```

### Declassifier logic (substrate/declassifier_port.py + policy/\*)

The declassifier's `_apply_declassifiers` function currently has:
```python
candidate: frozenset[Label] = ...
if (lower_axis_a_categories := {...}) and candidate & {Label.CONFIDENTIAL_PERSONAL}:
    # remove
```

**After R7**, re-type `candidate` to `LabelState` and replace string `.value` matching with direct `CategoryTag.category` comparison:

```python
candidate: LabelState = ...
if candidate.a and any(tag.category == "personal" for tag in candidate.a):
    # remove via _remove(candidate, LabelState(a=frozenset({PersonalTag})))
```

Full rewrite scope: grep for `Label.CONFIDENTIAL_` and `Label.UNTRUSTED_` matching in substrate-facing code.

### policy/capabilities.py — kind_add_labels

If `kind_add_labels` is exposed (decorator or registry mechanism that adds labels based on kind), change:

```python
# Before
def kind_add_labels(kind: CapabilityKind) -> frozenset[Label]:
    return {Label.TRUSTED_USER_DIRECT}

# After
def kind_add_tags(kind: CapabilityKind) -> LabelState:
    return LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)}))
```

---

## 4. Session Plumbing — Delete label_set Field

### session/model.py

**Line 16: `from capabledeputy.policy.labels import AxisA, AxisB, AxisD, Label, LabelState`**
Remove `Label` from the import.

**Lines 87–93 (DeclassEvent)** — this dataclass still carries `from_labels/to_labels: frozenset[Label]`. Currently it's used for audit trail of flat declassifications. 

**Options**:
- **Option A** (minimal): leave `DeclassEvent` as-is; it's audit-only and backward-compatible to have legacy flat events in the log.
- **Option B** (clean): re-type to `LabelState` and update `to_dict()/from_dict()` to serialize the four-axis model.

Recommend **Option A** for first R7 pass (audit trails are immutable; no harm in keeping them flat). The `DeclassEvent` itself will be superseded by a future `TagRemovalEvent` when the structured declassifier plumbing (substrate/declassifier_port.py) is wired.

**Session dataclass**: Search for `label_set` field. Remove it entirely:

```python
# Before
@dataclass(frozen=True)
class Session:
    ...
    label_set: frozenset[Label] = frozenset()  # ← DELETE THIS LINE
    ...
```

### session/model.py — Session.new() method

Remove `label_set` from the `new()` class method signature and body.

### session/model.py — Session.to_dict() / from_dict()

Remove `label_set` from serialization:

```python
# Before
def to_dict(self) -> dict[str, Any]:
    return {
        ...
        "label_set": sorted(label.value for label in self.label_set),  # ← DELETE THIS
        ...
    }

# After (just remove that block)
```

Similarly, remove from `from_dict()`.

### session/store.py — Database schema

**SCHEMA_VERSION is already v7** (R6 committed this). The `label_set` column was already dropped in the v7 schema definition. **No action needed** — the column does not exist in the current schema.

If any `_SCHEMA_SQL` or `_row_to_session` still references `label_set`, remove those lines.

Check:
```bash
grep -n "label_set" src/capabledeputy/session/store.py
```

If 0 results, schema is clean. If found, delete those lines.

### session/graph.py — delete add_labels()

**Lines ~432–441 (the `add_labels` async method)**:

```python
# DELETE THIS ENTIRE METHOD
async def add_labels(
    self,
    session_id: UUID,
    labels: frozenset[Label],
) -> None:
    ...
    new_set = session.label_set | labels
    ...
```

**All callers** must switch to the existing `add_tags(session_id, tags: LabelState)` method (already exists in R5).

**Remove the import**: `from capabledeputy.policy.labels import ... Label ...` (line at the top).

---

## 5. Design-Sensitive Rewrites — Call Out as NEEDS-JUDGMENT

### mode/dispatcher.select_mode(label_set)

**File**: `src/capabledeputy/mode/dispatcher.py` (if exists).

**Before**: `select_mode(label_set: frozenset[Label]) -> Mode`

**After**: `select_mode(label_state: LabelState) -> Mode`

**Logic**: Instead of checking for flat `Label.UNTRUSTED_*` membership, check for the presence of `ProvenanceTag(EXTERNAL_UNTRUSTED)` in `label_state.b` or specific categories in `label_state.a`.

**Example**:
```python
# Before
def select_mode(label_set):
    if Label.UNTRUSTED_EXTERNAL in label_set:
        return Mode.CAREFUL
    return Mode.NORMAL

# After
def select_mode(label_state):
    if any(tag.level == ProvenanceLevel.EXTERNAL_UNTRUSTED for tag in label_state.b):
        return Mode.CAREFUL
    return Mode.NORMAL
```

### agent/context._likely_outcome_for_tool

**File**: `src/capabledeputy/agent/context.py` (if exists).

This function uses flat-conflict heuristics to predict what `decide()` will return **before actually calling it** (for agent planning/lookahead).

**Before**: Mirrors the flat `ConflictRule.fires()` logic to check `label_set & rule.conflicts`.

**After**: Mirrors the four-axis `_conflict_invariant_outcome` logic.

**Example rewrite**:
```python
# Before
def _likely_outcome_for_tool(self, tool_name, label_set):
    for rule in CONFLICT_RULES:
        if rule.fires(label_set):
            return rule.decision

# After
def _likely_outcome_for_tool(self, tool_name, label_state, action):
    outcome = _conflict_invariant_outcome(
        axis_a=AxisA(categories=tuple(label_state.a)),
        axis_b=AxisB(entries=tuple(label_state.b)),
        action=action,
    )
    if outcome:
        return outcome[0]
    return Decision.ALLOW
```

### policy/tenancy.py — TenantLabel + multi_tenant_engine.py

**Search for usage**: `grep -n "TenantLabel\|decide_multi_tenant" src/capabledeputy/policy/tenancy.py src/capabledeputy/policy/multi_tenant_engine.py`.

**Pattern**: If `TenantLabel(label: Label)` exists and `decide_multi_tenant(per_tenant_labels)` threads them through:

```python
# Before
def decide_multi_tenant(
    per_tenant: dict[str, frozenset[Label]],
    ...
) -> dict[str, PolicyDecision]:
    for tenant, label_set in per_tenant.items():
        per_tenant[tenant] = decide(
            label_set,
            capabilities,
            action,
            ...
        )
```

**After**: Wrap `LabelState` instead:

```python
def decide_multi_tenant(
    per_tenant: dict[str, LabelState],
    ...
) -> dict[str, PolicyDecision]:
    for tenant, label_state in per_tenant.items():
        per_tenant[tenant] = decide(
            capabilities,
            action,
            labels=label_state,
            ...
        )
```

**If TenantLabel is ONLY used in tests**: verify there are **zero src callers** of `decide_multi_tenant` or `TenantLabel` in production code. If so, safe to delete both (but mark in commit message as test-only). If there are src callers, update them per above pattern.

### approval/model.py — labels_in/out

**Search**: `grep -n "labels_in\|labels_out" src/capabledeputy/approval/model.py`.

**Pattern**: If approval model carries flat label snapshots:

```python
# Before
@dataclass(frozen=True)
class ApprovalRequest:
    labels_in: frozenset[Label]
    labels_out: frozenset[Label] | None

# After
@dataclass(frozen=True)
class ApprovalRequest:
    labels_in: LabelState
    labels_out: LabelState | None
```

Update `to_dict()/from_dict()` to serialize/deserialize via `LabelState.to_axis_a()` etc.

### programmatic/value.LabeledValue + labels_of + union_labels

**Search**: `grep -n "class LabeledValue\|labels_of\|union_labels" src/capabledeputy/programmatic/`.

**Pattern**: If values carry flat label metadata:

```python
# Before
@dataclass
class LabeledValue:
    value: Any
    labels: frozenset[Label]

def labels_of(value) -> frozenset[Label]: ...
def union_labels(*values) -> frozenset[Label]: ...

# After
@dataclass
class LabeledValue:
    value: Any
    labels: LabelState

def labels_of(value) -> LabelState: ...
def union_labels(*values) -> LabelState: ...
```

Update threading in `programmatic/runner.py` to call `add_tags` instead of `add_labels`.

### resources/static.Resource.labels + yaml load

**Search**: `grep -n "class Resource\|\.labels" src/capabledeputy/resources/static.py`.

**Pattern**: If static resources declare labels in YAML:

```yaml
# Before
resources:
  database:
    labels:
      - confidential.personal
      - trusted.user_direct

# After (one of two options)
# Option A: declare inherent_tags natively
inherent_tags:
  a:
    - category: personal
      tier: regulated
  b:
    - level: principal-direct

# Option B: keep flat in YAML, convert at load time via tags_for_labels()
```

Recommend **Option B** for first pass (YAML is external config; conversion at load time is non-breaking). Mark as future improvement to migrate configs to native tags.

**Code change**:
```python
# Before
class Resource:
    labels: frozenset[Label]

def _load_from_yaml(spec):
    return Resource(labels=frozenset(Label(s) for s in spec["labels"]))

# After
def _load_from_yaml(spec):
    flat_labels = frozenset(Label(s) for s in spec.get("labels", []))
    return Resource(labels=tags_for_labels(flat_labels))
```

### daemon/approval_handlers.py + programmatic/bundle_runner.py — purpose-session seeding

**Search**: `grep -n "label_set.*TRUSTED_USER_DIRECT\|Session(label_set" src/capabledeputy/daemon/approval_handlers.py src/capabledeputy/programmatic/bundle_runner.py`.

**Pattern**: Creating sessions for approval/bundle execution with a specific default label set:

```python
# Before
session = Session(
    ...,
    label_set=frozenset({Label.TRUSTED_USER_DIRECT}),
)

# After
session = Session(
    ...,
    label_state=LabelState(
        b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)})
    ),
)
```

### upstream/config.py + upstream/adapter.py + upstream/server_yaml.py + skills/parser.py

**Search**: `grep -n "inherent_labels" src/capabledeputy/upstream/`.

**Pattern**: MCP server / upstream tool definitions that declare labels:

```python
# Before
inherent_labels=frozenset({Label.TRUSTED_USER_DIRECT})

# After
inherent_tags=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT)}))
```

If these are loaded from YAML/JSON, provide a conversion function:

```python
def _resolve_inherent_tags(spec):
    if "inherent_labels" in spec:  # backward compat for R6 YAML
        flat = frozenset(Label(s) for s in spec["inherent_labels"])
        return tags_for_labels(flat)
    elif "inherent_tags" in spec:
        return LabelState.from_dict(spec["inherent_tags"])
    return LabelState()
```

### demo/scenarios.py — scenario seeding

**Search**: `grep -n "label_set\|add_labels" src/capabledeputy/demo/scenarios.py`.

**Pattern**: Demo fixtures that seed sessions with labels:

```python
# Before
await graph.add_labels(session_id, frozenset({Label.CONFIDENTIAL_HEALTH}))

# After
await graph.add_tags(
    session_id,
    LabelState(a=frozenset({CategoryTag("health", Tier.REGULATED)}))
)
```

---

## 6. Final Deletion + Grep-Gate

### policy/labels.py deletions

1. **Lines 31–43: `Label` enum** — the entire flat `class Label(StrEnum): ...` block.

2. **Lines 314–341: `_LABEL_TO_TAGS` dict** — the forward map.

3. **Lines 344–355: `tags_for_label(label)` and `tags_for_labels(labels)` functions**.

4. **Lines 147–155 (optional): `ProvenanceTag.integrity_floor` field** — marked as transitional in comment (line 149–152). Can be kept for backward-compat audits or deleted if R6 already moved it to Operation `required_floor`. **Recommend deletion** if R6 completed the move.

### Grep-gate (zero-tolerance)

Run these commands and verify 0 results:

```bash
# In src/ (not tests/)
grep -r "frozenset\[Label\]" src/
grep -r "from.*import.*Label" src/
grep -r "Label\." src/

# In tests/ (should find only the migration — localized to test construction)
# After migration, these should also be 0:
grep -r "Label\." tests/
```

If any remain outside test files, the flip is incomplete — do not commit.

---

## 7. Test Migration (~40 files, ~335 usages)

### Scope identification

```bash
grep -l "decide(" tests/*.py | sort
```

Expect ~15–20 files. Each file likely has 3–20 `decide()` calls + additional `add_labels()` calls and `Session(..., label_set=...)` constructions.

### Uniform transforms

**Transform 1: decide() call signature**

```python
# Before
result = decide(
    frozenset({Label.CONFIDENTIAL_HEALTH}),
    capabilities,
    action,
)

# After
result = decide(
    capabilities,
    action,
    labels=LabelState(a=frozenset({CategoryTag("health", Tier.REGULATED)}))
)
```

**Transform 2: Session construction**

```python
# Before
session = Session(
    id=uuid4(),
    label_set=frozenset({Label.UNTRUSTED_EXTERNAL}),
    ...
)

# After
session = Session(
    id=uuid4(),
    label_state=LabelState(b=frozenset({ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)})),
    ...
)
```

**Transform 3: graph.add_labels() call**

```python
# Before
await graph.add_labels(session_id, frozenset({Label.CONFIDENTIAL_PERSONAL}))

# After
await graph.add_tags(
    session_id,
    LabelState(a=frozenset({CategoryTag("personal", Tier.REGULATED)}))
)
```

**Transform 4: Conflict-decision assertions**

**Keep as-is** — these still pass because the four-axis `_conflict_invariant_outcome` gate is proven equivalent:

```python
# Before (this works)
result = decide(
    frozenset({Label.UNTRUSTED_EXTERNAL, Label.EGRESS_EMAIL}),
    capabilities,
    action,
)
assert result.decision == Decision.DENY  # flat leg fires

# After (same test, re-typed input)
result = decide(
    capabilities,
    action,
    labels=tags_for_labels(frozenset({Label.UNTRUSTED_EXTERNAL, Label.EGRESS_EMAIL})),
)
assert result.decision == Decision.DENY  # four-axis leg fires (same outcome)
```

The rule ids are preserved (R4c gate ported them), so audit assertions still match.

### Suggested migration strategy: parallel batches

Split the ~40 test files into **disjoint cohorts of 6–8 files each**. Each cohort:
1. All files in cohort use the same value-map translations (§0).
2. A single commit per cohort (or grouped).
3. Run tests after each cohort to catch regressions early.

**Example cohort breakdown**:
- **Batch A** (6 files): `test_cascade_*.py`, `test_delegation_*.py`, `test_audit_*.py` — core engine tests.
- **Batch B** (6 files): `test_approval_*.py`, `test_override_*.py`, `test_grant_*.py` — approval/override tests.
- **Batch C** (6 files): `test_composition_*.py`, `test_reversibility_*.py` — decision composition tests.
- **Batch D** (6 files): `test_first_use_*.py`, `test_rate_limit_*.py`, `test_integration_*.py` — integration tests.
- **Batch E** (8 files): remaining `test_*.py` files.

Each batch is independently committable and test-verifiable.

### Helper function for common transforms

To avoid duplication, add a test utility in `tests/conftest.py` or a new `tests/helpers.py`:

```python
def make_label_state(**kwargs) -> LabelState:
    """Construct a LabelState from shorthand keywords.
    
    Examples:
        make_label_state(health=True, untrusted=True)
        → LabelState(
            a={CategoryTag("health", Tier.REGULATED)},
            b={ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED)}
        )
    """
    a_tags = set()
    b_tags = set()
    
    if kwargs.get("health"):
        a_tags.add(CategoryTag("health", Tier.REGULATED))
    if kwargs.get("financial"):
        a_tags.add(CategoryTag("financial", Tier.REGULATED))
    if kwargs.get("personal"):
        a_tags.add(CategoryTag("personal", Tier.REGULATED))
    if kwargs.get("untrusted"):
        b_tags.add(ProvenanceTag(ProvenanceLevel.EXTERNAL_UNTRUSTED))
    if kwargs.get("trusted"):
        b_tags.add(ProvenanceTag(ProvenanceLevel.PRINCIPAL_DIRECT))
    
    return LabelState(
        a=frozenset(a_tags),
        b=frozenset(b_tags),
    )
```

Then tests become more readable:

```python
result = decide(
    capabilities,
    action,
    labels=make_label_state(health=True),
)
```

---

## 8. Suggested Commit Sequence

**All within one atomic branch** (do not merge to main between commits). The final state must have **zero `Label` references** in src/.

### Sub-commit order:

**Commit A: engine.py & rules.py decision-leg deletion**
- Delete `_EGRESS_LABEL_FOR_KIND`, `egress_label_for()`.
- Delete the flat conflict-rule firing loop from `_decide_legacy`.
- Delete `ConflictRule` + `CONFLICT_RULES` from rules.py.
- Remove `label_set` param from `_decide_impl` + `_decide_legacy` + `decide` (provide temporary shim if needed).
- Update all docstrings; confirm `_conflict_invariant_outcome` is the only conflict gate.
- **Test gate**: existing conflict tests (test_composition_*.py) still pass; R4c-proven equivalence holds.

**Commit B: tool/session plumbing re-type**
- ToolContext/ToolResult/ToolDefinition: `label_set`→`label_state`, `additional_labels`→`additional_tags`, `inherent_labels`→`inherent_tags`.
- tools/client.py: propagation rewrite + decide() call signature.
- Native tool handlers (fs.py, inbox.py, etc.): additional_labels→additional_tags.
- Session.label_set field deletion + model.py serialization cleanup.
- session/graph.py: delete `add_labels()` method.
- **Test gate**: `test_tools_registry.py`, `test_session_store.py` pass; no regressions in client plumbing tests.

**Commit C: design-sensitive re-types**
- mode/dispatcher, agent/context, approval/model, programmatic/\*, resources/static, daemon/approval_handlers, upstream/\*, demo/scenarios.
- Each rewrite is localized to one file or subsystem; commit one subsystem per sub-commit if large.
- **Test gate**: integration tests (test_agent_loop.py, test_daemon.py) pass.

**Commit D: policy/labels.py enum + map deletion**
- Delete `Label` enum, `_LABEL_TO_TAGS`, `tags_for_label(s)`, optional `integrity_floor`.
- **Grep gate**: verify zero `Label` in src/ (allow test usage only).

**Commit E: test migration (parallel batches)**
- Batches A–E each as a separate commit (or group related ones).
- After each batch commit, run `pytest tests/` to verify green.
- Final grep gate after all batches: zero `Label` and `frozenset[Label]` across the tree.

---

## 9. Green-Bar Baseline

**Pre-existing flake**: `test_run_status_stop_lifecycle` has an env-dependent timeout (subprocess TimeoutError). This is **unrelated to labels** (appears in R6 baseline too). The suite is green (2076 passed) with this one flake.

**Expect R7 to be green with same flake** (the four-axis leg proves equivalence). If a *new* red appears, it indicates an incomplete migration (e.g., a `decide()` caller not re-typed).

---

## Checklist (for executor)

- [ ] Read this spec in full before starting.
- [ ] Start a new atomic branch: `git checkout -b r7-delete-flat-label`.
- [ ] Commit A: engine/rules leg deletion (test conflicts still pass).
- [ ] Commit B: tool/session plumbing (integration tests pass).
- [ ] Commit C: design-sensitive re-types (no new test failures).
- [ ] Commit D: enum + map deletion (grep-gate 0 in src/).
- [ ] Commit E: test migration in parallel batches (final grep-gate 0 everywhere).
- [ ] Run full test suite: `pytest tests/ -x` → all green (except pre-existing flake).
- [ ] Final verification: `grep -r "Label\." src/ tests/` → 0 results.
- [ ] Merge atomic branch to main, tag `v0.15.0-R7-delete-flat-label`, continue to release.

---

## Notes for Future Executor

1. **Value map is the source of truth**: Every `Label.X` reference in production code was semantically equivalent to one `LabelState(...)` from the _LABEL_TO_TAGS map. Use §0 as your reference throughout.

2. **Conflict gate is already proven**: The four-axis `_conflict_invariant_outcome` and R4c gate produce identical outcomes to the flat rules. **Do not second-guess this**; the proof is in the test suite (run-both-assert tests in R4c checkpoint).

3. **No behavior change**: Every assertion in existing tests stays valid. If a test fails during migration, it means a call site was incompletely re-typed, not that the model changed.

4. **Audit trail backward-compat (optional)**: Existing flat DeclassEvent entries in the audit log can stay flat; new entries are four-axis. This is intentional (immutable history). If you find yourself trying to convert historical audit entries, stop — they're read-only.

5. **YAML/JSON config backward-compat**: External YAML (upstream tools, resources, scenarios) can keep the flat label format during R7 and be migrated to native `inherent_tags` in a later pass. This is not a blocker for R7 completion.

6. **Shim-free**: Do not commit a `decide()` shim for backward-compat. All call sites must be migrated atomically in one branch.

---

## Appendix: File Inventory by Subsystem

### Core decision engine (3 files)
- policy/engine.py — decide/_decide_impl/_decide_legacy, _EGRESS_LABEL_FOR_KIND, CONFLICT_RULES firing, effective_labels field
- policy/rules.py — ConflictRule, CONFLICT_RULES
- policy/labels.py — Label enum, _LABEL_TO_TAGS, tags_for_label(s)

### Tool plumbing (4 files)
- tools/registry.py — ToolContext.label_set → label_state, ToolResult.additional_labels → additional_tags, ToolDefinition.inherent_labels → inherent_tags
- tools/client.py — decide() call, label propagation, ToolContext construction
- tools/native/{fs,inbox,web,tasks,memory,resources}.py — return ToolResult(..., additional_labels=...)

### Session state (3 files)
- session/model.py — Session.label_set field, serialization
- session/store.py — label_set column (already deleted in v7)
- session/graph.py — add_labels() method

### Decision composition (4 files)
- mode/dispatcher.py — select_mode(label_set) (if exists)
- agent/context.py — _likely_outcome_for_tool (if exists)
- policy/tenancy.py — TenantLabel (if used in src)
- policy/multi_tenant_engine.py — decide_multi_tenant (if used in src)

### Approval & structured values (4 files)
- approval/model.py — labels_in/out fields
- programmatic/value.py — LabeledValue.labels, labels_of(), union_labels()
- programmatic/runner.py — label threading in session
- programmatic/bundle_runner.py — purpose-session seeding

### Resources & upstream (5 files)
- resources/static.py — Resource.labels, yaml load
- upstream/config.py — inherent_labels in tool definitions
- upstream/adapter.py — label handling
- upstream/server_yaml.py — label conversion
- skills/parser.py — skill inherent_labels

### Demo & declassifier (3 files)
- demo/scenarios.py — scenario label seeding
- substrate/declassifier_port.py — candidate.value matching
- policy/*.py (declassifier integration points)

### Tests (~40 files, parallel migration)
- test_cascade_*.py — cascade & revocation tests
- test_composition_*.py — decision composition tests
- test_approval_*.py — approval flow tests
- test_override_*.py — override grant tests
- test_delegation_*.py — delegation tests
- test_first_use_*.py — first-use prompt tests
- test_rate_limit_*.py — rate limit tests
- test_audit_*.py — audit event tests
- test_integration_*.py — end-to-end tests
- ... (25 more)

---

This spec is **final and comprehensive**. Execute it as written — no deviations, no partial deletes.
