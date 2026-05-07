"""Schema-validated extraction via a quarantined LLM (DESIGN.md §5.2).

The quarantined LLM sees the labeled data but is given a constrained
output schema and no tools. Its only job is to fill in the schema. The
schema is the declassification gate: the planner LLM (and the rest of
the session) sees the typed object, never the raw labeled source.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.types import FinishReason, Message, Role
from capabledeputy.quarantined.schemas import get_schema, schema_to_jsonschema


class ExtractionError(RuntimeError):
    pass


def _build_extraction_prompt(
    schema_name: str,
    schema_json: dict[str, Any],
    labeled_text: str,
) -> list[Message]:
    system = (
        "You are a quarantined extractor. Your only job is to fill in a "
        "JSON object matching the schema below. You MUST emit only valid "
        "JSON conforming to the schema, with no other text. You have no "
        "tools and cannot take actions.\n\n"
        f"Schema name: {schema_name}\n"
        f"Schema (JSONSchema):\n{json.dumps(schema_json, indent=2)}"
    )
    user = (
        "Extract the requested fields from the following text. Return "
        "ONLY a single JSON object matching the schema.\n\n"
        f"---\n{labeled_text}\n---"
    )
    return [
        Message(role=Role.SYSTEM, content=system),
        Message(role=Role.USER, content=user),
    ]


async def extract(
    llm: LLMClient,
    schema_name: str,
    labeled_text: str,
) -> BaseModel:
    schema_cls = get_schema(schema_name)
    schema_json = schema_to_jsonschema(schema_name)
    messages = _build_extraction_prompt(schema_name, schema_json, labeled_text)
    response = await llm.respond(messages, [])

    if response.tool_calls:
        raise ExtractionError(
            "quarantined LLM emitted tool_calls, which it must not have access to",
        )
    if response.finish_reason != FinishReason.STOP:
        raise ExtractionError(
            f"quarantined LLM did not finish cleanly: {response.finish_reason}",
        )

    text = response.content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[: -len("```")].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"quarantined LLM output not JSON: {text[:200]}",
        ) from e

    try:
        return schema_cls.model_validate(parsed)
    except ValidationError as e:
        raise ExtractionError(f"output failed schema validation: {e}") from e
