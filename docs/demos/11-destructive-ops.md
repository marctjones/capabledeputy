# Demo 11: Destructive-Op Gate (Read OK, Create OK, Modify/Delete Suspect)

**Audience:** anyone asking "is there a model where reading and
appending are low-friction but modifying or deleting needs more
care?" Yes — this is the **Clark-Wilson well-formed-transaction**
principle, mirrored in CRUD-decomposed RBAC, append-only ledgers,
git's commit graph, and `chattr +a` filesystems.
**Time:** ~3 minutes.
**Requires:** nothing beyond `uv sync`.

## What the demo proves

1. **Reads ALLOW unconditionally** when capability matches. No gate.
2. **Creates ALLOW unconditionally** (when tool is tagged `CREATE_FS` /
   `CREATE_CAL`). The new key/event isn't replacing anything; nothing
   destructive happens.
3. **Modifies REQUIRE_APPROVAL by default.** A capability of kind
   `MODIFY_FS` does not automatically authorize destructive use; the
   user has to either:
   - Mark the capability `allows_destructive=True` (compartment-wide
     pre-authorization), OR
   - Approve each modify per-action via the approval queue (or
     bundle, per Demo 10).
4. **Deletes REQUIRE_APPROVAL by default.** Same logic.
5. **Backward-compat:** legacy `WRITE_FS` capabilities still match the
   granular kinds. With `allows_destructive=True` they cover all
   create/modify/delete actions; without it, modify and delete still
   gate.
6. **Conflict rules short-circuit the gate.** A health-meets-egress
   DENY fires before the destructive-op rule is consulted; the user
   can't "approve away" a non-negotiable conflict.

## Walkthrough

```bash
uv run pytest tests/test_destructive_ops.py -v
```

11 tests pass.

### The granular capability kinds

```python
class CapabilityKind(StrEnum):
    READ_FS = "READ_FS"
    WRITE_FS = "WRITE_FS"        # backward-compat union
    CREATE_FS = "CREATE_FS"      # new, non-destructive
    MODIFY_FS = "MODIFY_FS"      # new, destructive (gated)
    DELETE_FS = "DELETE_FS"      # new, destructive (gated)
    CREATE_CAL / MODIFY_CAL / DELETE_CAL  # calendar parallels
    ...
```

A `WRITE_FS` capability matches `CREATE_FS` / `MODIFY_FS` /
`DELETE_FS` actions via the union semantics in `Capability.matches`.
This means existing code that grants `WRITE_FS` keeps working — the
only change is that **destructive uses now require either a
`allows_destructive=True` flag or an approval**.

### The destructive-op gate

In `policy/engine.py`:

```python
if action.kind in DESTRUCTIVE_KINDS and not cap.allows_destructive:
    return PolicyDecision(
        decision=Decision.REQUIRE_APPROVAL,
        rule="destructive-op-needs-approval",
        ...
    )
```

The check sits BELOW the conflict-rule loop, so a DENY rule
(health-meets-egress, untrusted-meets-egress, etc.) still short-
circuits before the destructive gate applies. Non-negotiable rules
remain non-negotiable.

### Granular tools that surface this

- `memory.create` — `CREATE_FS`. Fails if key exists. **No gate.**
- `memory.write` — `WRITE_FS` (legacy). Creates or overwrites.
- `memory.update` — `MODIFY_FS`. Fails if key missing. **Gated.**
- `memory.delete` — `DELETE_FS`. **Gated.**
- `calendar.create_event` — `CREATE_CAL`. **No gate.**
- `calendar.update_event` — `MODIFY_CAL`. **Gated.**
- `calendar.delete_event` — `DELETE_CAL`. **Gated.**

LLM agents pick the right tool by intent. The schema descriptions
(`"Non-destructive: bypasses the destructive-op gate"` vs
`"Destructive: requires approval unless..."`) make this discoverable
in tool listings.

### The minimum-friction workflow

```python
caps = {
    Capability(kind=CapabilityKind.READ_FS, pattern="*"),
    Capability(kind=CapabilityKind.CREATE_FS, pattern="notes.*"),
    # Note: NO MODIFY_FS or DELETE_FS without explicit user grant.
}
```

This session can:
- Read any key.
- Create new notes under `notes.*`.

It **cannot** modify existing notes or delete anything without an
approval gate. New work is frictionless; destructive work is
deliberate.

### The pre-authorized compartment

```python
caps = {
    Capability(kind=CapabilityKind.READ_FS, pattern="scratchpad.*"),
    Capability(
        kind=CapabilityKind.MODIFY_FS,
        pattern="scratchpad.*",
        allows_destructive=True,    # explicit pre-authorization
    ),
    Capability(
        kind=CapabilityKind.DELETE_FS,
        pattern="scratchpad.*",
        allows_destructive=True,
    ),
}
```

Within `scratchpad.*` modify and delete are pre-authorized — the
session can rewrite freely. Other patterns (e.g., `health.*`,
`finance.*`) keep the default gating. This is the "scratch
namespace" idiom: a compartment where you don't care about history,
plus the rest of memory where you do.

### Combining with bundled approvals (Demo 10)

When a workflow has multiple destructive ops, the bundle collector
groups them all into one impact tree. The user reviews a plan like:

```
Bundle 8a3f... (5 step(s)):
  ✓ [ 1] memory.read                       (no gate)
  ✓ [ 2] memory.create   key=notes.new     (no gate)
  ⚠ [ 3] memory.update   key=notes.x       rule=destructive-op-needs-approval
  ⚠ [ 4] memory.update   key=notes.y       rule=destructive-op-needs-approval
  ⚠ [ 5] memory.delete   key=notes.draft   rule=destructive-op-needs-approval

  3 approval gate(s) pending, 0 non-negotiable deny(s).
```

One approval clears all three destructive operations.
Read and create flowed through unchallenged.

## What this demonstrates

- **Reads and creates are cheap.** The agent can build new state
  freely. No friction for non-destructive work.
- **Modifications and deletions are expensive — by design.** The
  user reviews and authorizes destructive work, either per-call or
  in bundles, or pre-authorizes specific compartments via
  `allows_destructive=True`.
- **The model maps onto well-known security idioms.**
  - Clark-Wilson: TPs are the modify/delete tools; the gate is the
    well-formedness check.
  - CRUD decomposition: `READ_FS` / `CREATE_FS` / `MODIFY_FS` /
    `DELETE_FS` are the four standard rights.
  - Append-only ledgers: a session with only `READ_FS` and
    `CREATE_FS` can never destroy history.
  - Git: regular commits append; `git rebase --force-push` requires
    deliberate authorization.
- **Backward compat is preserved.** Existing code with `WRITE_FS`
  capabilities keeps working. Migration is opt-in: tag the
  capability `allows_destructive=True` to bypass the gate, or split
  into granular kinds for per-pattern control.

## Files involved

- `src/capabledeputy/policy/capabilities.py` — `CapabilityKind` enum,
  `DESTRUCTIVE_KINDS`, `_WRITE_UNION_MATCHES`, `Capability.matches`,
  `allows_destructive`.
- `src/capabledeputy/policy/engine.py` — destructive-op gate after
  the conflict-rule loop; `DESTRUCTIVE_OP_RULE` constant.
- `src/capabledeputy/tools/native/memory.py` — `memory.create` /
  `memory.update` / `memory.delete` granular tools.
- `src/capabledeputy/tools/native/calendar.py` — `calendar.update_event`
  / `calendar.delete_event` granular tools.
- `tests/test_destructive_ops.py` — 11 tests covering the rule, the
  bypass, the conflict-rule short-circuit, and end-to-end agent flows.
