"""MCP stdio server that proxies tool calls to a CapableDeputy daemon.

The daemon must already be running. The server connects via the daemon's
JSON-RPC socket, discovers tools via tool.list, and forwards tool calls
through tool.call. Policy denials surface as tool execution errors
(isError=true) so the calling agent (e.g. Claude Code) sees them and
adapts in its own loop.

The `--session-id` argument binds the server to a specific CapableDeputy
session, so labels accumulate and policy decisions are made against
that session's state across the conversation.

Spec leverage (per modelcontextprotocol.io/specification/2025-11-25):

  - Real inputSchema per tool (not the empty `{"type": "object"}`
    placeholder).
  - structuredContent + text fallback for dict outputs, per
    "Structured Content" §.
  - isError=true on policy denials and tool errors, per "Tool
    Execution Errors" §.
  - ToolAnnotations (readOnlyHint / destructiveHint / openWorldHint)
    derived from the capability kind so MCP hosts can render
    appropriate UI confirmations per spec security guidance.
  - _meta carries CapableDeputy-specific capability metadata so
    capability-aware hosts can do further filtering.
  - Resources for memory entries with labels in _meta.
  - Prompts for canonical workflows.
  - Elicitation for in-flow approvals when policy denies a
    declassifiable action — the host's user confirms inline rather
    than running a separate `capdep approval approve` command.
  - Log notifications mirror policy decisions so host UIs surface
    them in real time.

Known boundary (v0.7 audit, documented not silently drifting):
  - In-flow *elicitation* (offer-to-approve without leaving the tool
    call) is intentionally scoped to `email.send` / SEND_EMAIL via a
    manual `approval.submit`. It predates server-side chokepoint
    approval registration (the daemon now auto-registers approvals and
    the outcome carries `approval_id`). It does NOT yet offer in-flow
    approval for the other now-approvable actions (purchase,
    destructive ops). Generalising it to consume `approval_id`
    uniformly is a scoped follow-up — deferred because the MCP stdio
    surface has no integration tests (same posture as the Textual
    apps); rushing an under-tested change to an external approval path
    is the larger risk.
  - All denials (including the v0.7 rules capability-expired /
    rate-limit-exceeded / capability-revoked-by-prior-use) DO surface
    to the host as an isError tool result carrying rule + reason +
    the shared deterministic recovery hint. Enforcement is unaffected
    — the daemon's `decide()` is the chokepoint; this proxy only
    relays decisions.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from capabledeputy.ipc.client import DaemonClient
from capabledeputy.ipc.socket_path import default_socket_path
from capabledeputy.presentation import DENY_RECOVERY

SERVER_NAME = "capdep"


_ANNOTATIONS_BY_KIND: dict[str, dict[str, bool]] = {
    "READ_FS": {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False},
    "WRITE_FS": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    "SEND_EMAIL": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
    "WEB_FETCH": {"readOnlyHint": True, "openWorldHint": True, "idempotentHint": True},
    "CALENDAR_READ": {"readOnlyHint": True, "idempotentHint": True},
    "CALENDAR_WRITE": {"readOnlyHint": False, "destructiveHint": True},
    "QUEUE_PURCHASE": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True},
}


# Rules that we know how to declassify via the approval workflow
# (currently SEND_EMAIL is the only action with a built-in
# purpose-limited execution path).
_ELICITABLE_RULES: frozenset[str] = frozenset(
    {"health-meets-egress", "financial-meets-email", "untrusted-meets-egress"},
)


def _annotations_for(tool: dict[str, Any]) -> mcp_types.ToolAnnotations | None:
    hints = _ANNOTATIONS_BY_KIND.get(tool["capability_kind"])
    if not hints:
        return None
    return mcp_types.ToolAnnotations(
        title=tool["name"],
        **hints,
    )


def _tool_meta(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "io.capabledeputy/capability_kind": tool["capability_kind"],
    }


async def discover_tools(client: DaemonClient) -> list[mcp_types.Tool]:
    result = await client.call("tool.list")
    tools: list[mcp_types.Tool] = []
    for tool in result["tools"]:
        schema = tool.get("parameters_schema") or {"type": "object"}
        annotations = _annotations_for(tool)
        tools.append(
            mcp_types.Tool(
                name=tool["name"],
                title=tool["name"],
                description=tool["description"],
                inputSchema=schema,
                annotations=annotations,
                # mcp `_meta` alias; SDK accepts at runtime, pyright's
                # generated model doesn't expose it. Boundary ignore.
                **{"_meta": _tool_meta(tool)},  # pyright: ignore[reportArgumentType]
            ),
        )
    return tools


def _is_elicitable_email_denial(tool_name: str, result: dict[str, Any]) -> bool:
    """Whether we should offer in-flow approval via elicitation."""
    if tool_name != "email.send":
        return False
    if result.get("decision") != "deny":
        return False
    rule = result.get("rule")
    return bool(rule) and rule in _ELICITABLE_RULES


def _build_elicit_schema(tool_name: str, args: dict[str, Any], rule: str) -> dict[str, Any]:
    return {
        "type": "object",
        "title": f"Approval needed for {tool_name}",
        "description": (
            f"This action was blocked by the policy rule '{rule}'. "
            "If you confirm, capdep will spawn a one-shot purpose-limited "
            "session to execute it; the originating session will not gain "
            "egress capability."
        ),
        "properties": {
            "approve": {
                "type": "boolean",
                "title": "Approve a one-shot send?",
                "description": (
                    f"To: {args.get('to', '?')}. "
                    f"Subject: {args.get('subject', '(none)')}. "
                    f"Body length: {len(str(args.get('body', '')))} chars."
                ),
            },
        },
        "required": ["approve"],
    }


async def _try_elicit_and_approve(
    client: DaemonClient,
    server: Server,
    session_id: UUID,
    tool_name: str,
    args: dict[str, Any],
    deny_result: dict[str, Any],
) -> mcp_types.CallToolResult | None:
    rule = deny_result.get("rule") or ""
    schema = _build_elicit_schema(tool_name, args, rule)
    try:
        elicit_result = await server.request_context.session.elicit(
            message=(f"Approve this {tool_name} despite policy rule '{rule}'? to={args.get('to')}"),
            # mcp's elicit() takes requestedSchema as a plain dict
            # (camelCase). The prior code passed `requested_schema=`
            # with an ElicitRequestedSchema object — wrong kwarg AND
            # wrong type; this path would have raised at runtime when
            # the email-approval elicitation fired.
            requestedSchema={
                "type": "object",
                "properties": schema["properties"],
                "required": schema["required"],
            },
        )
    except Exception:
        return None

    if elicit_result.action != "accept":
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text=(
                        f"User declined elicitation for {tool_name} "
                        f"(action={elicit_result.action})."
                    ),
                ),
            ],
            isError=True,
        )

    content = elicit_result.content or {}
    if not content.get("approve"):
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text="User did not approve via elicitation.",
                ),
            ],
            isError=True,
        )

    submitted = await client.call(
        "approval.submit",
        {
            "from_session": str(session_id),
            "action": "SEND_EMAIL",
            "payload": str(args.get("body", "")),
            "target": str(args.get("to", "")),
            "justification": "user-approved via MCP elicitation",
        },
    )
    approved = await client.call(
        "approval.approve",
        {"id": submitted["id"], "decided_by": "mcp-elicitation"},
    )
    dispatch = approved.get("dispatch") or {}
    if dispatch.get("decision") == "allow":
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text=(
                        "Approved via elicitation; executed in purpose-limited "
                        f"session {approved.get('executed_in_session')}.\n\n"
                        f"{json.dumps(dispatch.get('output') or {}, indent=2)}"
                    ),
                ),
            ],
            isError=False,
        )
    return mcp_types.CallToolResult(
        content=[
            mcp_types.TextContent(
                type="text",
                text=(
                    "Elicitation accepted but execution failed: "
                    f"{dispatch.get('reason') or dispatch.get('error') or 'unknown'}"
                ),
            ),
        ],
        isError=True,
    )


async def _send_log(server: Server, level: str, message: str) -> None:
    with suppress(Exception):
        await server.request_context.session.send_log_message(
            level=level,  # type: ignore[arg-type]
            data=message,
            logger="capdep",
        )


async def dispatch_tool(
    client: DaemonClient,
    session_id: UUID,
    name: str,
    arguments: dict[str, Any],
    server: Server | None = None,
) -> mcp_types.CallToolResult:
    result = await client.call(
        "tool.call",
        {
            "session_id": str(session_id),
            "tool": name,
            "args": arguments,
        },
    )

    if result.get("error"):
        text = f"tool error: {result['error']}"
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            isError=True,
        )

    if result["decision"] != "allow":
        if server is not None and _is_elicitable_email_denial(name, result):
            await _send_log(
                server,
                "warning",
                f"policy denied {name} (rule={result.get('rule')}); offering elicitation",
            )
            elicit_result = await _try_elicit_and_approve(
                client,
                server,
                session_id,
                name,
                arguments,
                result,
            )
            if elicit_result is not None:
                return elicit_result

        rule = result.get("rule") or "no_rule"
        reason = result.get("reason") or ""
        text = f"policy denied (decision={result['decision']}, rule={rule}): {reason}"
        # Surface the same deterministic operator recovery the REPL /
        # TUI / console show (shared, tested presentation.DENY_RECOVERY)
        # so MCP hosts get actionable guidance for the v0.7 rules
        # (capability-expired / rate-limit-exceeded / revoked) too —
        # not just an opaque denial.
        recovery = DENY_RECOVERY.get(rule)
        if recovery:
            text += f"  [recover: {recovery}]"
        meta: dict[str, Any] = {
            "io.capabledeputy/decision": result["decision"],
            "io.capabledeputy/rule": rule,
            "io.capabledeputy/effective_labels": result.get("effective_labels", []),
        }
        if server is not None:
            await _send_log(
                server,
                "warning",
                f"policy denied {name}: rule={rule}",
            )
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=text)],
            isError=True,
            **{"_meta": meta},
        )

    output = result.get("output") or {}
    structured: dict[str, Any] | None = output if isinstance(output, dict) else None
    text_payload = json.dumps(output, indent=2) if isinstance(output, dict | list) else str(output)
    if result.get("labels_added"):
        text_payload += (
            "\n\n[capdep: session labels expanded with " + ", ".join(result["labels_added"]) + "]"
        )

    call_meta: dict[str, Any] = {
        "io.capabledeputy/labels_added": result.get("labels_added", []),
    }

    if server is not None and result.get("labels_added"):
        await _send_log(
            server,
            "info",
            f"tool {name} succeeded; labels expanded with " + ", ".join(result["labels_added"]),
        )

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text_payload)],
        structuredContent=structured,
        isError=False,
        **{"_meta": call_meta},
    )


async def build_server(client: DaemonClient, session_id: UUID) -> Server:
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[mcp_types.Tool]:
        return await discover_tools(client)

    @server.call_tool()
    async def _call_tool(
        name: str,
        arguments: dict[str, Any] | None,
    ) -> mcp_types.CallToolResult:
        return await dispatch_tool(client, session_id, name, arguments or {}, server)

    from capabledeputy.mcp_server import prompts as _prompts
    from capabledeputy.mcp_server import resources as _resources

    @server.list_resources()
    async def _list_resources() -> list[mcp_types.Resource]:
        return await _resources.list_resources(client)

    @server.read_resource()
    async def _read_resource(uri: Any) -> str:
        return await _resources.read_resource(client, session_id, str(uri))

    @server.list_prompts()
    async def _list_prompts() -> list[mcp_types.Prompt]:
        return _prompts.list_prompts()

    @server.get_prompt()
    async def _get_prompt(
        name: str,
        arguments: dict[str, str] | None,
    ) -> mcp_types.GetPromptResult:
        return _prompts.get_prompt(name, arguments)

    return server


async def _watch_capability_changes(
    socket_path: Path,
    session_id: UUID,
    server: Server,
) -> None:
    """Subscribe to the daemon's audit stream and emit MCP
    tools/list_changed when our bound session's capabilities change."""
    client = DaemonClient(socket_path)
    target = str(session_id)
    with suppress(Exception):
        async for event in await client.subscribe(["audit"]):
            data = event.get("data") or {}
            if data.get("event_type") != "capability.granted":
                continue
            if (data.get("session_id") or "") != target:
                continue
            with suppress(Exception):
                await server.request_context.session.send_tool_list_changed()


async def serve(session_id: UUID, socket_path: Path | None = None) -> None:
    import anyio as _anyio

    socket = socket_path or default_socket_path()
    client = DaemonClient(socket)
    server = await build_server(client, session_id)
    async with (
        stdio_server() as (read_stream, write_stream),
        _anyio.create_task_group() as tg,
    ):
        tg.start_soon(_watch_capability_changes, socket, session_id, server)
        try:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
        finally:
            tg.cancel_scope.cancel()
