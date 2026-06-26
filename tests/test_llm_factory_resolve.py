from __future__ import annotations

import pytest

from capabledeputy.llm import factory


def test_resolve_planner_model_spec_honors_explicit_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CAPDEP_LLM_MODEL", "claude-haiku-4-5")
    assert factory.resolve_planner_model_spec(prefer_local_mlx=True) == "claude-haiku-4-5"


def test_resolve_planner_model_spec_prefers_ollama_when_mlx_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CAPDEP_LLM_MODEL", raising=False)
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    monkeypatch.setattr(factory, "mlx_metal_available", lambda: True)
    monkeypatch.setattr(factory, "ollama_reachable", lambda: True)

    assert factory.resolve_planner_model_spec(prefer_local_mlx=False) == "ollama/phi4:latest"