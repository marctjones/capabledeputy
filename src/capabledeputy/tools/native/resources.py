"""Native tools that expose operator-declared resources to the LLM.

Two tools:

  - resources.list(prefix?) — discover available resources
  - resources.read(uri)     — read one resource's content

Both go through the chokepoint with READ_FS capability kind.
Inherent labels from the resource declaration propagate into the
session — so reading a `confidential.personal` document taints the
session for downstream egress decisions, same as fs.read.

Both tools are auto-allow under the standard reversibility model
(reversible/system; reading a static document is a clean operation).
"""

from __future__ import annotations

from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.resources.static import StaticResourcePublisher
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

MAX_RESOURCE_READ_BYTES = 256 * 1024


def make_resources_tools(publisher: StaticResourcePublisher) -> list[ToolDefinition]:
    async def resources_list(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        prefix = str(args.get("prefix", "") or "")
        catalog = publisher.list(prefix)
        return ToolResult(
            output={
                "prefix": prefix,
                "count": len(catalog),
                "resources": [r.to_catalog_entry() for r in catalog],
            },
        )

    async def resources_read(args: dict[str, Any], _ctx: ToolContext) -> ToolResult:
        uri = str(args["uri"])
        resource = publisher.get(uri)
        if resource is None:
            return ToolResult(
                output={"found": False, "uri": uri},
            )
        if not resource.content_path.is_file():
            return ToolResult(
                output={
                    "found": False,
                    "uri": uri,
                    "error": f"content file missing: {resource.content_path}",
                },
            )
        size = resource.content_path.stat().st_size
        if size > MAX_RESOURCE_READ_BYTES:
            return ToolResult(
                output={
                    "found": False,
                    "uri": uri,
                    "error": f"resource too large: {size} bytes (max {MAX_RESOURCE_READ_BYTES})",
                },
            )
        content = resource.content_path.read_text(encoding="utf-8")
        # The resource's declared labels are inherent — they propagate
        # into the session on read. This is the same chokepoint flow as
        # fs.read with bindings, but driven by per-resource declaration
        # rather than by URI binding patterns.
        return ToolResult(
            output={
                "found": True,
                "uri": uri,
                "name": resource.name,
                "mime_type": resource.mime_type,
                "size": size,
                "content": content,
            },
            additional_tags=resource.tags,
        )

    return [
        ToolDefinition(
            name="resources.list",
            operations=(Operation(EffectClass.OBSERVE, subtype="resources.list"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            surfaces_destination_id=False,
            description=(
                "List operator-published resources available to this session. "
                "Returns {uri, name, description, mime_type, labels} per "
                "resource. Optional prefix arg filters by uri prefix."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=resources_list,
            target_arg="prefix",
            parameters_schema={
                "type": "object",
                "properties": {"prefix": {"type": "string"}},
            },
        ),
        ToolDefinition(
            name="resources.read",
            operations=(Operation(EffectClass.FETCH, subtype="resources.read"),),
            risk_ids=("RISK-PII-DISCLOSURE",),
            effect_class="data.read_local",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            surfaces_destination_id=True,
            description=(
                "Read the content of an operator-published resource by uri. "
                "Returns {found, name, mime_type, size, content}. The "
                f"max content size is {MAX_RESOURCE_READ_BYTES} bytes. "
                "Inherent labels from the resource declaration propagate "
                "into this session."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=resources_read,
            target_arg="uri",
            parameters_schema={
                "type": "object",
                "properties": {"uri": {"type": "string"}},
                "required": ["uri"],
            },
        ),
    ]
