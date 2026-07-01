"""Tests for models.yaml loading and deterministic model role routing."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.llm.models_config import load_models_config
from capabledeputy.llm.pool import ModelPool
from capabledeputy.llm.routing import ModelRoutingContext, resolve_model_role
from capabledeputy.mode.dispatcher import ExecutionMode
from capabledeputy.agent.loop import _extract_model_role_directive


def test_load_models_config_from_repo_file() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_models_config(root / "configs" / "models.yaml")
    assert config.default_backend == "mlx"
    assert "planner.fast" in config.roles
    assert "planner.quality" in config.roles
    assert "extractor" in config.roles
    assert config.tool_selection.mode == "retrieve"


def test_resolve_fast_by_default() -> None:
    config = load_models_config()
    result = resolve_model_role(
        config,
        ModelRoutingContext(
            purpose_handle="general",
            execution_mode=ExecutionMode.TURN_LEVEL,
            n_visible_tools=5,
        ),
    )
    assert result.role == "planner.fast"


def test_resolve_tools_for_large_surface() -> None:
    config = load_models_config()
    result = resolve_model_role(
        config,
        ModelRoutingContext(
            purpose_handle="general",
            execution_mode=ExecutionMode.TURN_LEVEL,
            n_visible_tools=16,
        ),
    )
    assert result.role == "planner.tools"
    assert result.reason == "large_tool_surface"


def test_resolve_tools_for_programmatic_mode() -> None:
    config = load_models_config()
    result = resolve_model_role(
        config,
        ModelRoutingContext(
            purpose_handle="general",
            execution_mode=ExecutionMode.PROGRAMMATIC,
            n_visible_tools=3,
        ),
    )
    assert result.role == "planner.tools"


def test_resolve_quality_for_long_writing_turn() -> None:
    config = load_models_config()
    result = resolve_model_role(
        config,
        ModelRoutingContext(
            purpose_handle="writing",
            execution_mode=ExecutionMode.TURN_LEVEL,
            n_visible_tools=3,
            n_selected_tools=0,
            user_message_chars=1200,
        ),
    )
    assert result.role == "planner.quality"
    assert result.reason == "long_quality_purpose"


def test_resolve_tools_for_many_selected_tools() -> None:
    config = load_models_config()
    result = resolve_model_role(
        config,
        ModelRoutingContext(
            purpose_handle="general",
            execution_mode=ExecutionMode.TURN_LEVEL,
            n_visible_tools=6,
            n_selected_tools=4,
        ),
    )
    assert result.role == "planner.tools"
    assert result.reason == "multi_tool_turn"


def test_manual_model_directive_is_stripped() -> None:
    message, role = _extract_model_role_directive("/quality make this sharper")
    assert message == "make this sharper"
    assert role == "planner.quality"

    message, role = _extract_model_role_directive("/model tools search and summarize")
    assert message == "search and summarize"
    assert role == "planner.tools"

    message, role = _extract_model_role_directive("plain question")
    assert message == "plain question"
    assert role is None


def test_resolve_manual_quality_override() -> None:
    config = load_models_config()
    result = resolve_model_role(
        config,
        ModelRoutingContext(
            purpose_handle="general",
            execution_mode=ExecutionMode.TURN_LEVEL,
            n_visible_tools=0,
            model_role_override="planner.quality",
        ),
    )
    assert result.role == "planner.quality"
    assert result.reason == "session_override"


def test_model_pool_status_reports_roles() -> None:
    pool = ModelPool.from_config()
    status = pool.status()
    assert status["backend"] == "mlx"
    assert "planner.fast" in status["roles"]
    assert status["tool_selection_mode"] == "retrieve"


def test_model_pool_status_reports_env_model_overrides(monkeypatch) -> None:
    monkeypatch.setenv("CAPDEP_LLM_TOOLS_MODEL", "mlx/custom/tools")
    monkeypatch.setenv("CAPDEP_LLM_QUALITY_MODEL", "mlx/custom/quality")
    pool = ModelPool.from_config()
    status = pool.status()
    assert status["roles"]["planner.tools"]["mlx"] == "custom/tools"
    assert status["roles"]["planner.quality"]["mlx"] == "custom/quality"
