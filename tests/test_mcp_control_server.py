from __future__ import annotations

import mcp.types as mcp_types

from capabledeputy.mcp_server.control import (
    build_control_server,
    discover_control_tools,
    dispatch_control_tool,
)


def _text(result: mcp_types.CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, mcp_types.TextContent)
    return content.text


def test_control_tools_include_daemon_client_surface() -> None:
    tools = discover_control_tools()
    names = {tool.name for tool in tools}

    assert "capdep_ping" in names
    assert "app_status" in names
    assert "daemon_state" in names
    assert "session_list" in names
    assert "session_new" in names
    assert "session_security_context" in names
    assert "session_turn_start" in names
    assert "session_turn_events" in names
    assert "session_turn_cancel" in names
    assert "tool_call" in names
    assert "workstream_claim" in names
    assert "workstream_ensure" in names
    assert "workstream_release" in names
    assert "workstream_list" in names
    assert "workstream_release_client" in names
    assert "workstream_sweep_expired" in names
    assert "approval_approve" in names
    assert "setup_status" in names
    assert "workflow_launch" in names
    assert "mcp_admission_preview" in names
    assert "mcp_admission_approve" in names
    assert "mcp_admission_disable" in names
    assert "mcp_admission_list" in names
    assert "mcp_admission_audit" in names
    assert "google_oauth_status" in names
    assert "gmail_oauth_login" in names
    assert "provenance_graph" in names
    assert "onguard_schedule_create" in names
    assert "onguard_queue_enqueue" in names
    assert "onguard_artifact_promote" in names

    tool_call = next(tool for tool in tools if tool.name == "tool_call")
    assert tool_call.annotations is not None
    assert tool_call.annotations.readOnlyHint is False
    assert tool_call.annotations.destructiveHint is True
    assert tool_call.annotations.openWorldHint is True

    app_status = next(tool for tool in tools if tool.name == "app_status")
    assert app_status.annotations is not None
    assert app_status.annotations.readOnlyHint is True

    for tool in tools:
        assert tool.outputSchema is not None
        assert tool.meta is not None
        assert tool.meta["io.capabledeputy/surface"] == "control"
        assert tool.meta["io.capabledeputy/session_bound"] is False


async def test_control_status_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon({"app.status": {"daemon": "running"}})

    result = await dispatch_control_tool(client, "app_status")

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["daemon"] == "running"
    assert result.meta is not None
    assert result.meta["io.capabledeputy/surface"] == "control"
    assert client.calls == [("app.status", None)]


async def test_control_daemon_state_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon({"daemon.state": {"schema_version": 1, "daemon": {"pid": 123}}})

    result = await dispatch_control_tool(client, "daemon_state")

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["schema_version"] == 1
    assert client.calls == [("daemon.state", None)]


async def test_control_session_new_dispatches_params(fake_daemon) -> None:
    client = fake_daemon({"session.new": {"id": "s1"}})

    result = await dispatch_control_tool(
        client,
        "session_new",
        {
            "owner": "codex",
            "intent": "review inbox",
            "purpose_handle": "personal_assistant",
            "labels": ["low", "user"],
            "first_use_prompts": False,
        },
    )

    assert result.isError is False
    assert client.calls == [
        (
            "session.new",
            {
                "owner": "codex",
                "intent": "review inbox",
                "purpose_handle": "personal_assistant",
                "labels": ["low", "user"],
                "first_use_prompts": False,
            },
        ),
    ]


async def test_control_session_security_context_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon(
        {"session.security_context": {"session": {"id": "s1"}, "schema_version": 1}},
    )

    result = await dispatch_control_tool(
        client,
        "session_security_context",
        {"session_id": "s1"},
    )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["session"]["id"] == "s1"
    assert client.calls == [("session.security_context", {"session_id": "s1"})]


async def test_control_session_turn_start_dispatches_to_daemon(fake_daemon) -> None:
    client = fake_daemon({"session.turn.start": {"turn": {"id": "t1"}}})

    result = await dispatch_control_tool(
        client,
        "session_turn_start",
        {
            "session_id": "s1",
            "message": "hello",
            "client_id": "codex",
            "heartbeat_enabled": True,
            "heartbeat_timeout_seconds": 5,
        },
    )

    assert result.isError is False
    assert client.calls == [
        (
            "session.turn.start",
            {
                "session_id": "s1",
                "message": "hello",
                "client_id": "codex",
                "heartbeat_enabled": True,
                "heartbeat_timeout_seconds": 5,
            },
        ),
    ]


async def test_control_session_turn_events_and_cancel_dispatch(fake_daemon) -> None:
    client = fake_daemon(
        {
            "session.turn.events": {"events": []},
            "session.turn.cancel": {"turn": {"id": "t1", "status": "cancelled"}},
        },
    )

    events = await dispatch_control_tool(
        client,
        "session_turn_events",
        {"turn_id": "t1", "after": 3},
    )
    cancel = await dispatch_control_tool(
        client,
        "session_turn_cancel",
        {"turn_id": "t1", "reason": "operator", "client_id": "codex"},
    )

    assert events.isError is False
    assert cancel.isError is False
    assert client.calls == [
        ("session.turn.events", {"turn_id": "t1", "after": 3}),
        (
            "session.turn.cancel",
            {"turn_id": "t1", "reason": "operator", "client_id": "codex"},
        ),
    ]


async def test_control_tool_call_dispatches_policy_gated_call(fake_daemon) -> None:
    client = fake_daemon({"tool.call": {"queued_approval": True}})

    result = await dispatch_control_tool(
        client,
        "tool_call",
        {
            "session_id": "s1",
            "tool": "gmail.send",
            "args": {"to": "person@example.com"},
        },
    )

    assert result.isError is False
    assert "queued_approval" in _text(result)
    assert client.calls == [
        (
            "tool.call",
            {
                "session_id": "s1",
                "tool": "gmail.send",
                "args": {"to": "person@example.com"},
            },
        ),
    ]


async def test_control_approval_approve_dispatches(fake_daemon) -> None:
    client = fake_daemon({"approval.approve": {"status": "approved"}})

    result = await dispatch_control_tool(
        client,
        "approval_approve",
        {"id": 42},
    )

    assert result.isError is False
    assert client.calls == [
        ("approval.approve", {"id": 42, "decided_by": "mcp-control"}),
    ]


async def test_control_gmail_oauth_login_dispatches(fake_daemon) -> None:
    client = fake_daemon({"setup.google_gmail.oauth_login": {"token_configured": True}})

    result = await dispatch_control_tool(
        client,
        "gmail_oauth_login",
        {"open_browser": True, "timeout_seconds": 90},
    )

    assert result.isError is False
    assert client.calls == [
        (
            "setup.google_gmail.oauth_login",
            {"open_browser": True, "timeout_seconds": 90},
        ),
    ]


async def test_control_workflow_launch_dispatches(fake_daemon) -> None:
    client = fake_daemon({"workflow.launch": {"turn": {"id": "t1"}}})

    result = await dispatch_control_tool(
        client,
        "workflow_launch",
        {"template_id": "meeting-prep", "client_id": "codex"},
    )

    assert result.isError is False
    assert client.calls == [
        ("workflow.launch", {"template_id": "meeting-prep", "client_id": "codex"}),
    ]


async def test_control_mcp_admission_dispatches_actor_defaults(fake_daemon) -> None:
    client = fake_daemon(
        {
            "mcp.admission.preview": {"server": "github"},
            "mcp.admission.approve": {"server": "github"},
        }
    )

    preview = await dispatch_control_tool(
        client,
        "mcp_admission_preview",
        {"server": "github", "tools": [{"name": "list_issues"}]},
    )
    approve = await dispatch_control_tool(
        client,
        "mcp_admission_approve",
        {"server": "github", "tools": ["list_issues"]},
    )

    assert preview.isError is False
    assert approve.isError is False
    assert client.calls == [
        (
            "mcp.admission.preview",
            {
                "server": "github",
                "tools": [{"name": "list_issues"}],
                "actor": "mcp-control",
            },
        ),
        (
            "mcp.admission.approve",
            {
                "server": "github",
                "tools": ["list_issues"],
                "approved_by": "mcp-control",
            },
        ),
    ]


async def test_control_workstream_claim_dispatches(fake_daemon) -> None:
    client = fake_daemon({"workstream.claim": {"workstream": {"id": "w1"}}})

    result = await dispatch_control_tool(
        client,
        "workstream_claim",
        {
            "session_id": "s1",
            "client_id": "gui",
            "lease_seconds": 120,
            "reason": "interactive edit",
        },
    )

    assert result.isError is False
    assert client.calls == [
        (
            "workstream.claim",
            {
                "session_id": "s1",
                "client_id": "gui",
                "lease_seconds": 120,
                "reason": "interactive edit",
                "workstream_id": None,
                "lease_token": None,
            },
        ),
    ]


async def test_control_workstream_release_client_dispatches(fake_daemon) -> None:
    client = fake_daemon({"workstream.release_client": {"workstreams": []}})

    result = await dispatch_control_tool(
        client,
        "workstream_release_client",
        {"client_id": "gui-a", "reason": "heartbeat lost"},
    )

    assert result.isError is False
    assert client.calls == [
        (
            "workstream.release_client",
            {"client_id": "gui-a", "reason": "heartbeat lost"},
        ),
    ]


async def test_control_workstream_sweep_expired_dispatches(fake_daemon) -> None:
    client = fake_daemon({"workstream.sweep_expired": {"workstreams": []}})

    result = await dispatch_control_tool(client, "workstream_sweep_expired")

    assert result.isError is False
    assert client.calls == [("workstream.sweep_expired", None)]


async def test_control_google_oauth_tools_dispatch_to_generic_daemon_rpc(fake_daemon) -> None:
    client = fake_daemon(
        {
            "setup.google.oauth_status": {"service_id": "google-calendar"},
            "setup.google.oauth_revoke": {"token_configured": False},
        },
    )

    status = await dispatch_control_tool(
        client,
        "google_oauth_status",
        {"service_id": "google-calendar"},
    )
    revoke = await dispatch_control_tool(
        client,
        "google_oauth_revoke",
        {"service_id": "google-drive"},
    )

    assert status.isError is False
    assert revoke.isError is False
    assert client.calls == [
        ("setup.google.oauth_status", {"service_id": "google-calendar"}),
        ("setup.google.oauth_revoke", {"service_id": "google-drive"}),
    ]


async def test_control_onguard_tools_dispatch_to_daemon_rpc(fake_daemon) -> None:
    client = fake_daemon(
        {
            "schedule.create": {"schedule": {"schedule_id": "sched-1"}},
            "client.queue.enqueue": {"command": {"command_id": "cmd-1"}},
            "artifact.promote": {"artifact": {"artifact_id": "art-1", "status": "promoted"}},
        },
    )

    schedule = await dispatch_control_tool(
        client,
        "onguard_schedule_create",
        {
            "schedule_id": "sched-1",
            "client_id": "onguard.digest.daily",
            "command": "build_daily_digest",
            "recurrence": {"kind": "daily", "hour": 7, "minute": 30},
            "payload": {"topics": ["calendar", "mail"]},
            "labels": ["personal.profile"],
        },
    )
    queued = await dispatch_control_tool(
        client,
        "onguard_queue_enqueue",
        {
            "client_id": "onguard.finance.guard",
            "command": "guard_finance_document",
            "payload": {"source": "email"},
            "labels": ["external-untrusted"],
        },
    )
    promoted = await dispatch_control_tool(
        client,
        "onguard_artifact_promote",
        {"artifact_id": "art-1"},
    )

    assert schedule.isError is False
    assert queued.isError is False
    assert promoted.isError is False
    assert client.calls == [
        (
            "schedule.create",
            {
                "schedule_id": "sched-1",
                "client_id": "onguard.digest.daily",
                "command": "build_daily_digest",
                "recurrence": {"kind": "daily", "hour": 7, "minute": 30},
                "payload": {"topics": ["calendar", "mail"]},
                "labels": ["personal.profile"],
                "created_by": "mcp-control",
            },
        ),
        (
            "client.queue.enqueue",
            {
                "client_id": "onguard.finance.guard",
                "command": "guard_finance_document",
                "payload": {"source": "email"},
                "labels": ["external-untrusted"],
            },
        ),
        (
            "artifact.promote",
            {"artifact_id": "art-1", "promoted_by": "mcp-control"},
        ),
    ]


async def test_control_unknown_tool_is_error(fake_daemon) -> None:
    client = fake_daemon({})

    result = await dispatch_control_tool(client, "missing")

    assert result.isError is True
    assert "unknown control tool" in _text(result)


async def test_build_control_server_constructs_server(fake_daemon) -> None:
    server = await build_control_server(fake_daemon({}))

    assert server.name == "capdep-control"
