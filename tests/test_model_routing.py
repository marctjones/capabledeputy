"""Tests for models.yaml loading and deterministic model role routing."""

from __future__ import annotations

from pathlib import Path

from capabledeputy.llm.models_config import load_models_config
from capabledeputy.llm.pool import ModelPool
from capabledeputy.llm.routing import ModelRoutingContext, resolve_model_role
from capabledeputy.mode.dispatcher import ExecutionMode


def test_load_models_config_from_repo_file() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_models_config(root / "configs" / "models.yaml")
    assert config.default_backend == "mlx"
    assert "planner.fast" in config.roles
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


def test_model_pool_status_reports_roles() -> None:
    pool = ModelPool.from_config()
    status = pool.status()
    assert status["backend"] == "mlx"
    assert "planner.fast" in status["roles"]
    assert status["tool_selection_mode"] == "retrieve"