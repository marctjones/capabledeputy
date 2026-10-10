"""The Apple Silicon local-only guard must be an allowlist, not a denylist.

A hosted planner receives everything that enters the planner context (mail
bodies, document text). On Apple Silicon the daemon therefore refuses any
non-local model unless the operator explicitly opts in with
``CAPDEP_ALLOW_REMOTE_LLM=1``.
"""

from __future__ import annotations

import pytest

from capabledeputy.llm import pool
from capabledeputy.llm.pool import require_mlx_on_apple_silicon


@pytest.fixture(autouse=True)
def _apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    monkeypatch.delenv("CAPDEP_ALLOW_REMOTE_LLM", raising=False)
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    monkeypatch.delenv("CAPDEP_LLM_MODEL", raising=False)


@pytest.mark.parametrize(
    "model",
    [
        "fireworks_ai/accounts/fireworks/models/qwen3-235b-a22b",
        "openai/gpt-4o",
        "gemini/gemini-2.5-pro",
        "together_ai/meta-llama/Llama-3-70b",
        "groq/llama-3.3-70b",
        "ollama/phi4:latest",
        "claude-haiku-4-5",
        "anthropic/claude-sonnet",
    ],
)
def test_hosted_or_unrecognised_models_are_refused(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    monkeypatch.setenv("CAPDEP_LLM_MODEL", model)
    with pytest.raises(RuntimeError, match="CAPDEP_ALLOW_REMOTE_LLM"):
        require_mlx_on_apple_silicon()


@pytest.mark.parametrize(
    "model",
    [
        "mlx/Qwen/Qwen3-4B-MLX-4bit",
        "mlx-community/Qwen3-14B-4bit",
        "Qwen/Qwen3-4B-MLX-4bit",
        "/Users/someone/models/qwen3",
        "~/models/qwen3",
        "./models/qwen3",
    ],
)
def test_local_model_specs_are_accepted(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    monkeypatch.setenv("CAPDEP_LLM_MODEL", model)
    require_mlx_on_apple_silicon()


def test_unset_model_is_accepted() -> None:
    require_mlx_on_apple_silicon()


@pytest.mark.parametrize("backend", ["litellm", "api", "anthropic", "claude-cli", "cli"])
def test_remote_backends_are_refused(monkeypatch: pytest.MonkeyPatch, backend: str) -> None:
    monkeypatch.setenv("CAPDEP_LLM_BACKEND", backend)
    with pytest.raises(RuntimeError, match="remote planner"):
        require_mlx_on_apple_silicon()


@pytest.mark.parametrize("flag", ["1", "true", "yes", "on"])
def test_explicit_opt_in_allows_hosted_models(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.setenv("CAPDEP_ALLOW_REMOTE_LLM", flag)
    monkeypatch.setenv("CAPDEP_LLM_MODEL", "fireworks_ai/accounts/fireworks/models/x")
    require_mlx_on_apple_silicon()


def test_guard_is_inactive_off_apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setenv("CAPDEP_LLM_MODEL", "openai/gpt-4o")
    require_mlx_on_apple_silicon()


def test_is_local_model_spec_helper_is_exported() -> None:
    assert pool.is_local_model_spec("mlx/x")
    assert not pool.is_local_model_spec("fireworks_ai/x")
