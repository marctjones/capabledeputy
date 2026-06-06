"""quarantined.extract tool: dual-LLM extraction over labeled memory.

Reads a labeled value from the memory store, runs it through a
quarantined LLM constrained to a Pydantic schema, and returns the
schema-validated result. The schema validation IS the declassification
gate (DESIGN.md §5.2): the planner LLM in the calling session sees
only the structured fields, never the raw labeled text.

The tool's output therefore propagates NO additional labels into the
calling session — typed extraction through an approved schema is by
construction declassified.
"""

from __future__ import annotations

from typing import Any

from capabledeputy.llm.client import LLMClient
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.quarantined.extractor import ExtractionError, extract
from capabledeputy.quarantined.schemas import list_schemas
from capabledeputy.tools.native.memory import LabeledMemoryStore
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


def make_extract_tools(
    memory: LabeledMemoryStore,
    quarantined_llm: LLMClient,
) -> list[ToolDefinition]:
    async def quarantined_extract(
        args: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        key = str(args["key"])
        schema_name = str(args["schema"])
        entry = memory.read(key)
        if entry is None:
            return ToolResult(output={"found": False})
        try:
            extracted = await extract(quarantined_llm, schema_name, str(entry.value))
        except ExtractionError as e:
            return ToolResult(output={"found": True, "error": str(e)})
        return ToolResult(
            output={
                "found": True,
                "schema": schema_name,
                "data": extracted.model_dump(mode="json"),
            },
        )

    schemas = ", ".join(list_schemas())
    return [
        ToolDefinition(
            name="quarantined.extract",
            effect_class="data.read_quarantined",
            operations=(Operation(EffectClass.FETCH, subtype="quarantined.extract"),),
            risk_ids=("RISK-INDIRECT-INJECTION",),
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "Extract structured fields from labeled memory through a "
                "quarantined LLM. The labeled raw text never enters the "
                "calling session's context — only the schema-validated "
                f"result. Available schemas: {schemas}. Required args: "
                "key (string), schema (one of the available schema names)."
            ),
            capability_kind=CapabilityKind.READ_FS,
            handler=quarantined_extract,
            target_arg="key",
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key to extract from."},
                    "schema": {
                        "type": "string",
                        "enum": list_schemas(),
                        "description": "Declassification schema to validate against.",
                    },
                },
                "required": ["key", "schema"],
            },
        ),
    ]
