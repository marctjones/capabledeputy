"""Agent-callable `sandbox.run` tool (004 U036 — Phase C).

Lets the agent dispatch a one-shot sandboxed execution through the
chokepoint. The tool:

  - Validates the requested region exists and the agent has
    `EXECUTE_SANDBOX <spec_id>` capability.
  - Creates a fresh disposable region from the requested spec.
  - Writes any caller-provided `inputs` to the region's `/in` mount.
  - Runs `argv` with `timeout_seconds` cap.
  - Harvests stdout/stderr + any files the container wrote to
    `/out`.
  - Discards the region (region death = containment guarantee,
    FR-040).

The chokepoint composes the result with the isolation posture:
effective reversibility lifts to `reversible/system` (containment
undoes the run by construction) but outputs that leave the region
retain their source-category labels — `EXECUTE.sandbox` does NOT
declassify (FR-041).
"""

from __future__ import annotations

import base64
import contextlib
from typing import Any

from capabledeputy.audit.events import Event, EventType
from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult


async def _emit_region_event(
    audit,
    event_type: EventType,
    *,
    session_id,
    payload: dict[str, Any],
) -> None:
    """Helper for emitting ISOLATION_REGION_* events when an audit
    writer is wired. No-op when audit is None (e.g. tests that
    don't care about the event trail). FR-040 / Pattern ⑤ audit."""
    if audit is None:
        return
    await audit.write(
        Event(
            event_type=event_type,
            session_id=session_id,
            payload=payload,
        ),
    )


def make_sandbox_tools(policy_context, audit=None) -> list[ToolDefinition]:
    """Build the `sandbox.run` tool if a SandboxActuator is wired on
    `policy_context`. Returns an empty list otherwise so the tool
    never appears in the agent's list when there's no provider —
    cleaner than registering a tool that always denies.

    `audit`: optional AuditWriter. When provided, every region
    lifecycle transition emits an `isolation_region.created` /
    `isolation_region.discarded` audit event (FR-040). Pattern ⑤'s
    audit trail requires the lifecycle to be visible: a contained
    run that proceeds without an event pair is a reviewable defect.
    """
    if policy_context is None or policy_context.sandbox_actuator is None:
        return []

    actuator = policy_context.sandbox_actuator

    async def run_sandbox(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        spec_id = str(args.get("spec_id", "")).strip()
        if not spec_id:
            return ToolResult(output={"error": "spec_id is required"})

        argv_raw = args.get("argv")
        if not isinstance(argv_raw, list) or not argv_raw:
            return ToolResult(
                output={"error": "argv must be a non-empty list of strings"},
            )
        argv = tuple(str(a) for a in argv_raw)

        timeout_raw = args.get("timeout_seconds")
        timeout_seconds = int(timeout_raw) if timeout_raw is not None else 30
        if timeout_seconds < 1 or timeout_seconds > 600:
            return ToolResult(
                output={"error": "timeout_seconds must be in [1, 600]"},
            )

        # `inputs`: {name: str|{base64: "..."}} — text is encoded utf-8;
        # base64 form is the escape hatch for binary blobs.
        inputs_raw = args.get("inputs") or {}
        if not isinstance(inputs_raw, dict):
            return ToolResult(output={"error": "inputs must be an object"})
        inputs: dict[str, bytes] = {}
        for name, value in inputs_raw.items():
            if isinstance(value, str):
                inputs[name] = value.encode("utf-8")
            elif isinstance(value, dict) and "base64" in value:
                try:
                    inputs[name] = base64.b64decode(value["base64"])
                except Exception as e:
                    return ToolResult(
                        output={"error": f"input {name!r}: invalid base64 ({e})"},
                    )
            else:
                return ToolResult(
                    output={
                        "error": (
                            f"input {name!r} must be a string or "
                            "{base64: '...'} object"
                        ),
                    },
                )

        stdin_raw = args.get("stdin")
        stdin_bytes: bytes | None = None
        if isinstance(stdin_raw, str):
            stdin_bytes = stdin_raw.encode("utf-8")

        # Create + run + harvest + discard.
        try:
            region_id = actuator.create_region(spec_id=spec_id)
        except TypeError:
            # Actuator stub doesn't accept spec_id kwarg
            region_id = actuator.create_region()
        except Exception as e:
            return ToolResult(output={"error": f"create_region failed: {e}"})

        # FR-040 — emit ISOLATION_REGION_CREATED so audit/replay can
        # reconstruct the region lifecycle for SC-017 / SC-021.
        # Closes the "events defined but never emitted" gap.
        await _emit_region_event(
            audit,
            EventType.ISOLATION_REGION_CREATED,
            session_id=ctx.session_id,
            payload={
                "region_id": region_id,
                "spec_id": spec_id,
                "argv": list(argv),
                "timeout_seconds": timeout_seconds,
            },
        )

        try:
            result = actuator.execute(
                region_id=region_id,
                argv=argv,
                env={},
                timeout_seconds=timeout_seconds,
                stdin_bytes=stdin_bytes,
                inputs=inputs or None,
            )
        except Exception as e:
            with contextlib.suppress(Exception):
                actuator.discard_region(region_id)
            await _emit_region_event(
                audit,
                EventType.ISOLATION_REGION_DISCARDED,
                session_id=ctx.session_id,
                payload={
                    "region_id": region_id,
                    "spec_id": spec_id,
                    "reason": "execute_failed",
                    "error": str(e)[:200],
                },
            )
            return ToolResult(output={"error": f"execute failed: {e}"})

        output_payload = {
            "spec_id": spec_id,
            "exit_code": result.exit_code,
            "output_digest": result.output_digest,
            "cancelled": result.cancelled,
            "timed_out": result.timed_out,
            "outputs": [
                {
                    "name": o.name,
                    "size": o.size,
                    "sha256": o.sha256,
                    "preview": o.preview,
                    "truncated": o.truncated,
                }
                for o in result.outputs
            ],
        }

        with contextlib.suppress(Exception):
            actuator.discard_region(region_id)

        await _emit_region_event(
            audit,
            EventType.ISOLATION_REGION_DISCARDED,
            session_id=ctx.session_id,
            payload={
                "region_id": region_id,
                "spec_id": spec_id,
                "reason": "run_completed",
                "exit_code": result.exit_code,
                "cancelled": result.cancelled,
                "timed_out": result.timed_out,
            },
        )

        return ToolResult(output=output_payload)

    return [
        ToolDefinition(
            name="sandbox.run",
            effect_class="EXECUTE.sandbox",
            operations=(Operation(EffectClass.EXECUTE_SANDBOX, subtype="sandbox.run"),),
            risk_ids=("RISK-UNSAFE-CODE-EXEC",),
            surfaces_destination_id=True,
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            description=(
                "Run a command inside a fresh disposable Podman sandbox "
                "region. `spec_id` selects one of the operator-declared "
                "regions (see your daemon config's `sandbox:` block). "
                "`argv` is the command + args. `inputs` is an optional "
                "object mapping `{filename: text|{base64: '...'}}` — each "
                "lands at /in/<filename> in the container (read-only). "
                "Files the container writes to /out are surfaced in the "
                "result. The region is discarded after the run — "
                "containment is the guarantee.\n\n"
                "USE THIS WHEN: the user wants to execute untrusted "
                "code, run a build or script that mutates files, try a "
                "downloaded payload, or experiment without touching the "
                "host filesystem. Reversibility lifts to reversible/"
                "system inside the region; outputs that leave the "
                "region keep their source labels."
            ),
            capability_kind=CapabilityKind.EXECUTE_SANDBOX,
            handler=run_sandbox,
            target_arg="spec_id",
            parameters_schema={
                "type": "object",
                "properties": {
                    "spec_id": {
                        "type": "string",
                        "description": (
                            "Operator-declared region template id "
                            "(must match `EXECUTE_SANDBOX <pattern>` "
                            "capability granted to this session)."
                        ),
                    },
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command and arguments to run inside the container.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max run time (default 30, max 600).",
                    },
                    "inputs": {
                        "type": "object",
                        "description": (
                            "Optional files to stage at /in/<name>. "
                            "Values may be plain strings (utf-8 text) "
                            "or {base64: '...'} for binary."
                        ),
                    },
                    "stdin": {
                        "type": "string",
                        "description": (
                            "Optional utf-8 text piped to the "
                            "container's stdin before output capture "
                            "starts."
                        ),
                    },
                },
                "required": ["spec_id", "argv"],
            },
        ),
    ]
