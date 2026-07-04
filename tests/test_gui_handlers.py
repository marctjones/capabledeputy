import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from capabledeputy.app import App
from capabledeputy.approval.model import ApprovalAction
from capabledeputy.artifacts import ArtifactEffect, ArtifactType, TypedArtifact
from capabledeputy.audit.events import Event, EventType
from capabledeputy.daemon.gui_handlers import make_gui_handlers


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_app_status_reports_daemon_gui_summary(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="hello", purpose_handle="inbox")
    await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.SEND_EMAIL,
        payload="hi",
        target="me@example.com",
        justification="test",
    )

    result = await handlers["app.status"]({})

    assert result["daemon"]["connected"] is True
    assert result["daemon"]["session_count"] == 1
    assert result["daemon"]["pending_approval_count"] == 1
    assert result["daemon"]["tool_count"] > 0


async def test_setup_status_reports_actionable_checks(app: App) -> None:
    handlers = make_gui_handlers(app)

    result = await handlers["setup.status"]({})

    check_ids = {check["id"] for check in result["checks"]}
    assert {
        "daemon",
        "model",
        "relationship-groups",
        "apple-automation",
        "daemon-settings",
        "config-validation",
    } <= check_ids


async def test_policy_explain_returns_plain_english_for_recent_decision(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="policy")
    await app.audit.write(
        Event(
            event_type=EventType.POLICY_DECIDED,
            session_id=session.id,
            payload={
                "decision": "deny",
                "rule": "no matching capability",
                "reason": "no matching capability for SEND_EMAIL",
            },
        ),
    )

    result = await handlers["policy.explain"]({"session_id": str(session.id)})

    assert result["found"] is True
    assert result["decision"] == "deny"
    assert "not granted authority" in result["plain_english"]


async def test_policy_explain_reports_missing_decision(app: App) -> None:
    handlers = make_gui_handlers(app)

    result = await handlers["policy.explain"]({"session_id": "missing"})

    assert result == {"found": False, "message": "No matching policy decision found."}


async def test_policy_explain_covers_common_rule_explanations(app: App) -> None:
    handlers = make_gui_handlers(app)
    cases = [
        ("allow", "safe", "all checks passed", "allowed"),
        ("deny", "blocked-egress", "external destination", "external destination"),
        ("deny", "biba", "untrusted input", "lower-integrity"),
        ("deny", "brewer-nash", "conflict detected", "conflicting compartments"),
        ("require_approval", "operator approval", "manual review", "explicit human approval"),
    ]
    for decision, rule, reason, expected in cases:
        session = await app.graph.new(intent=f"policy-{rule}")
        await app.audit.write(
            Event(
                event_type=EventType.POLICY_DECIDED,
                session_id=session.id,
                payload={"decision": decision, "rule": rule, "reason": reason},
            ),
        )

        result = await handlers["policy.explain"]({"session_id": str(session.id)})

        assert expected in result["plain_english"]


async def test_approval_detail_reports_daemon_action_guidance(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="approval")
    await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.SEND_EMAIL,
        payload="hello",
        target="spouse@example.com",
        justification="trusted recurring recipient",
        rule="untrusted-meets-egress",
    )
    second = await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.SEND_EMAIL,
        payload="second",
        target="spouse@example.com",
        justification="same target",
    )

    result = await handlers["approval.detail"]({"id": second.id})

    assert result["approval"]["id"] == second.id
    assert result["approval"]["action"] == "SEND_EMAIL"
    assert "Send an email" in result["effect_text"]
    assert result["sibling_group"]["id"] == str(second.sibling_group_id)
    assert result["sibling_group"]["approvable"] is True
    assert {"deny", "defer", "narrow-pattern"} <= {
        action["id"] for action in result["suggested_actions"]
    }


async def test_approval_detail_includes_typed_review_artifact(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="artifact approval")
    artifact = TypedArtifact(
        artifact_type=ArtifactType.EMAIL_DRAFT,
        title="Reply draft",
        content="Hello from the reviewed draft.",
        target="friend@example.com",
        destination_id="gmail:recipient:friend@example.com",
        effect=ArtifactEffect.SEND,
    )
    approval = await app.approval_queue.submit(
        from_session=session.id,
        action=ApprovalAction.SEND_EMAIL,
        payload=json.dumps(artifact.to_dict()),
        target=artifact.destination_id,
        justification="review exact draft",
    )

    result = await handlers["approval.detail"]({"id": approval.id})

    review = result["review_artifact"]
    assert review["artifact_type"] == "email_draft"
    assert review["destination_id"] == "gmail:recipient:friend@example.com"
    assert review["sha256"] == artifact.sha256
    assert review["preview"] == "Hello from the reviewed draft."


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (ApprovalAction.QUEUE_PURCHASE, "Queue a purchase"),
        (ApprovalAction.EXECUTE_DESTRUCTIVE, "Execute a destructive operation"),
        (ApprovalAction.DECLASSIFY, "explicitly approved for release"),
        (ApprovalAction.GRANT, "Grant scoped authority"),
        (ApprovalAction.MERGE, "Merge session state"),
    ],
)
async def test_approval_detail_reports_effect_text_for_non_email_actions(
    app: App,
    action: ApprovalAction,
    expected: str,
) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent=f"approval-{action.value}")
    approval = await app.approval_queue.submit(
        from_session=session.id,
        action=action,
        payload="payload",
        target="target",
        justification="requires operator review",
    )

    result = await handlers["approval.detail"]({"id": approval.id})

    assert expected in result["effect_text"]
    assert result["suggested_actions"][-1]["id"] == "narrow-pattern"


async def test_provenance_graph_materializes_nodes_and_edges(app: App) -> None:
    handlers = make_gui_handlers(app)
    session = await app.graph.new(intent="prov")
    await app.audit.write(
        Event(
            event_type=EventType.PROVENANCE_NODE,
            session_id=session.id,
            payload={"node_id": "a", "kind": "source"},
        ),
    )
    await app.audit.write(
        Event(
            event_type=EventType.PROVENANCE_NODE,
            session_id=session.id,
            payload={"node_id": "b", "kind": "tool_result"},
        ),
    )
    await app.audit.write(
        Event(
            event_type=EventType.PROVENANCE_EDGE,
            session_id=session.id,
            payload={"from_node_id": "a", "to_node_id": "b", "kind": "input"},
        ),
    )

    result = await handlers["provenance.graph"]({"session_id": str(session.id)})

    assert {node["id"] for node in result["nodes"]} == {"a", "b"}
    assert result["edges"] == [{"from": "a", "to": "b", "kind": "input", "metadata": {}}]


async def test_google_oauth_gui_handlers_are_daemon_owned(
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    handlers = make_gui_handlers(app)

    async def fake_login(
        service_id: str,
        *,
        open_browser: bool,
        timeout_seconds: int,
    ) -> dict[str, object]:
        assert service_id == "google-calendar"
        assert open_browser is False
        assert timeout_seconds == 3
        return {
            "service_id": service_id,
            "server": service_id,
            "display_name": "Google Calendar",
            "configured": True,
            "client_id_configured": True,
            "client_secret_configured": True,
            "token_configured": True,
            "token_cache": str(tmp_path / "xdg" / "capdep" / "calendar.token"),
        }

    monkeypatch.setattr("capabledeputy.daemon.gui_handlers.run_google_oauth_login", fake_login)

    all_status = await handlers["setup.google.oauth_status"]({})
    configured = await handlers["setup.google.configure_oauth"](
        {
            "service_id": "google-calendar",
            "client_id": "client-id",
            "client_secret": "client-secret",
        },
    )
    token_cache = Path(configured["token_cache"])
    token_cache.parent.mkdir(parents=True, exist_ok=True)
    token_cache.write_text("{}", encoding="utf-8")
    single_status = await handlers["setup.google.oauth_status"](
        {"service_id": "google-calendar"},
    )
    logged_in = await handlers["setup.google.oauth_login"](
        {
            "service_id": "google-calendar",
            "open_browser": False,
            "timeout_seconds": 3,
        },
    )
    revoked = await handlers["setup.google.oauth_revoke"]({"service_id": "google-calendar"})

    assert {service["service_id"] for service in all_status["services"]} >= {
        "google-gmail",
        "google-calendar",
        "google-drive",
    }
    assert {preset["id"] for preset in all_status["presets"]} >= {
        "gmail",
        "gmail-calendar",
        "workspace",
    }
    assert configured["client_secret_configured"] is True
    assert single_status["token_configured"] is True
    assert logged_in["token_configured"] is True
    assert revoked["token_configured"] is False
    events = await app.audit.read_all()
    assert [event.payload["action"] for event in events[-3:]] == [
        "google.configure_oauth",
        "google.oauth_login",
        "google.oauth_revoke",
    ]


async def test_google_oauth_status_reports_runtime_reload_state(
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    app.upstream_manager = SimpleNamespace(
        server_status={
            "google-gmail": SimpleNamespace(state="registered"),
        },
    )
    handlers = make_gui_handlers(app)

    gmail = await handlers["setup.google.oauth_status"]({"service_id": "google-gmail"})
    calendar = await handlers["setup.google.oauth_status"]({"service_id": "google-calendar"})

    assert gmail["reload_state"]["registered"] is True
    assert gmail["restart_required"] is False
    assert calendar["reload_state"]["registered"] is False
    assert calendar["restart_required"] is True


async def test_google_oauth_login_and_revoke_reload_running_manager(
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    calls: list[tuple[str, str]] = []

    class FakeManager:
        def __init__(self) -> None:
            self.server_status = {}

        async def reload_server(self, config) -> None:
            calls.append(("reload", config.name))
            self.server_status[config.name] = SimpleNamespace(state="registered")

        async def unload_server(self, name: str) -> None:
            calls.append(("unload", name))
            self.server_status.pop(name, None)

    async def fake_login(
        service_id: str,
        *,
        open_browser: bool,
        timeout_seconds: int,
    ) -> dict[str, object]:
        status = configure_google_oauth_client(
            service_id,
            client_id="cid",
            client_secret="secret",
        )
        token_cache = Path(status["token_cache"])
        token_cache.parent.mkdir(parents=True, exist_ok=True)
        token_cache.write_text('{"access_token": "redacted"}', encoding="utf-8")
        return {**status, "token_configured": True}

    from capabledeputy.daemon.google_gmail_setup import configure_google_oauth_client

    app.upstream_manager = FakeManager()
    handlers = make_gui_handlers(app)
    monkeypatch.setattr("capabledeputy.daemon.gui_handlers.run_google_oauth_login", fake_login)

    logged_in = await handlers["setup.google.oauth_login"](
        {"service_id": "google-drive", "open_browser": False},
    )
    revoked = await handlers["setup.google.oauth_revoke"]({"service_id": "google-drive"})

    assert calls == [("reload", "google-drive"), ("unload", "google-drive")]
    assert logged_in["reload_state"]["registered"] is True
    assert revoked["reload_state"]["registered"] is False


async def test_legacy_gmail_oauth_gui_handlers_remain_available(
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    handlers = make_gui_handlers(app)

    async def fake_login(*, open_browser: bool, timeout_seconds: int) -> dict[str, object]:
        assert open_browser is False
        assert timeout_seconds == 5
        return {
            "server": "google-gmail",
            "service_id": "google-gmail",
            "display_name": "Google Gmail",
            "configured": True,
            "client_id_configured": True,
            "client_secret_configured": True,
            "token_configured": True,
            "token_cache": str(tmp_path / "xdg" / "capdep" / "gmail.token"),
        }

    monkeypatch.setattr("capabledeputy.daemon.gui_handlers.run_gmail_oauth_login", fake_login)

    configured = await handlers["setup.google_gmail.configure_oauth"](
        {"client_id": "client-id", "client_secret": "client-secret"},
    )
    status = await handlers["setup.google_gmail.oauth_status"]({})
    logged_in = await handlers["setup.google_gmail.oauth_login"](
        {"open_browser": False, "timeout_seconds": 5},
    )

    assert configured["client_id_configured"] is True
    assert status["client_secret_configured"] is True
    assert logged_in["token_configured"] is True


async def test_setup_status_summarizes_google_workspace_loaded(
    app: App,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    handlers = make_gui_handlers(app)
    statuses = []
    for service_id in ("google-gmail", "google-calendar", "google-drive"):
        status = await handlers["setup.google.configure_oauth"](
            {
                "service_id": service_id,
                "client_id": f"{service_id}-id",
                "client_secret": f"{service_id}-secret",
            },
        )
        token_cache = Path(status["token_cache"])
        token_cache.parent.mkdir(parents=True, exist_ok=True)
        token_cache.write_text("{}", encoding="utf-8")
        statuses.append(status)
    monkeypatch.setattr(
        app,
        "upstream_manager",
        SimpleNamespace(
            server_status={
                service_id: SimpleNamespace(
                    name=service_id,
                    state="running",
                    registered_tool_count=1,
                    rejected_tool_count=0,
                    error="",
                    transport="streamable-http",
                    url="https://example.invalid/mcp",
                )
                for service_id in ("google-gmail", "google-calendar", "google-drive")
            },
        ),
        raising=False,
    )

    result = await handlers["setup.status"]({})
    google_check = next(check for check in result["checks"] if check["id"] == "google-oauth")

    assert google_check["status"] == "ok"
    assert google_check["detail"] == (
        "Google Workspace MCP connectors are configured, authorized, and loaded."
    )
    assert all(action["enabled"] for action in google_check["actions"])


async def test_macos_frontmost_context_reports_unavailable_off_macos(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    handlers = make_gui_handlers(app)

    result = await handlers["macos.frontmost_context"]({})

    assert result == {"available": False, "reason": "not macOS", "chips": []}
