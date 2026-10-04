"""The daily-driver local baseline must not route into a larger model."""

import os
import subprocess
import sys
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


def test_daily_local_wrapper_preserves_role_model_pool() -> None:
    root = Path(__file__).resolve().parents[1]
    wrapper = root / "scripts" / "with-daily-local-8b.sh"
    result = subprocess.run(
        [
            "sh",
            str(wrapper),
            sys.executable,
            "-c",
            (
                "import os; print(os.environ.get('CAPDEP_LLM_BACKEND', '')); "
                "print(os.environ['CAPDEP_MODELS_CONFIG'])"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "CAPDEP_LLM_BACKEND": "litellm"},
    )
    backend, config_path = result.stdout.splitlines()
    assert backend == ""
    assert config_path == str(root / "configs" / "models-daily-local-8b.yaml")


def test_daily_local_wrapper_honors_explicit_cached_model_path(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    wrapper = root / "scripts" / "with-daily-local-8b.sh"
    model_dir = tmp_path / "Qwen3-8B-4bit-mlx"
    model_dir.mkdir()
    result = subprocess.run(
        [
            "sh",
            str(wrapper),
            sys.executable,
            "-c",
            "import os; print(os.environ['CAPDEP_LLM_MODEL'])",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "CAPDEP_DAILY_MLX_MODEL": str(model_dir)},
    )
    assert result.stdout.strip() == str(model_dir)
