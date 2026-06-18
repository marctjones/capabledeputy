from __future__ import annotations

from capabledeputy.llm.factory import (
    default_llm_model_spec,
    make_llm_client,
    mlx_enable_thinking,
)
from capabledeputy.llm.litellm_client import LiteLLMClient
from capabledeputy.llm.mlx_client import DEFAULT_MLX_MODEL, MLXLLMClient


def test_default_model_spec_prefers_mlx_on_macos(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    assert default_llm_model_spec() == f"mlx/{DEFAULT_MLX_MODEL}"


def test_default_model_spec_preserves_anthropic_on_intel_macos(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    assert default_llm_model_spec() == "claude-haiku-4-5"


def test_default_model_spec_preserves_anthropic_elsewhere(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    assert default_llm_model_spec() == "claude-haiku-4-5"


def test_make_llm_client_builds_mlx_client(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    client = make_llm_client("mlx/some-model")
    assert isinstance(client, MLXLLMClient)
    assert client._enable_thinking is False


def test_make_llm_client_builds_litellm_client(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    client = make_llm_client("claude-haiku-4-5")
    assert isinstance(client, LiteLLMClient)


def test_mlx_enable_thinking_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_MLX_ENABLE_THINKING", raising=False)
    assert mlx_enable_thinking() is False


def test_mlx_enable_thinking_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("CAPDEP_MLX_ENABLE_THINKING", "true")
    assert mlx_enable_thinking() is True


def test_make_llm_client_threads_thinking_env(monkeypatch) -> None:
    monkeypatch.delenv("CAPDEP_LLM_BACKEND", raising=False)
    monkeypatch.setenv("CAPDEP_MLX_ENABLE_THINKING", "1")
    client = make_llm_client("mlx/some-model")
    assert isinstance(client, MLXLLMClient)
    assert client._enable_thinking is True


def test_make_llm_client_backend_mlx_accepts_plain_model(monkeypatch) -> None:
    monkeypatch.setenv("CAPDEP_LLM_BACKEND", "mlx")
    client = make_llm_client("some-model")
    assert isinstance(client, MLXLLMClient)


def test_make_llm_client_backend_litellm_uses_api_default_on_macos(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.machine", lambda: "arm64")
    monkeypatch.setenv("CAPDEP_LLM_BACKEND", "litellm")
    monkeypatch.delenv("CAPDEP_LLM_MODEL", raising=False)
    client = make_llm_client(None)
    assert isinstance(client, LiteLLMClient)
    assert client._model == "claude-haiku-4-5"
