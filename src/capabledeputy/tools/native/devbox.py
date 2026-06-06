"""Agent-callable `devbox.*` tools — persistent dev containers.

Where `sandbox.run` (tools/native/sandbox.py) spawns a disposable
container per call, the `devbox.*` family addresses a LONG-LIVED
container keyed by (session, spec_id). The LLM can do iterative
work — edit, build, run tests, install a missing dep, iterate
again — across many turns; files in `/work` persist for the life
of the session (and beyond, on the host filesystem).

Tools registered (only when a PodmanDevbox is wired on the
PolicyContext — otherwise the list is empty, same pattern as
sandbox.run):

  devbox.start  — start (or attach to) a container for a spec.
                  Idempotent: returns the existing one if alive.
  devbox.exec   — run argv inside the live container. Auto-starts
                  if not already running, so most agents will only
                  ever call this one.
  devbox.stop   — tear down the container. Workspace dir is left
                  on disk by default so the operator can recover.
  devbox.list   — enumerate live containers for this session +
                  the set of declared specs the agent could pick
                  from.

Capability gating: pattern is the spec_id, same as EXECUTE_SANDBOX.
The native `email.send`-style approval routing is NOT applied —
exec inside an already-running container is reversible (the agent
can re-run, undo, etc.) and the persistent /work mount means the
work product is recoverable. The `start` and `stop` tools are
likewise reversible/system: restartable, no external side effects.
"""

from __future__ import annotations

import base64
from typing import Any

from capabledeputy.policy.capabilities import CapabilityKind
from capabledeputy.policy.effect_class import EffectClass, Operation
from capabledeputy.tools.registry import ToolContext, ToolDefinition, ToolResult

# Per-call cap on the bytes we return inline (stdout + stderr each).
# Output longer than this gets truncated with a marker so the LLM
# can ask for a narrower argv (e.g. `head -n 200`) on the next turn.
_MAX_INLINE_BYTES = 16 * 1024


def _truncate(buf: bytes) -> tuple[str, bool]:
    """Decode UTF-8 + cap to _MAX_INLINE_BYTES. Returns (text,
    truncated). Truncation marker is part of the returned text so
    the LLM sees the boundary inline."""
    if len(buf) <= _MAX_INLINE_BYTES:
        return buf.decode("utf-8", errors="replace"), False
    head = buf[:_MAX_INLINE_BYTES].decode("utf-8", errors="replace")
    return (
        head + f"\n\n[truncated: {len(buf):,} bytes captured; "
        f"capped at {_MAX_INLINE_BYTES:,}. Narrow your argv "
        "(e.g. head/tail/grep) and retry.]",
        True,
    )


def make_devbox_tools(policy_context) -> list[ToolDefinition]:
    """Build the `devbox.*` tool family if a PodmanDevbox is wired.
    Empty list when the daemon isn't configured with the Podman
    provider — keeps the agent's tool list honest about what's
    actually available."""
    if policy_context is None or policy_context.devbox_manager is None:
        return []

    devbox = policy_context.devbox_manager

    async def devbox_start(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        spec_id = str(args.get("spec_id", "")).strip()
        if not spec_id:
            return ToolResult(output={"error": "spec_id is required"})
        try:
            live = devbox.start_or_get(ctx.session_id, spec_id)
        except Exception as e:
            return ToolResult(output={"error": f"start failed: {e}"})
        return ToolResult(
            output={
                "spec_id": spec_id,
                "container_name": live.container_name,
                "workspace_host_path": str(live.workspace_host_path),
                "started": True,
            },
        )

    async def devbox_exec(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
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
        if timeout_seconds < 1 or timeout_seconds > 1800:
            return ToolResult(
                output={"error": "timeout_seconds must be in [1, 1800]"},
            )

        workdir = str(args.get("workdir") or "/work").strip() or "/work"

        stdin_raw = args.get("stdin")
        stdin_bytes: bytes | None = None
        if isinstance(stdin_raw, str):
            stdin_bytes = stdin_raw.encode("utf-8")
        elif isinstance(stdin_raw, dict) and "base64" in stdin_raw:
            try:
                stdin_bytes = base64.b64decode(stdin_raw["base64"])
            except Exception as e:
                return ToolResult(
                    output={"error": f"stdin: invalid base64 ({e})"},
                )

        env_raw = args.get("env") or {}
        if not isinstance(env_raw, dict):
            return ToolResult(output={"error": "env must be an object"})
        env: dict[str, str] = {str(k): str(v) for k, v in env_raw.items()}

        try:
            result = devbox.exec(
                ctx.session_id,
                spec_id,
                argv=argv,
                timeout_seconds=timeout_seconds,
                env=env,
                stdin_bytes=stdin_bytes,
                workdir=workdir,
            )
        except Exception as e:
            return ToolResult(output={"error": f"exec failed: {e}"})

        stdout_text, stdout_trunc = _truncate(result.stdout)
        stderr_text, stderr_trunc = _truncate(result.stderr)
        return ToolResult(
            output={
                "spec_id": spec_id,
                "container_name": result.container_name,
                "exit_code": result.exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_truncated": stdout_trunc,
                "stderr_truncated": stderr_trunc,
                "cancelled": result.cancelled,
                "timed_out": result.timed_out,
            },
        )

    async def devbox_stop(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        spec_id = str(args.get("spec_id", "")).strip()
        if not spec_id:
            return ToolResult(output={"error": "spec_id is required"})
        purge = bool(args.get("purge_workspace", False))
        try:
            stopped = devbox.stop(
                ctx.session_id,
                spec_id,
                purge_workspace=purge,
            )
        except Exception as e:
            return ToolResult(output={"error": f"stop failed: {e}"})
        return ToolResult(
            output={
                "spec_id": spec_id,
                "stopped": stopped,
                "workspace_purged": purge if stopped else False,
            },
        )

    async def devbox_list(_args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            live_list = devbox.list_session(ctx.session_id)
            all_specs = list(devbox.list_specs())
        except Exception as e:
            return ToolResult(output={"error": f"list failed: {e}"})
        return ToolResult(
            output={
                "live": list(live_list),
                "available_specs": all_specs,
            },
        )

    return [
        ToolDefinition(
            name="devbox.start",
            operations=(Operation(EffectClass.EXECUTE_SANDBOX, subtype="devbox.start"),),
            risk_ids=("RISK-UNSAFE-CODE-EXEC",),
            surfaces_destination_id=True,
            description=(
                "Start (or attach to) a persistent dev container for "
                "the given spec_id. Idempotent: if a container is "
                "already live for (this session, this spec), returns "
                "its handle without restarting. The `/work` volume "
                "persists across turns and across daemon restarts. "
                "USE THIS WHEN: starting a multi-turn coding session "
                "in a specific tech stack. For most flows you can "
                "skip this and just call `devbox.exec` directly — "
                "it auto-starts."
            ),
            capability_kind=CapabilityKind.EXECUTE_DEVBOX,
            effect_class="EXECUTE.devbox",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            handler=devbox_start,
            target_arg="spec_id",
            parameters_schema={
                "type": "object",
                "properties": {
                    "spec_id": {
                        "type": "string",
                        "description": (
                            "Operator-declared region template id "
                            "(must match `EXECUTE_DEVBOX <pattern>` "
                            "granted to this session)."
                        ),
                    },
                },
                "required": ["spec_id"],
            },
        ),
        ToolDefinition(
            name="devbox.exec",
            operations=(Operation(EffectClass.EXECUTE_SANDBOX, subtype="devbox.exec"),),
            risk_ids=("RISK-UNSAFE-CODE-EXEC",),
            surfaces_destination_id=True,
            description=(
                "Run `argv` inside the persistent dev container for "
                "spec_id. Auto-starts the container if not already "
                "running. Working dir defaults to `/work` (the "
                "persistent volume). Returns stdout, stderr, and "
                "exit_code. stdin can be a string or "
                "{base64: '...'} for binary. env is a flat string "
                "map (subject to the spec's env_allowlist). Output "
                "larger than 16 KiB is truncated with a marker — "
                "narrow your argv on the retry."
            ),
            capability_kind=CapabilityKind.EXECUTE_DEVBOX,
            effect_class="EXECUTE.devbox",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            handler=devbox_exec,
            target_arg="spec_id",
            parameters_schema={
                "type": "object",
                "properties": {
                    "spec_id": {"type": "string"},
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Command + arguments.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max wait per exec (default 30, max 1800).",
                    },
                    "workdir": {
                        "type": "string",
                        "description": (
                            "Working directory inside the container (default `/work`)."
                        ),
                    },
                    "stdin": {
                        "description": (
                            "Optional stdin: utf-8 string OR {base64: '...'} for binary."
                        ),
                    },
                    "env": {
                        "type": "object",
                        "description": (
                            "Env vars to pass through (filtered by the spec's env_allowlist)."
                        ),
                    },
                },
                "required": ["spec_id", "argv"],
            },
        ),
        ToolDefinition(
            name="devbox.stop",
            operations=(Operation(EffectClass.EXECUTE_SANDBOX, subtype="devbox.stop"),),
            risk_ids=("RISK-UNSAFE-CODE-EXEC",),
            surfaces_destination_id=True,
            description=(
                "Tear down the persistent container for spec_id. The "
                "workspace dir is preserved on disk by default so you "
                "can recover work; pass `purge_workspace: true` to "
                "also delete it. Returns whether something was found "
                "and stopped."
            ),
            capability_kind=CapabilityKind.EXECUTE_DEVBOX,
            effect_class="EXECUTE.devbox",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            handler=devbox_stop,
            target_arg="spec_id",
            parameters_schema={
                "type": "object",
                "properties": {
                    "spec_id": {"type": "string"},
                    "purge_workspace": {
                        "type": "boolean",
                        "description": (
                            "If true, also delete the host-side workspace dir (default false)."
                        ),
                    },
                },
                "required": ["spec_id"],
            },
        ),
        ToolDefinition(
            name="devbox.list",
            operations=(Operation(EffectClass.OBSERVE, subtype="devbox.list"),),
            risk_ids=("RISK-EXCESSIVE-AGENCY",),
            description=(
                "List the live persistent containers for this "
                "session and the available specs you can start. "
                "Read-only; no side effects."
            ),
            capability_kind=CapabilityKind.EXECUTE_DEVBOX,
            effect_class="EXECUTE.devbox",
            default_reversibility={"degree": "reversible", "agent": "system"},
            tool_provenance="operator-curated",
            handler=devbox_list,
            target_arg="spec_id",
            parameters_schema={
                "type": "object",
                "properties": {},
            },
        ),
    ]
