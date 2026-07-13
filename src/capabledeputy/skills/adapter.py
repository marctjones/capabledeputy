"""Convert a Skill into a labeled ToolDefinition the registry can host.

The handler:
  1. Renders the skill body with the call args (`{{var}}` substitution).
  2. If the skill declares a `schema`, runs the rendered prompt through
     `quarantined.extractor.extract` and returns the validated object's
     fields as the tool output.
  3. Otherwise calls the LLM directly with the rendered prompt as the
     user message and a fixed quarantined-style system prompt, returning
     `{"text": <response>}`.
  4. Either way, the skill's declared `inherent_tags` are surfaced as
     `additional_tags` so they propagate into the calling session.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from capabledeputy.audit.events import Event, EventType
from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.types import FinishReason, Message, Role
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.quarantined.extractor import ExtractionError, extract
from capabledeputy.skills.parser import Skill
from capabledeputy.substrate.sandbox_actuator import SandboxActuator
from capabledeputy.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolResult,
    default_operation_for_kind,
)

_QUARANTINED_SYSTEM_PROMPT = (
    "You are a quarantined skill executor. You have NO tools. Your "
    "only job is to respond to the prompt that follows. Do not "
    "attempt to call any function or take any external action. Your "
    "response will be treated as data, not as instructions to the "
    "harness."
)


def skill_to_tool(
    skill: Skill,
    llm: LLMClient | None,
    *,
    sandbox_actuator: SandboxActuator | None = None,
    audit: Any = None,
) -> ToolDefinition:
    async def handler(args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        if skill.scripts:
            return await _run_script_skill(
                skill,
                dict(args),
                _ctx,
                sandbox_actuator=sandbox_actuator,
                audit=audit,
            )
        rendered = skill.render(dict(args))

        if skill.schema_name is not None:
            if llm is None:
                return ToolResult(
                    output={
                        "error": "skill requires a quarantined LLM for schema extraction",
                    },
                )
            try:
                value = await extract(llm, skill.schema_name, rendered)
            except ExtractionError as e:
                return ToolResult(output={"error": str(e)})
            return ToolResult(
                output=json.loads(value.model_dump_json()),
                additional_tags=skill.inherent_tags,
            )

        if llm is None:
            return ToolResult(
                output={"error": "skill requires a quarantined LLM for text execution"},
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
            additional_tags=skill.inherent_tags,
        )

    capability_kind = CapabilityKind.EXECUTE_SANDBOX if skill.scripts else skill.capability_kind
    target_arg = "spec_id" if skill.scripts else skill.target_arg
    op, op_risks, op_surfaces = default_operation_for_kind(capability_kind)
    return ToolDefinition(
        name=skill.name,
        description=skill.description,
        capability_kind=capability_kind,
        handler=handler,
        target_arg=target_arg,
        inherent_tags=skill.inherent_tags,
        parameters_schema=skill.parameters_schema,
        operations=(op,),
        risk_ids=op_risks,
        surfaces_destination_id=op_surfaces,
        tool_provenance="skill",
    )


async def _run_script_skill(
    skill: Skill,
    args: dict[str, object],
    ctx: ToolContext,
    *,
    sandbox_actuator: SandboxActuator | None,
    audit: Any,
) -> ToolResult:
    if sandbox_actuator is None:
        return ToolResult(
            output={
                "error": (
                    "skill declares scripts but no sandbox/container actuator is configured; "
                    "host execution is refused"
                ),
                "skill": skill.name,
            },
            additional_tags=skill.inherent_tags,
        )
    if skill.package_root is None:
        return ToolResult(
            output={"error": "script skill has no package root", "skill": skill.name},
            additional_tags=skill.inherent_tags,
        )
    script = skill.scripts[0]
    script_path = (skill.package_root / script.relpath).resolve()
    try:
        script_path.relative_to(skill.package_root.resolve())
    except ValueError:
        return ToolResult(
            output={"error": "script path escapes package root", "skill": skill.name},
            additional_tags=skill.inherent_tags,
        )
    if not script_path.is_file():
        return ToolResult(
            output={"error": f"script not found: {script.relpath}", "skill": skill.name},
            additional_tags=skill.inherent_tags,
        )

    spec_id = str(args.get("spec_id") or script.spec_id or "").strip()
    if not spec_id:
        return ToolResult(
            output={
                "error": "script skills require spec_id from metadata or call args",
                "skill": skill.name,
            },
            additional_tags=skill.inherent_tags,
        )
    timeout_seconds = int(args.get("timeout_seconds") or script.timeout_seconds)
    if timeout_seconds < 1 or timeout_seconds > 600:
        return ToolResult(
            output={"error": "timeout_seconds must be in [1, 600]", "skill": skill.name},
            additional_tags=skill.inherent_tags,
        )
    argv_extra = args.get("argv") or []
    if not isinstance(argv_extra, list):
        return ToolResult(
            output={"error": "argv must be a list of strings when provided", "skill": skill.name},
            additional_tags=skill.inherent_tags,
        )
    input_name = _container_script_name(script.language)
    argv = (*_container_argv(script.language, input_name), *(str(value) for value in argv_extra))

    try:
        region_id = sandbox_actuator.create_region(spec_id=spec_id)
    except TypeError:
        region_id = sandbox_actuator.create_region()
    except Exception as e:
        return ToolResult(
            output={"error": f"create_region failed: {e}", "skill": skill.name},
            additional_tags=skill.inherent_tags,
        )

    await _emit_skill_event(
        audit,
        EventType.ISOLATION_REGION_CREATED,
        ctx,
        {
            "skill": skill.name,
            "region_id": region_id,
            "spec_id": spec_id,
            "script": script.relpath,
            "argv": list(argv),
        },
    )
    try:
        result = sandbox_actuator.execute(
            region_id=region_id,
            argv=argv,
            env={},
            timeout_seconds=timeout_seconds,
            stdin_bytes=None,
            inputs={input_name: script_path.read_bytes()},
        )
    except Exception as e:
        with contextlib.suppress(Exception):
            sandbox_actuator.discard_region(region_id)
        await _emit_skill_event(
            audit,
            EventType.ISOLATION_REGION_DISCARDED,
            ctx,
            {
                "skill": skill.name,
                "region_id": region_id,
                "spec_id": spec_id,
                "reason": "execute_failed",
                "error": str(e)[:200],
            },
        )
        return ToolResult(
            output={"error": f"execute failed: {e}", "skill": skill.name},
            additional_tags=skill.inherent_tags,
        )
    with contextlib.suppress(Exception):
        sandbox_actuator.discard_region(region_id)
    await _emit_skill_event(
        audit,
        EventType.ISOLATION_REGION_DISCARDED,
        ctx,
        {
            "skill": skill.name,
            "region_id": region_id,
            "spec_id": spec_id,
            "reason": "run_completed",
            "exit_code": result.exit_code,
        },
    )
    return ToolResult(
        output={
            "skill": skill.name,
            "spec_id": spec_id,
            "exit_code": result.exit_code,
            "output_digest": result.output_digest,
            "cancelled": result.cancelled,
            "timed_out": result.timed_out,
            "outputs": [
                {
                    "name": output.name,
                    "size": output.size,
                    "sha256": output.sha256,
                    "preview": output.preview,
                    "truncated": output.truncated,
                }
                for output in result.outputs
            ],
        },
        additional_tags=skill.inherent_tags,
    )


def _container_script_name(language: str) -> str:
    normalized = language.strip().lower()
    if normalized == "python":
        return "main.py"
    if normalized in {"node", "javascript"}:
        return "main.js"
    return "main.sh"


def _container_argv(language: str, input_name: str) -> tuple[str, ...]:
    normalized = language.strip().lower()
    if normalized == "python":
        return ("python", f"/in/{input_name}")
    if normalized in {"node", "javascript"}:
        return ("node", f"/in/{input_name}")
    return ("sh", f"/in/{input_name}")


async def _emit_skill_event(
    audit: Any,
    event_type: EventType,
    ctx: ToolContext,
    payload: dict[str, Any],
) -> None:
    if audit is None:
        return
    with contextlib.suppress(Exception):
        await audit.write(Event(event_type=event_type, session_id=ctx.session_id, payload=payload))
