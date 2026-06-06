"""Reference Handle / Pattern ③ (003 US5 / FR-047 / SC-021).

First-class data-blind planning: the planner manipulates only
opaque, unforgeable, per-session tokens (`ReferenceHandle`); the
*runtime* binds real values at controlled insertion points and
records where each handle landed.

Two structural invariants (per contracts/reference_handle.md):

  1. The planner never holds raw values. `ReferenceHandleStore`
     keeps `id → value` in runtime-private state; the planner sees
     only the `ReferenceHandle` (no `value` field on the dataclass).

  2. Bind is dispatcher-only and gated by decide(). The dispatcher
     calls `bind(...)` AFTER `decide()` has approved on the handle's
     labels; `bind` emits a `pattern3.handle_bind` audit event with
     the canonical destination id.

The store is per-process in-memory; per-session UUIDs prevent
cross-session smuggling. The contract is satisfied here as a
concrete class because the chokepoint is in-TCB; spec 004's tool
substrate consumes this same class.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


class ReferenceHandleError(RuntimeError):
    """Handle lookup or bind violation. Fail-closed per Principle VI."""


@dataclass(frozen=True)
class ResolvedLabels:
    """The frozen-at-fetch labels carried with a handle. A stringly-typed
    snapshot (category ids + provenance level strings) — the handle wire
    format; callers project a `LabelState` into these tuples."""

    axis_a: tuple[str, ...] = field(default_factory=tuple)  # category ids
    axis_b: tuple[str, ...] = field(default_factory=tuple)  # provenance levels


@dataclass(frozen=True)
class ReferenceHandle:
    """The planner-visible token. NOTE: no `bound_value` field — the
    value lives in runtime-private state keyed by id."""

    id: UUID
    session_id: UUID
    labels: ResolvedLabels
    issued_at: datetime


@dataclass(frozen=True)
class HandleBindEvent:
    """Where-the-secret-landed record (FR-047). Emitted by `bind`."""

    handle_id: UUID
    destination_canonical_id: str
    tool: str
    audit_id: UUID
    at: datetime


class ReferenceHandleStore:
    """In-TCB store. Holds (handle_id → value) in private state; only
    the dispatcher invokes `bind` to retrieve values. The planner has
    no access to this object."""

    def __init__(self) -> None:
        self._values: dict[UUID, Any] = {}
        # session_id → set of handle ids issued for that session.
        # Used by destroy_session_handles to scope cleanup.
        self._by_session: dict[UUID, set[UUID]] = {}
        # handle_id → list of bind events (where the value landed).
        self._bind_trail: dict[UUID, list[HandleBindEvent]] = {}

    def issue(
        self,
        session_id: UUID,
        value: Any,
        labels: ResolvedLabels,
    ) -> ReferenceHandle:
        """Issue a fresh handle bound to `value`. Returns ONLY the
        ReferenceHandle (which has no value field) — the value lives
        in this store's private dict, keyed by the unforgeable id."""
        handle = ReferenceHandle(
            id=uuid4(),
            session_id=session_id,
            labels=labels,
            issued_at=datetime.now(UTC),
        )
        self._values[handle.id] = value
        self._by_session.setdefault(session_id, set()).add(handle.id)
        return handle

    def bind(
        self,
        session_id: UUID,
        handle_id: UUID,
        destination_canonical_id: str,
        tool: str,
        audit_id: UUID,
    ) -> Any:
        """Look up the value for `handle_id` and emit a HandleBindEvent.

        Refuses (raises ReferenceHandleError):
          - Unknown handle id (planner forging or guessing).
          - Cross-session use (a handle issued in session A used in
            session B).
          - Empty destination_canonical_id (FR-047 audit demands a
            non-empty destination).

        Callers MUST run decide() against the handle's labels BEFORE
        calling bind; this store does not consult decide() itself
        (separation of mechanisms — the store is the substrate, the
        engine is the oracle)."""
        if handle_id not in self._values:
            raise ReferenceHandleError(
                f"unknown handle id {handle_id} (forged or cross-session)",
            )
        if handle_id not in self._by_session.get(session_id, set()):
            raise ReferenceHandleError(
                f"handle {handle_id} was not issued for session {session_id}",
            )
        if not destination_canonical_id:
            raise ReferenceHandleError(
                "bind requires a non-empty destination_canonical_id (FR-047)",
            )
        value = self._values[handle_id]
        event = HandleBindEvent(
            handle_id=handle_id,
            destination_canonical_id=destination_canonical_id,
            tool=tool,
            audit_id=audit_id,
            at=datetime.now(UTC),
        )
        self._bind_trail.setdefault(handle_id, []).append(event)
        return value

    def destroy_session_handles(self, session_id: UUID) -> None:
        """Called on terminal session status. Removes the stored
        values; bind_trail is retained (audit demand)."""
        ids = self._by_session.pop(session_id, set())
        for hid in ids:
            self._values.pop(hid, None)

    def bind_trail(self, handle_id: UUID) -> list[HandleBindEvent]:
        """Return all bind events for a handle (FR-047)."""
        return list(self._bind_trail.get(handle_id, ()))

    def has_handle(self, handle_id: UUID) -> bool:
        return handle_id in self._values


async def wrap_output_with_handles(
    *,
    store: ReferenceHandleStore,
    session_id: UUID,
    output: dict[str, Any],
    sensitive_keys: tuple[str, ...] = (),
    labels: ResolvedLabels | None = None,
) -> dict[str, Any]:
    """Pattern (3) producer side.

    Substitute each value at `sensitive_keys` in `output` with a
    ReferenceHandle issued by the store. The planner sees only the
    UUID string; the substrate (dispatcher) binds the real value
    post-decide. Without this helper, every read-tool's raw value
    flows directly into the planner's context.

    Returns a new dict (does not mutate). Non-sensitive keys are
    copied through unchanged. Empty `sensitive_keys` returns the
    output unchanged (no-op wrapper).

    Operators wire this into read-tool adapters that produce labeled
    data. Example:

        async def medical_read(args, ctx):
            value = ... # fetch from substrate
            return ToolResult(
                output=await wrap_output_with_handles(
                    store=ctx.handle_store,
                    session_id=ctx.session_id,
                    output={"record": value, "key": args["key"]},
                    sensitive_keys=("record",),
                    labels=ResolvedLabels(
                        axis_a=("health",),
                        axis_b=("source-declared",),
                    ),
                ),
            )

    The dispatcher's post-decide bind step (LabeledToolClient._bind_
    reference_handles) then substitutes the UUID back to `value`
    before the consuming tool's handler runs.
    """
    if not sensitive_keys:
        return dict(output)
    resolved_labels = labels or ResolvedLabels()
    wrapped: dict[str, Any] = {}
    for key, value in output.items():
        if key in sensitive_keys:
            handle = store.issue(session_id, value, resolved_labels)
            wrapped[key] = str(handle.id)
        else:
            wrapped[key] = value
    return wrapped


def make_handle_wrapper(
    tool_handler: Any,
    *,
    store: ReferenceHandleStore,
    sensitive_keys: tuple[str, ...],
    labels: ResolvedLabels | None = None,
) -> Any:
    """Decorator factory: wrap a tool handler so its output's
    sensitive_keys are auto-issued as ReferenceHandles. The
    planner-visible output contains UUIDs; the bound values live
    in the store and are substituted at the dispatcher's bind step
    on downstream tools that declare accepts_handles=True.

    Operators apply this to any read-tool that fetches data above a
    tier threshold; the resulting tool integrates into the existing
    ToolDefinition without further changes. Pattern (3) is now end-
    to-end: producer wrapper here, consumer bind in client.py.
    """
    from capabledeputy.tools.registry import ToolResult

    async def wrapped(args: dict[str, Any], context: Any) -> Any:
        raw = await tool_handler(args, context)
        wrapped_output = await wrap_output_with_handles(
            store=store,
            session_id=context.session_id,
            output=raw.output if isinstance(raw, ToolResult) else dict(raw),
            sensitive_keys=sensitive_keys,
            labels=labels,
        )
        if isinstance(raw, ToolResult):
            from dataclasses import replace as _replace

            return _replace(raw, output=wrapped_output)
        return ToolResult(output=wrapped_output)

    return wrapped


def is_planner_safe_token(planner_token: str) -> bool:
    """Cheap sanity check that callers can use to assert the planner
    is NOT holding a raw labeled value. A planner-safe token is the
    string form of a UUID (the handle id); anything longer or with
    non-hex/non-dash chars is suspect."""
    # UUID strings are exactly 36 chars: 8-4-4-4-12 with dashes.
    if len(planner_token) != 36:
        return False
    parts = planner_token.split("-")
    if len(parts) != 5 or [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    allowed = set("0123456789abcdef")
    return all(c in allowed for c in planner_token.replace("-", ""))


def generate_handle_token() -> str:
    """Generate a token-shaped string; useful for testing the
    planner-safe-token check. Production handle ids come from uuid4()."""
    return str(UUID(bytes=secrets.token_bytes(16), version=4))
