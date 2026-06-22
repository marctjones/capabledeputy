from __future__ import annotations

from pathlib import Path

import anyio
from typer.testing import CliRunner

from capabledeputy.cli.main import app
from capabledeputy.mcp_server.control import dispatch_control_tool
from tests.daemon_integration import running_daemon

runner = CliRunner()


async def _invoke_cli(args: list[str]):
    return await anyio.to_thread.run_sync(lambda: runner.invoke(app, args))


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
