from pathlib import Path

import pytest

from capabledeputy.llm.models_config import load_models_config


def test_smoke_override_prevents_large_role_fallback(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("CAPDEP_MODELS_CONFIG", str(root / "configs/models-smoke.yaml"))
    config = load_models_config(root / "configs/models.yaml")
    assert set(config.roles) == {
        "planner.fast",
        "planner.tools",
        "planner.quality",
        "planner.coder",
        "extractor",
    }
    assert all(
        spec.mlx == "mlx-community/Qwen2.5-0.5B-Instruct-4bit" and spec.max_tokens == 128
        for spec in config.roles.values()
    )


def test_missing_explicit_profile_does_not_load_large_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("CAPDEP_MODELS_CONFIG", str(tmp_path / "missing.yaml"))
    with pytest.raises(FileNotFoundError):
        load_models_config()
