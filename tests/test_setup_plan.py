from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from capabledeputy.app import App
from capabledeputy.daemon.gui_handlers import make_gui_handlers
from capabledeputy.daemon.setup_plan import (
    FIRST_WORKFLOW_ID,
    _configured_upstreams_check,
    _search_provider_check,
    build_setup_check,
    build_setup_checks,
    build_setup_plan,
    build_setup_steps,
)
from capabledeputy.policy.context import PolicyContext


@pytest.fixture
def app(tmp_path: Path) -> App:
    return App(state_db_path=tmp_path / "state.db", audit_log_path=tmp_path / "audit.jsonl")


async def test_setup_plan_reports_ordered_steps_and_workflow(app: App) -> None:
    handlers = make_gui_handlers(app)

    plan = await handlers["setup.plan"]({})
    check = await handlers["setup.check"]({})

    assert plan["first_workflow"]["id"] == FIRST_WORKFLOW_ID
    assert plan["steps"]
    assert plan["steps"][0]["id"] == "daemon"
    assert plan["summary"]["ok"] >= 1
    assert "checks" in plan
    assert check["first_workflow"] == FIRST_WORKFLOW_ID
    assert check["ready"] == plan["ready"]
    assert check["workflow_ready"] == plan["workflow_ready"]


async def test_setup_status_matches_plan_checks(app: App) -> None:
    handlers = make_gui_handlers(app)

    status = await handlers["setup.status"]({})
    plan = await handlers["setup.plan"]({})

    assert status["checks"] == plan["checks"]


async def test_setup_check_flags_missing_model_as_blocking(app: App) -> None:
    app.llm_client = None

    check = build_setup_check(app)
    plan = build_setup_plan(app)

    assert check["workflow_ready"] is False
    assert "model" in plan["first_workflow"]["blockers"]
    assert any(step["id"] == "model" and step["blocking"] for step in plan["steps"])


async def test_setup_plan_workflow_ready_with_model_and_policy(app: App) -> None:
    from capabledeputy.llm.fake import FakeLLMClient

    app.llm_client = FakeLLMClient([])
    app.policy_context = PolicyContext()

    plan = build_setup_plan(app)

    assert plan["workflow_ready"] is True
    assert plan["first_workflow"]["ready"] is True
    assert plan["first_workflow"]["blockers"] == []


async def test_setup_plan_includes_imap_email_step(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.policy_context = PolicyContext()
    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.imap_credentials_present",
        lambda: False,
    )

    plan = build_setup_plan(app)
    imap_step = next(step for step in plan["steps"] if step["id"] == "imap-email")

    assert imap_step["status"] == "warning"
    assert "imap-setup" in imap_step["detail"]


async def test_setup_plan_reports_failed_configured_mcp_servers(app: App) -> None:
    app.upstream_manager = SimpleNamespace(
        server_status={
            "bundled-search": SimpleNamespace(
                name="bundled-search",
                state="registered",
                registered_tool_count=1,
                rejected_tool_count=0,
                error="",
                transport="stdio",
                url="",
            ),
            "example": SimpleNamespace(
                name="example",
                state="failed",
                registered_tool_count=0,
                rejected_tool_count=0,
                error="[Errno 2] No such file or directory: 'missing-command'",
                transport="stdio",
                url="",
            ),
        },
    )

    plan = build_setup_plan(app)
    check = next(check for check in plan["checks"] if check["id"] == "configured-mcp")
    step = next(step for step in plan["steps"] if step["id"] == "configured-mcp")

    assert check["status"] == "warning"
    assert check["failed_servers"] == ["example"]
    assert "missing-command" in check["detail"]
    assert step["status"] == "warning"


async def test_setup_plan_reports_registered_configured_mcp_servers(app: App) -> None:
    app.upstream_manager = SimpleNamespace(
        server_status={
            "bundled-search": SimpleNamespace(
                name="bundled-search",
                state="registered",
                registered_tool_count=1,
                rejected_tool_count=1,
                error="",
                transport="stdio",
                url="",
            ),
        },
    )

    plan = build_setup_plan(app)
    check = next(check for check in plan["checks"] if check["id"] == "configured-mcp")

    assert check["status"] == "ok"
    assert "1/1 configured MCP server(s) registered" in check["detail"]
    assert "1 tool(s) were rejected by policy classification" in check["detail"]


def test_configured_upstreams_check_no_upstream_is_manual() -> None:
    check = _configured_upstreams_check([])
    assert check["status"] == "manual"


def test_search_provider_check_kagi_registered_is_ok() -> None:
    upstream = [{"name": "kagi", "state": "registered"}]
    check = _search_provider_check(upstream)
    assert check["status"] == "ok"
    assert "Kagi" in check["detail"]


def test_search_provider_check_brave_and_bundled_registered_is_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-key")
    upstream = [{"name": "bundled-search", "state": "registered"}]
    check = _search_provider_check(upstream)
    assert check["status"] == "ok"
    assert "Brave" in check["detail"]


def test_search_provider_check_kagi_key_without_uvx_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAGI_API_KEY", "kagi-key")
    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.uvx_spawn_command",
        # uvx_spawn_command() falls back to the bare unresolved ["uvx"]
        # literal when it can't find a real uvx binary anywhere — that's
        # the "not available" sentinel (see cli/_managed_config.py).
        lambda: ["uvx"],
    )
    check = _search_provider_check([])
    assert check["status"] == "warning"
    assert "uvx is missing" in check["detail"]


def test_search_provider_check_kagi_failed_reports_error() -> None:
    upstream = [{"name": "kagi", "state": "failed", "error": "boom"}]
    check = _search_provider_check(upstream)
    assert check["status"] == "warning"
    assert "boom" in check["detail"]


def test_search_provider_check_credential_vault_error_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(_path: object) -> None:
        raise RuntimeError("vault unreadable")

    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.load_credential_vault",
        _raise,
    )
    # Doesn't raise — the vault lookup failure is swallowed and treated as
    # "no Kagi key from the vault", falling through to the default warning.
    check = _search_provider_check([])
    assert check["status"] == "warning"


def test_build_setup_steps_skips_unknown_step_ids() -> None:
    steps = build_setup_steps([{"id": "daemon", "status": "ok"}])
    assert [step["id"] for step in steps] == ["daemon"]


async def test_setup_plan_omits_first_workflow_when_catalog_empty(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.first_workflow_template",
        lambda: None,
    )

    plan = build_setup_plan(app)

    assert "first_workflow" not in plan


async def test_build_setup_checks_model_detail_notes_mlx_unavailable(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from capabledeputy.llm.fake import FakeLLMClient

    app.llm_client = FakeLLMClient([])
    monkeypatch.setattr("capabledeputy.daemon.setup_plan.mlx_metal_available", lambda: False)
    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.resolve_planner_model_spec",
        lambda *, prefer_local_mlx=True: "mlx/some-model",
    )
    from capabledeputy.daemon.settings_store import DaemonSettings

    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.load_settings",
        lambda: DaemonSettings(prefer_local_mlx=True),
    )

    checks = build_setup_checks(app)
    model_check = next(c for c in checks if c["id"] == "model")

    assert "MLX Metal unavailable" in model_check["detail"]


async def test_build_setup_checks_model_detail_notes_ollama_fallback(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from capabledeputy.llm.fake import FakeLLMClient

    app.llm_client = FakeLLMClient([])
    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.resolve_planner_model_spec",
        lambda *, prefer_local_mlx=True: "ollama/some-model",
    )
    monkeypatch.setattr("capabledeputy.daemon.setup_plan.ollama_reachable", lambda: True)
    from capabledeputy.daemon.settings_store import DaemonSettings

    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.load_settings",
        lambda: DaemonSettings(prefer_local_mlx=False),
    )

    checks = build_setup_checks(app)
    model_check = next(c for c in checks if c["id"] == "model")

    assert "using local Ollama planner" in model_check["detail"]


async def test_setup_plan_imap_loaded_with_credentials_is_ok(
    app: App,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.policy_context = PolicyContext()
    app.upstream_manager = SimpleNamespace(
        server_status={
            "mail": SimpleNamespace(
                name="mail",
                state="registered",
                registered_tool_count=1,
                rejected_tool_count=0,
                error="",
                transport="stdio",
                url="",
            ),
        },
    )
    monkeypatch.setattr(
        "capabledeputy.daemon.setup_plan.imap_credentials_present",
        lambda: True,
    )

    plan = build_setup_plan(app)
    imap_step = next(step for step in plan["steps"] if step["id"] == "imap-email")

    assert imap_step["status"] == "ok"
    assert "saved credentials" in imap_step["detail"]
