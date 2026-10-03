"""The daily-driver local baseline must not route into a larger model."""

from pathlib import Path

from capabledeputy.llm.models_config import load_models_config
from capabledeputy.llm.routing import ModelRoutingContext, resolve_model_role
from capabledeputy.mode.dispatcher import ExecutionMode


def test_daily_local_8b_profile_uses_one_model_for_every_role() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_models_config(root / "configs" / "models-daily-local-8b.yaml")
    assert {spec.mlx for spec in config.roles.values()} == {"mlx-community/Qwen3-8B-4bit"}
    assert config.roles["planner.fast"].max_tokens == 512
    assert config.roles["extractor"].max_tokens == 512


def test_daily_local_8b_profile_never_role_escalates() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_models_config(root / "configs" / "models-daily-local-8b.yaml")
    result = resolve_model_role(
        config,
        ModelRoutingContext(
            purpose_handle="writing",
            execution_mode=ExecutionMode.PROGRAMMATIC,
            n_visible_tools=25,
            n_selected_tools=15,
            user_message_chars=10_000,
        ),
    )
    assert result.role == "planner.fast"
    assert result.reason == "daily_local_8b_baseline"
