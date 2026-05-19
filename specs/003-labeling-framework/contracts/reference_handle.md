# Contract: Reference Handle / Pattern ③ (003)

First-class data-blind planning (FR-047). The planner manipulates only opaque, unforgeable, per-session tokens; the runtime binds real values at controlled insertion points and records destination provenance.

## Types

```python
@dataclass(frozen=True)
class ReferenceHandle:
    id: UUID                     # unforgeable, per-session
    session_id: UUID
    labels: ResolvedLabels       # axis_a, axis_b (frozen at fetch time)
    issued_at: datetime
    # NOTE: bound_value is NEVER on this struct; it lives in runtime-private state keyed by id

@dataclass(frozen=True)
class HandleBindEvent:
    handle_id: UUID
    destination_canonical_id: str
    tool: str
    audit_id: UUID
    at: datetime
```

## Runtime interface (in-TCB)

```python
class ReferenceHandleStore(Protocol):
    def issue(self, session_id: UUID, value: Any, labels: ResolvedLabels) -> ReferenceHandle:
        """Issue a fresh handle bound to value in runtime-private memory. The planner receives only the ReferenceHandle (no value access)."""

    def bind(self, session_id: UUID, handle_id: UUID, destination_canonical_id: str, tool: str, audit_id: UUID) -> Any:
        """ONLY callable by the deterministic dispatcher AFTER decide() has approved. Returns the bound value for substitution into the tool call AND emits a HandleBindEvent. A bind on a handle whose labels would be denied by decide() MUST be refused."""

    def destroy_session_handles(self, session_id: UUID) -> None:
        """Called on session terminal status; full bind-trail retained in audit."""

    def bind_trail(self, handle_id: UUID) -> list[HandleBindEvent]:
        """Where-the-secret-landed provenance (FR-047)."""
```

## ToolDefinition extension (Pattern ③ opt-in)

A tool that wants to accept handles in place of literal arguments declares:
```python
accepts_handles: bool = False
handle_arg_names: list[str] = []   # which named args may be ReferenceHandles
```
The dispatcher refuses to substitute into a tool with `accepts_handles=False`.

## Invariants (Principle I, III, VI, VIII)

1. **Unforgeable.** Handle IDs are random UUID4; the planner cannot construct one whose `bind` succeeds (the store rejects unknown ids; per-session scoping prevents cross-session smuggling).
2. **Planner never holds the value.** Asserted by a CI test: planner context inspection across all `restricted`-tier scenarios MUST contain 0 raw bound values; only handle IDs (SC-021).
3. **Bind only after decide.** No bind call is reachable from the planner directly; `bind` is dispatcher-only and gated by `decide()`'s outcome on the handle's labels (NOT on the handle id, which is opaque).
4. **Where-the-secret-landed recorded.** Every successful bind produces a `pattern3.handle_bind` audit event with the destination canonical id. A handle ever bound MUST have ≥1 entry in `bind_trail`.
5. **`restricted` requires ③ (or ⑤).** `select_mode` enforces: a session whose effective tier is `restricted` and whose available tools cannot route through ③ (no `accepts_handles=true` tool) AND cannot run in ⑤ (no SandboxActuator) → spawn refused (FR-047).
6. **Handles are not capabilities.** A handle has no authority; it is a data-shaped reference. The dispatcher's `bind` requires both: the handle's labels pass `decide()` AND the session holds a capability authorizing the tool/effect.

## CI invariant tests required

- `test_reference_handle_unforgeable`: planner-side attempts to construct, guess, or cross-session-use a handle id fail.
- `test_planner_context_no_raw_value_under_restricted`: across the quickstart §3 scenario battery, planner `history` contains 0 raw labeled values.
- `test_bind_emits_where_landed`: every successful bind emits a `pattern3.handle_bind` audit event with non-empty `destination_canonical_id`.
- `test_restricted_without_pattern3_or_5_refused_at_spawn`: spawning a `restricted` session whose tool surface offers neither ③ nor ⑤ is refused before any capability is granted.
- `test_handle_is_not_a_capability`: a session holding a handle but no matching capability cannot dispatch the corresponding tool.
