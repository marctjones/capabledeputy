"""Select and construct the planning LLM backend."""

from __future__ import annotations

import os
import urllib.error
import urllib.request

from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.mlx_client import DEFAULT_MLX_MODEL

_CLAUDE_CLI_ALIASES = frozenset({"claude-cli", "claude", "cli", "subscription"})
_MLX_ALIASES = frozenset({"mlx", "metal", "local-mlx"})
_LITELLM_ALIASES = frozenset({"litellm", "api", "anthropic"})
_DEFAULT_OLLAMA_MODEL = "ollama/phi4:latest"


def mlx_metal_available() -> bool:
    """Return True when MLX can see an Apple Metal device."""
    try:
        import mlx.core as mx  # type: ignore[import-not-found]

        return bool(mx.metal.is_available())
    except Exception:
        return False


def ollama_reachable() -> bool:
    """Return True when a local Ollama daemon responds on the default port."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.0) as response:
            return response.status < 400
    except (OSError, urllib.error.URLError, ValueError):
        return False


def default_llm_model_spec() -> str:
    """Default model spec when the operator did not explicitly choose one."""
    import platform

    if platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}:
        return f"mlx/{DEFAULT_MLX_MODEL}"
    return "claude-haiku-4-5"


def resolve_planner_model_spec(*, prefer_local_mlx: bool = True) -> str:
    """Pick a local planner model when env vars did not override the backend.

    On Apple Silicon: prefer MLX when Metal is available and the operator
    has not disabled local MLX. Fall back to Ollama when MLX is unavailable
    or disabled but Ollama is running.
    """
    if os.environ.get("CAPDEP_LLM_BACKEND") or os.environ.get("CAPDEP_LLM_MODEL"):
        return os.environ.get("CAPDEP_LLM_MODEL") or default_llm_model_spec()

    import platform

    on_apple_silicon = platform.system() == "Darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }
    allow_remote = os.environ.get("CAPDEP_ALLOW_REMOTE_LLM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if on_apple_silicon and prefer_local_mlx and mlx_metal_available():
        return default_llm_model_spec()
    if on_apple_silicon and prefer_local_mlx and not allow_remote:
        return default_llm_model_spec()
    if ollama_reachable() and allow_remote:
        return _DEFAULT_OLLAMA_MODEL
    return default_llm_model_spec()


def mlx_enable_thinking() -> bool:
    """Whether MLX models should use model-native thinking mode."""
    raw = os.environ.get("CAPDEP_MLX_ENABLE_THINKING")
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def make_llm_client(model: str | None = None) -> LLMClient:
    """Construct an LLM client from env/backend settings.

    Selection order:
    - `CAPDEP_LLM_BACKEND=claude-cli` uses the Claude CLI subscription backend.
    - `CAPDEP_LLM_BACKEND=mlx` uses the MLX runtime.
    - `CAPDEP_LLM_BACKEND=litellm` uses LiteLLM/API.
    - with no backend set, `model` / `CAPDEP_LLM_MODEL` / platform default decides.
    """
    backend = os.environ.get("CAPDEP_LLM_BACKEND", "").strip().lower()
    if backend in _CLAUDE_CLI_ALIASES:
        from capabledeputy.llm.claude_cli import ClaudeCliClient

        return ClaudeCliClient(model=os.environ.get("CAPDEP_CLAUDE_MODEL"))
    if backend in _LITELLM_ALIASES:
        return _make_litellm_client(model or os.environ.get("CAPDEP_LLM_MODEL"))
    if backend in _MLX_ALIASES:
        return _make_mlx_client(model or os.environ.get("CAPDEP_LLM_MODEL") or DEFAULT_MLX_MODEL)
    if backend:
        raise ValueError(f"unknown CAPDEP_LLM_BACKEND {backend!r}")

    model_spec = model or os.environ.get("CAPDEP_LLM_MODEL") or default_llm_model_spec()
    if model_spec.startswith("mlx/"):
        return _make_mlx_client(model_spec.removeprefix("mlx/"))
    return _make_litellm_client(model_spec)


def _make_mlx_client(model_spec: str) -> LLMClient:
    repo = model_spec.removeprefix("mlx/")
    if not repo:
        raise ValueError("MLX model spec must be of the form 'mlx/<repo-or-path>'.")
    from capabledeputy.llm.mlx_client import MLXLLMClient

    return MLXLLMClient(model=repo, enable_thinking=mlx_enable_thinking())


def _make_litellm_client(model_spec: str | None) -> LLMClient:
    from capabledeputy.llm.litellm_client import LiteLLMClient

    return LiteLLMClient(model=model_spec or "claude-haiku-4-5")
