"""Layer-3+ TUI integration coverage for the workflows that were the
real gap: the live security sidebar evolving, the spectator's
session-detail wiring, and drive-loop resilience. All via the shared
scriptable `fake_daemon` (state evolution, RPC failure, scripted
audit events) + Pilot. No socket, no new deps."""

from __future__ import annotations

from typing import Any, cast

from textual.widgets import Input, RichLog, Static

from capabledeputy.ipc.client import DaemonNotRunningError
from capabledeputy.tui.app import (
    CapDepTUI,
    DaemonRPCWorkbenchScreen,
    GoogleWorkspaceSetupScreen,
    SetupAssistantScreen,
    WorkflowLibraryScreen,
)
from capabledeputy.tui.console import CapDepConsole

_SID = "abcd1234-0000-0000-0000-000000000000"


async def _settle(pilot: Any, n: int = 6) -> None:
    for _ in range(n):
        await pilot.pause()


def _text(app: Any, sel: str) -> str:
    """Plain text of a widget's current content."""
    widget = app.query_one(sel)
    if isinstance(widget, RichLog):
        return " ".join(s.text for s in widget.lines)
    return str(widget.render())


def _status(app: CapDepConsole) -> str:
    return _text(app, "#status")


# ---- live security sidebar evolves clean → TAINTED ---------------------


async def test_sidebar_flips_clean_to_tainted_across_a_turn(
    fake_daemon,
) -> None:
    clean = {
        "id": _SID,
        "status": "active",
        "label_set": [],
        "used_kinds": [],
        "capability_set": [],
        "history": [],
    }
    tainted = {
        "id": _SID,
        "status": "active",
        "label_set": ["untrusted.external"],
        "used_kinds": ["READ_FS"],
        "capability_set": [
            {
                "kind": "QUEUE_PURCHASE",
                "pattern": "amazon",
                "expires_at": None,
                "rate_limit": {"max_uses": 2, "window_seconds": 60},
            },
        ],
        "history": [],
    }
    client = fake_daemon().sequence("session.get", [clean, tainted])
    client.set(
        "session.send",
        {
            "content": "read inbox",
            "iterations": 1,
            "finish_reason": "stop",
            "tool_outcomes": [],
        },
    )
    app = CapDepConsole(_SID)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        assert "clean" in _status(app)  # first session.get
        await pilot.press(*"summarise inbox", "enter")
        await _settle(pilot)
        s = _status(app)
        assert "TAINTED" in s  # compartment flipped, live
        assert "untrusted.external" in s
        assert "rate 2/60s" in s  # capability constraint surfaced


# ---- spectator session-detail wiring -----------------------------------


async def test_spectator_session_detail_renders_caps_and_recovery(
    fake_daemon,
) -> None:
    full = {
        "id": _SID,
        "status": "active",
        "intent": "spectator demo",
        "parent": None,
        "label_set": ["untrusted.external"],
        "capability_set": [
            {"kind": "SEND_EMAIL", "pattern": "*", "expires_at": None},
        ],
        "history": [
            {"role": "user", "content": "hi"},
            {"role": "agent", "content": "hello"},
        ],
    }
    events = [
        {
            "timestamp": "2026-05-16T12:00:00",
            "event_type": "policy.decided",
            "session_id": _SID,
            "payload": {
                "decision": "deny",
                "rule": "untrusted-meets-egress",
                "tool": "email.send",
            },
        },
    ]
    client = fake_daemon(
        {
            "session.get": full,
            "session.list": {"sessions": [full]},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": events},
        }
    )
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await app._update_session_detail(_SID, events)
        await _settle(pilot)
        convo = _text(app, "#conversation")
        trace = _text(app, "#trace")
        assert "hello" in convo and "hi" in convo
        assert "TAINTED" in trace  # compartment block
        assert "SEND_EMAIL" in trace  # capability block
        assert "recover:" in trace  # deny recovery hint surfaced


async def test_spectator_renders_onguard_coordination_summary(fake_daemon) -> None:
    client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": []},
            "client.registry.list": {
                "clients": [
                    {
                        "client_id": "onguard.digest.daily",
                        "kind": "onguard",
                        "status": "active",
                    }
                ]
            },
            "client.queue.list": {
                "commands": [{"client_id": "onguard.digest.daily", "status": "queued"}]
            },
            "schedule.list": {
                "schedules": [{"client_id": "onguard.digest.daily", "status": "proposed"}]
            },
            "artifact.list": {
                "artifacts": [{"client_id": "onguard.digest.daily", "status": "draft"}]
            },
            "client.events.list": {
                "events": [{"client_id": "onguard.digest.daily", "event_type": "digest.ready"}]
            },
        }
    )
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        assert app._onguard_summary == [
            {
                "client_id": "onguard.digest.daily",
                "status": "active",
                "queue": 1,
                "schedules": 1,
                "artifacts": 1,
                "events": 1,
            }
        ]


async def test_tui_google_workspace_setup_dispatches_daemon_oauth_rpcs(fake_daemon) -> None:
    client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": []},
            "client.registry.list": {"clients": []},
            "client.queue.list": {"commands": []},
            "schedule.list": {"schedules": []},
            "artifact.list": {"artifacts": []},
            "client.events.list": {"events": []},
            "setup.google.oauth_status": {
                "services": [
                    {
                        "service_id": "google-gmail",
                        "display_name": "Google Gmail",
                        "configured": False,
                        "client_id_configured": False,
                        "client_secret_configured": False,
                        "token_configured": False,
                    }
                ]
            },
            "setup.google.configure_oauth": {
                "service_id": "google-gmail",
                "display_name": "Google Gmail",
                "configured": True,
                "client_id_configured": True,
                "client_secret_configured": True,
                "token_configured": False,
            },
            "setup.google.oauth_login": {
                "service_id": "google-gmail",
                "display_name": "Google Gmail",
                "configured": True,
                "client_id_configured": True,
                "client_secret_configured": True,
                "token_configured": True,
            },
            "setup.google.oauth_revoke": {
                "service_id": "google-gmail",
                "display_name": "Google Gmail",
                "configured": True,
                "client_id_configured": True,
                "client_secret_configured": True,
                "token_configured": False,
            },
        }
    )
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("w")
        await _settle(pilot)

        screen = cast(GoogleWorkspaceSetupScreen, app.screen)
        screen.query_one("#service-id", Input).value = "google-gmail"
        screen.query_one("#client-id", Input).value = "cid"
        screen.query_one("#client-secret", Input).value = "secret"
        screen.action_save_client()
        await _settle(pilot)
        assert screen.query_one("#client-secret", Input).value == ""

        screen.action_login()
        await _settle(pilot)
        screen.action_revoke()
        await _settle(pilot)

    assert ("setup.google.oauth_status", {}) in client.calls
    assert (
        "setup.google.configure_oauth",
        {"service_id": "google-gmail", "client_id": "cid", "client_secret": "secret"},
    ) in client.calls
    assert (
        "setup.google.oauth_login",
        {"service_id": "google-gmail", "open_browser": True, "timeout_seconds": 180},
    ) in client.calls
    assert ("setup.google.oauth_revoke", {"service_id": "google-gmail"}) in client.calls


async def test_tui_google_workspace_setup_renders_status_without_secrets(fake_daemon) -> None:
    client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": []},
            "client.registry.list": {"clients": []},
            "client.queue.list": {"commands": []},
            "schedule.list": {"schedules": []},
            "artifact.list": {"artifacts": []},
            "client.events.list": {"events": []},
            "setup.google.oauth_status": {
                "services": [
                    {
                        "service_id": "google-calendar",
                        "display_name": "Google Calendar",
                        "configured": True,
                        "client_id_configured": True,
                        "client_secret_configured": True,
                        "token_configured": False,
                        "server_yaml": "/tmp/google-calendar.yaml",
                    }
                ]
            },
        }
    )
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("w")
        await _settle(pilot)
        status = _text(app.screen, "#google-status")
        assert "Google Calendar" in status
        assert "client=yes" in status
        assert "token=no" in status
        assert "client_secret" not in status


async def test_tui_daemon_rpc_workbench_calls_arbitrary_daemon_method(fake_daemon) -> None:
    client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": []},
            "client.registry.list": {"clients": []},
            "client.queue.list": {"commands": []},
            "schedule.list": {"schedules": []},
            "artifact.list": {"artifacts": []},
            "client.events.list": {"events": []},
            "policy.validate": {"ok": True, "checked": ["rules"]},
        }
    )
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("m")
        await _settle(pilot)

        screen = cast(DaemonRPCWorkbenchScreen, app.screen)
        screen.query_one("#rpc-method", Input).value = "policy.validate"
        screen.query_one("#rpc-params", Input).value = "{\"strict\": true}"
        screen.action_run_rpc()
        await _settle(pilot)

        result = _text(screen, "#rpc-result")
        assert '"ok": true' in result

    assert ("policy.validate", {"strict": True}) in client.calls


async def test_tui_setup_assistant_renders_daemon_plan(fake_daemon) -> None:
    client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": []},
            "client.registry.list": {"clients": []},
            "client.queue.list": {"commands": []},
            "schedule.list": {"schedules": []},
            "artifact.list": {"artifacts": []},
            "client.events.list": {"events": []},
            "daemon.state": {"workstreams": {"active_count": 0}},
            "setup.plan": {
                "ready": False,
                "workflow_ready": False,
                "first_workflow": {
                    "id": "morning-briefing",
                    "title": "Morning Briefing",
                    "hint": "Resolve blocking setup steps.",
                },
                "summary": {"blocking": 1, "warning": 0, "manual": 0, "ok": 3},
                "checks": [
                    {
                        "id": "daemon",
                        "title": "Daemon",
                        "status": "ok",
                        "detail": "Connected",
                        "actions": [],
                    }
                ],
            },
            "setup.check": {"ok": False, "workflow_ready": False},
            "setup.status": {"checks": [{"id": "daemon", "title": "Daemon", "status": "ok"}]},
        }
    )
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("s")
        await _settle(pilot)
        summary = _text(app.screen, "#setup-summary")
        assert "Morning Briefing" in summary
        assert "check_ok=False" in summary


async def test_tui_workflow_library_lists_templates(fake_daemon) -> None:
    client = fake_daemon(
        {
            "session.list": {"sessions": []},
            "approval.list": {"approvals": []},
            "audit.tail": {"events": []},
            "client.registry.list": {"clients": []},
            "client.queue.list": {"commands": []},
            "schedule.list": {"schedules": []},
            "artifact.list": {"artifacts": []},
            "client.events.list": {"events": []},
            "daemon.state": {"workstreams": {"active_count": 0}},
            "workflow.templates": {
                "templates": [
                    {
                        "id": "morning-briefing",
                        "title": "Morning Briefing",
                        "purpose_handle": "general",
                        "requires_foreground_review": False,
                    }
                ]
            },
        }
    )
    app = CapDepTUI(poll_interval=999.0)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("W")
        await _settle(pilot)
        screen = cast(WorkflowLibraryScreen, app.screen)
        assert screen._templates[0]["id"] == "morning-briefing"


# ---- drive-loop resilience ---------------------------------------------


async def test_console_daemon_down_is_handled(fake_daemon) -> None:
    client = fake_daemon(
        {"session.get": {"id": _SID, "label_set": [], "capability_set": [], "history": []}}
    )
    client.raises("session.send", DaemonNotRunningError)
    app = CapDepConsole(_SID)
    app._client = client
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press(*"hi", "enter")
        await _settle(pilot)
        text = " ".join(s.text for s in app.query_one("#log", RichLog).lines)
        assert "daemon not running" in text  # surfaced, not crashed


async def test_console_rpc_error_is_handled(fake_daemon) -> None:
    client = fake_daemon(
        {"session.get": {"id": _SID, "label_set": [], "capability_set": [], "history": []}}
    )
    client.raises("session.send", RuntimeError)
    app = CapDepConsole(_SID)
    app._client = client
    from textual.widgets import RichLog

    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press(*"hi", "enter")
        await _settle(pilot)
        text = " ".join(s.text for s in app.query_one("#log", RichLog).lines)
        assert "rpc error" in text


async def test_console_empty_input_does_not_send(fake_daemon) -> None:
    client = fake_daemon(
        {"session.get": {"id": _SID, "label_set": [], "capability_set": [], "history": []}}
    )
    app = CapDepConsole(_SID)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("enter")  # empty prompt
        await _settle(pilot)
        assert "session.send" not in [m for m, _ in app._client.calls]


async def test_console_audit_event_triggers_status_refresh(
    fake_daemon,
) -> None:
    client = fake_daemon(
        {"session.get": {"id": _SID, "label_set": [], "capability_set": [], "history": []}}
    )
    client.events(
        [
            {"stream": "audit", "data": {"session_id": _SID, "event_type": "label.propagated"}},
        ]
    )
    app = CapDepConsole(_SID)
    app._client = client
    async with app.run_test() as pilot:
        await _settle(pilot)
        # on_mount refresh + the scripted audit event each call session.get
        n = sum(1 for m, _ in app._client.calls if m == "session.get")
        assert n >= 2
