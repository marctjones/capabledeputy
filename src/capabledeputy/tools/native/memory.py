"""Labeled in-memory key-value store and the memory.read / memory.write tools.

memory.write stores a value alongside the calling session's current label
state. memory.read returns the value along with the stored labels as
additional_tags, so they propagate into the calling session — that's
the IFC-correct behavior: reading labeled data inherits its labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from capabledeputy.approval.model import ApprovalAction
from capabledeputy.approval.route import ApprovalPayloadKind, ApprovalRoute
from capabledeputy.patterns.reference_handle import ResolvedLabels
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.policy.labels import LabelState
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

_DESTRUCTIVE_ROUTE = ApprovalRoute(
    action=ApprovalAction.EXECUTE_DESTRUCTIVE,
    target_arg="key",
    payload_kind=ApprovalPayloadKind.TOOL_ENVELOPE,
)


@dataclass
class _MemoryEntry:
    value: Any
    label_state: LabelState


class LabeledMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, _MemoryEntry] = {}

    def write(self, key: str, value: Any, label_state: LabelState) -> None:
        self._data[key] = _MemoryEntry(value=value, label_state=label_state)

    def read(self, key: str) -> _MemoryEntry | None:
        return self._data.get(key)

    def keys(self) -> list[str]:
        return sorted(self._data.keys())

    def label_state_of(self, key: str) -> LabelState:
        entry = self._data.get(key)
        return entry.label_state if entry else LabelState()

    def labels_of(self, key: str) -> LabelState:
        """Alias for label_state_of (backward compat)."""
        return self.label_state_of(key)


def _resolved_labels_from_state(label_state: LabelState) -> ResolvedLabels:
    """Encode a LabelState snapshot in the reference-handle wire format."""
    axis_a = tuple(
        f"{tag.category}:{tag.tier.value}"
        for tag in sorted(label_state.a, key=lambda t: (t.category, t.tier.value))
    )
    axis_b = tuple(tag.level.value for tag in sorted(label_state.b, key=lambda t: t.level.value))
    return ResolvedLabels(axis_a=axis_a, axis_b=axis_b)


def make_memory_tools(store: LabeledMemoryStore) -> list[ToolDefinition]:
    async def memory_write(args: dict[str, Any], context: ToolContext) -> ToolResult:
        key = str(args["key"])
        value = args["value"]
        store.write(key, value, context.label_state)
        return ToolResult(output={"ok": True, "key": key})

    async def memory_read(args: dict[str, Any], context: ToolContext) -> ToolResult:
        key = str(args["key"])
        entry = store.read(key)
        if entry is None:
            return ToolResult(output={"found": False})
        return ToolResult(
            output={"found": True, "value": entry.value},
            additional_tags=entry.label_state,
        )

    async def memory_handle(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Return a Pattern (3) handle for a stored value without exposing it."""
        key = str(args["key"])
        entry = store.read(key)
        if entry is None:
            return ToolResult(output={"found": False})
        if context.handle_store is None:
            return ToolResult(
                output={
                    "found": True,
                    "error": "reference handle store is not wired",
                },
            )
        handle = context.handle_store.issue(
            context.session_id,
            entry.value,
            _resolved_labels_from_state(entry.label_state),
        )
        return ToolResult(
            output={
                "found": True,
                "key": key,
                "handle": str(handle.id),
            },
        )

    async def memory_create(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Create-only write. Fails if the key already exists. Tagged
        CREATE_FS so the policy engine's destructive-op gate doesn't
        fire — creating a new key is non-destructive by definition."""
        key = str(args["key"])
        value = args["value"]
        if store.read(key) is not None:
            return ToolResult(
                output={"ok": False, "error": f"key already exists: {key}"},
            )
        store.write(key, value, context.label_state)
        return ToolResult(output={"ok": True, "key": key, "created": True})

    async def memory_update(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Modify-existing write. Fails if the key doesn't exist.
        Tagged MODIFY_FS — the destructive-op gate fires unless the
        capability has allows_destructive=True or the user approves."""
        key = str(args["key"])
        value = args["value"]
        if store.read(key) is None:
            return ToolResult(
                output={"ok": False, "error": f"key does not exist: {key}"},
            )
        store.write(key, value, context.label_state)
        return ToolResult(output={"ok": True, "key": key, "modified": True})

    async def memory_delete(args: dict[str, Any], context: ToolContext) -> ToolResult:
        """Remove a key from the store. Tagged DELETE_FS — the
        destructive-op gate fires unless explicitly authorized."""
        key = str(args["key"])
        if store.read(key) is None:
            return ToolResult(
                output={"ok": False, "error": f"key does not exist: {key}"},
            )
        # The store doesn't currently expose a delete primitive; we
        # emulate it by overwriting with a tombstone marker. A future
        # store implementation should add a real delete that removes
        # the entry. For now this surfaces the right policy semantics.
        store._data.pop(key, None)  # type: ignore[attr-defined]
        return ToolResult(output={"ok": True, "key": key, "deleted": True})

    return [
        ToolDefinition(
            name="memory.write",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="memory.write"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            description=(
                "Write a value to a key in the memory store (create or "
                "overwrite). Required args: key (string), value (string)."
            ),
            capability_kind=CapabilityKind.WRITE_FS,
            handler=memory_write,
            target_arg="key",
            effect_class="data.write_local",
            default_reversibility={"degree": "reversible-with-friction", "agent": "human"},
            tool_provenance="operator-curated",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to write."},
                    "value": {"type": "string", "description": "Value to store."},
                },
                "required": ["key", "value"],
            },
        ),
        ToolDefinition(
            name="memory.read",
            operations=(Operation(EffectClass.FETCH, subtype="memory.read"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            description=(
                "Read the value at a key in the memory store. Returns "
                "{found, value} and propagates the value's labels into "
                "the calling session. Raw reads are refused for restricted "
                "or prohibited source labels; use memory.handle plus a "
                "handle-aware tool or sealed isolation for those values. "
                "Required args: key (string)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=memory_read,
            target_arg="key",
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            source_label_lookup=lambda args: store.label_state_of(str(args.get("key", ""))),
            forbid_restricted_source=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to read."},
                },
                "required": ["key"],
            },
        ),
        ToolDefinition(
            name="memory.handle",
            operations=(Operation(EffectClass.FETCH, subtype="memory.handle"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            description=(
                "Issue a planner-safe reference handle for the value at a "
                "memory key. The raw value stays in the runtime-private "
                "handle store; the planner receives only {found, key, "
                "handle}. Use this for restricted/prohibited memory values "
                "that must flow into handle-aware tools. Required args: "
                "key (string)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=memory_handle,
            target_arg="key",
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            source_label_lookup=lambda args: store.label_state_of(str(args.get("key", ""))),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to wrap."},
                },
                "required": ["key"],
            },
        ),
        ToolDefinition(
            name="memory.create",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="memory.create"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            description=(
                "Create a new key. Fails if the key already exists. "
                "Non-destructive: bypasses the destructive-op gate. "
                "Required args: key (string), value (string)."
            ),
            capability_kind=CapabilityKind.CREATE_FS,
            handler=memory_create,
            target_arg="key",
            effect_class="data.create_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        ),
        ToolDefinition(
            name="memory.update",
            operations=(Operation(EffectClass.MUTATE_LOCAL, subtype="memory.update"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            surfaces_destination_id=True,
            description=(
                "Update an existing key. Fails if the key doesn't exist. "
                "Destructive: requires approval unless the capability has "
                "allows_destructive=True. Required args: key, value."
            ),
            capability_kind=CapabilityKind.MODIFY_FS,
            effect_class="data.modify_local",
            default_reversibility={"degree": "reversible-with-friction", "agent": "human"},
            tool_provenance="operator-curated",
            handler=memory_update,
            target_arg="key",
            approval_route=_DESTRUCTIVE_ROUTE,
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        ),
        ToolDefinition(
            name="memory.delete",
            operations=(Operation(EffectClass.DESTROY, subtype="memory.delete"),),
            risk_ids=("RISK-DESTRUCTIVE-WRITE",),
            surfaces_destination_id=True,
            effect_class="data.delete_local",
            default_reversibility={"degree": "irreversible", "agent": "external"},
            tool_provenance="operator-curated",
            description=(
                "Remove a key from the memory store. Destructive: "
                "requires approval unless the capability has "
                "allows_destructive=True. Required args: key."
            ),
            capability_kind=CapabilityKind.DELETE_FS,
            handler=memory_delete,
            target_arg="key",
            approval_route=_DESTRUCTIVE_ROUTE,
            parameters_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        ),
    ]
