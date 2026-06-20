"""Small tool descriptor contracts derived from ToolDefinition.

ToolDefinition remains the runtime compatibility type because it carries an
async handler. These descriptors split that large object into the contracts
needed by policy validation, flow enforcement, and manifest inspection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from capabledeputy.policy.effect_class import Operation
from capabledeputy.policy.labels import LabelState


@dataclass(frozen=True)
class ToolRuntimeDescriptor:
    """Non-policy runtime shape of a tool."""

    name: str
    description: str
    parameters_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolPolicyDescriptor:
    """Policy classification and target-extraction metadata."""

    capability_kind: str
    target_arg: str
    target_template: str | None = None
    amount_arg: str | None = None
    effect_class: str | None = None
    operations: tuple[Operation, ...] = ()
    inherent_tags: LabelState = field(default_factory=LabelState)
    arg_inherent_tags: dict[str, LabelState] = field(default_factory=dict)
    tool_provenance: str = "operator-curated"
    surfaces_destination_id: bool = False
    risk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolFlowDescriptor:
    """Information-flow behavior that is not the handler itself."""

    accepts_handles: bool = False
    handle_arg_names: tuple[str, ...] = ()
    forbid_restricted_source: bool = False
    has_source_label_lookup: bool = False


@dataclass(frozen=True)
class ToolDescriptor:
    """Complete inspectable descriptor for one registered tool."""

    runtime: ToolRuntimeDescriptor
    policy: ToolPolicyDescriptor
    flow: ToolFlowDescriptor

    @property
    def name(self) -> str:
        return self.runtime.name


def describe_tool(tool: Any) -> ToolDescriptor:
    """Build a descriptor from a ToolDefinition-like object."""

    kind = tool.capability_kind
    kind_value = kind.value if hasattr(kind, "value") else str(kind)
    return ToolDescriptor(
        runtime=ToolRuntimeDescriptor(
            name=str(tool.name),
            description=str(tool.description),
            parameters_schema=dict(getattr(tool, "parameters_schema", {}) or {}),
        ),
        policy=ToolPolicyDescriptor(
            capability_kind=str(kind_value),
            target_arg=str(getattr(tool, "target_arg", "target")),
            target_template=getattr(tool, "target_template", None),
            amount_arg=getattr(tool, "amount_arg", None),
            effect_class=getattr(tool, "effect_class", None),
            operations=tuple(getattr(tool, "operations", ()) or ()),
            inherent_tags=getattr(tool, "inherent_tags", LabelState()),
            arg_inherent_tags=dict(getattr(tool, "arg_inherent_tags", {}) or {}),
            tool_provenance=str(getattr(tool, "tool_provenance", "operator-curated")),
            surfaces_destination_id=bool(getattr(tool, "surfaces_destination_id", False)),
            risk_ids=tuple(str(r) for r in (getattr(tool, "risk_ids", ()) or ())),
        ),
        flow=ToolFlowDescriptor(
            accepts_handles=bool(getattr(tool, "accepts_handles", False)),
            handle_arg_names=tuple(str(a) for a in (getattr(tool, "handle_arg_names", ()) or ())),
            forbid_restricted_source=bool(getattr(tool, "forbid_restricted_source", False)),
            has_source_label_lookup=getattr(tool, "source_label_lookup", None) is not None,
        ),
    )
