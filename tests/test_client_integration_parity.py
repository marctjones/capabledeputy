from __future__ import annotations

from pathlib import Path

from anyio.to_thread import run_sync
from typer.testing import CliRunner

from capabledeputy.cli.main import app
from capabledeputy.mcp_server.control import dispatch_control_tool
from capabledeputy.tui.app import CapDepTUI
from capabledeputy.tui.console import CapDepConsole
from tests.daemon_integration import running_daemon

runner = CliRunner()


async def _invoke_cli(args: list[str]):
    return await run_sync(lambda: runner.invoke(app, args))


async def test_mcp_control_onguard_tools_use_live_daemon(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as daemon:
        registered = await dispatch_control_tool(
            daemon.client,
            "onguard_registry_register",
            {
                "client_id": "onguard.digest.daily",
                "owner": "operator",
                "version": "test",
            },
        )
        assert registered.isError is False

        scheduled = await dispatch_control_tool(
            daemon.client,
            "onguard_schedule_create",
            {
                "schedule_id": "daily-digest-test",
                "client_id": "onguard.digest.daily",
                "command": "build_daily_digest",
                "recurrence": {"kind": "manual"},
                "payload": {"topics": ["calendar", "mail"]},
                "labels": ["personal.profile"],
            },
        )
        assert scheduled.isError is False
        assert scheduled.structuredContent is not None
        schedule = scheduled.structuredContent["schedule"]
        assert schedule["client_id"] == "onguard.digest.daily"
        assert schedule["created_by"] == "mcp-control"

        queued = await dispatch_control_tool(
            daemon.client,
            "onguard_queue_enqueue",
            {
                "client_id": "onguard.digest.daily",
                "command": "build_daily_digest",
                "payload": {"topics": ["calendar", "mail"]},
                "labels": ["personal.profile"],
            },
        )
        assert queued.isError is False
        assert queued.structuredContent is not None
        command = queued.structuredContent["command"]
        assert command["status"] == "queued"
        assert command["labels"] == ["personal.profile"]

        listed = await daemon.client.call(
            "client.queue.list",
            {"client_id": "onguard.digest.daily"},
        )
        assert listed["commands"][0]["command_id"] == command["command_id"]


async def test_security_context_is_available_to_cli_and_mcp_control(
    tmp_path: Path,
) -> None:
    async with running_daemon(tmp_path) as daemon:
        socket_arg = str(daemon.paths.socket)
        created = await daemon.client.call(
            "session.new",
            {
                "owner": "operator",
                "intent": "security context parity",
                "purpose_handle": "personal_assistant",
            },
        )
        await daemon.client.call(
            "approval.submit",
            {
                "from_session": created["id"],
                "action": "SEND_EMAIL",
                "payload": '{"to":"user@example.com"}',
                "target": "user@example.com",
                "labels_in": ["untrusted.external"],
                "justification": "requires approval",
            },
        )

        cli = await _invoke_cli(
            [
                "session",
                "security-context",
                created["id"],
                "--socket",
                socket_arg,
                "--json",
            ],
        )
        mcp = await dispatch_control_tool(
            daemon.client,
            "session_security_context",
            {"session_id": created["id"]},
        )

        assert cli.exit_code == 0
        assert '"schema_version": 1' in cli.stdout
        assert '"pending_count": 1' in cli.stdout
        assert mcp.isError is False
        assert mcp.structuredContent is not None
        assert mcp.structuredContent["session"]["id"] == created["id"]
        assert mcp.structuredContent["approvals"]["pending_count"] == 1


async def test_cli_onguard_read_paths_use_live_daemon(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as daemon:
        socket_arg = str(daemon.paths.socket)
        await daemon.client.call(
            "client.registry.register",
            {
                "client_id": "onguard.finance.guard",
                "kind": "onguard",
                "owner": "operator",
            },
        )
        await daemon.client.call(
            "client.queue.enqueue",
            {
                "client_id": "onguard.finance.guard",
                "command": "guard_finance_document",
                "payload": {"source": "email"},
                "labels": ["external-untrusted", "finance"],
            },
        )
        await daemon.client.call(
            "schedule.create",
            {
                "schedule_id": "finance-guard-test",
                "client_id": "onguard.finance.guard",
                "command": "guard_finance_document",
                "recurrence": {"kind": "manual"},
                "payload": {"source": "email"},
                "created_by": "test",
            },
        )
        await daemon.client.call(
            "artifact.create",
            {
                "client_id": "onguard.finance.guard",
                "artifact_type": "finance.quarantine",
                "payload": {"reason": "untrusted_email_statement"},
                "labels": ["external-untrusted", "finance"],
            },
        )

        clients = await _invoke_cli(["onguard", "clients", "--socket", socket_arg])
        queue = await _invoke_cli(
            [
                "onguard",
                "queue",
                "--socket",
                socket_arg,
                "--client-id",
                "onguard.finance.guard",
            ]
        )
        schedules = await _invoke_cli(
            [
                "onguard",
                "schedules",
                "--socket",
                socket_arg,
                "--client-id",
                "onguard.finance.guard",
            ]
        )
        artifacts = await _invoke_cli(
            [
                "onguard",
                "artifacts",
                "--socket",
                socket_arg,
                "--client-id",
                "onguard.finance.guard",
            ]
        )

        assert clients.exit_code == 0
        assert "onguard.finance.guard kind=onguard status=active" in clients.stdout
        assert queue.exit_code == 0
        assert "guard_finance_document status=queued" in queue.stdout
        assert schedules.exit_code == 0
        assert "client=onguard.finance.guard" in schedules.stdout
        assert "recurrence={'kind': 'manual'}" in schedules.stdout
        assert "status=proposed" in schedules.stdout
        assert artifacts.exit_code == 0
        assert "type=finance.quarantine status=draft" in artifacts.stdout


async def test_tui_console_mounts_against_live_daemon(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as daemon:
        created = await daemon.client.call(
            "session.new",
            {"owner": "operator", "intent": "live tui integration"},
        )
        app = CapDepConsole(created["id"])
        app._client = daemon.client

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#log") is not None
            assert app.query_one("#status") is not None
            assert app.query_one("#prompt") is not None
            status = str(app.query_one("#status").render())
            assert "compartment" in status


async def test_tui_spectator_mounts_against_live_daemon(tmp_path: Path) -> None:
    async with running_daemon(tmp_path) as daemon:
        await daemon.client.call(
            "session.new",
            {"owner": "operator", "intent": "live spectator integration"},
        )
        app = CapDepTUI(poll_interval=999.0)
        app._client = daemon.client

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#sessions") is not None
            assert app.query_one("#approvals") is not None
            assert app.query_one("#events") is not None
            assert app._sessions
