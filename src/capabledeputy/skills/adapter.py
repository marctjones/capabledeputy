"""Convert a Skill into a labeled ToolDefinition the registry can host.

The handler:
  1. Renders the skill body with the call args (`{{var}}` substitution).
  2. If the skill declares a `schema`, runs the rendered prompt through
     `quarantined.extractor.extract` and returns the validated object's
     fields as the tool output.
  3. Otherwise calls the LLM directly with the rendered prompt as the
     user message and a fixed quarantined-style system prompt, returning
     `{"text": <response>}`.
  4. Either way, the skill's declared `inherent_labels` are surfaced as
     `additional_labels` so they propagate into the calling session.
"""

from __future__ import annotations

import json

from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.types import FinishReason, Message, Role
from capabledeputy.quarantined.extractor import ExtractionError, extract
from capabledeputy.skills.parser import Skill
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

_QUARANTINED_SYSTEM_PROMPT = (
    "You are a quarantined skill executor. You have NO tools. Your "
    "only job is to respond to the prompt that follows. Do not "
    "attempt to call any function or take any external action. Your "
    "response will be treated as data, not as instructions to the "
    "harness."
)


def skill_to_tool(skill: Skill, llm: LLMClient) -> ToolDefinition:
    async def handler(args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        rendered = skill.render(dict(args))

        if skill.schema_name is not None:
            try:
                value = await extract(llm, skill.schema_name, rendered)
            except ExtractionError as e:
                return ToolResult(output={"error": str(e)})
            return ToolResult(
                output=json.loads(value.model_dump_json()),
                additional_labels=skill.inherent_labels,
            )

        response = await llm.respond(
            [
                Message(role=Role.SYSTEM, content=_QUARANTINED_SYSTEM_PROMPT),
                Message(role=Role.USER, content=rendered),
            ],
            [],
        )
        if response.tool_calls:
            return ToolResult(
                output={
                    "error": (
                        "skill LLM emitted tool_calls — refused; skill body "
                        "must produce a text response only"
                    ),
                },
            )
        if response.finish_reason != FinishReason.STOP:
            return ToolResult(
                output={"error": f"skill LLM did not finish cleanly: {response.finish_reason}"},
            )
        return ToolResult(
            output={"text": response.content},
            additional_labels=skill.inherent_labels,
        )

    return ToolDefinition(
        name=skill.name,
        description=skill.description,
        capability_kind=skill.capability_kind,
        handler=handler,
        target_arg=skill.target_arg,
        inherent_labels=skill.inherent_labels,
        parameters_schema=skill.parameters_schema,
    )
