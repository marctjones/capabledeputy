"""MLX model pool with lazy role-based clients."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from capabledeputy.llm.client import LLMClient
from capabledeputy.llm.factory import make_llm_client, mlx_enable_thinking
from capabledeputy.llm.models_config import ModelsConfig, load_models_config
from capabledeputy.llm.routing import ModelRoutingContext, ModelRoutingResult, resolve_model_role


@dataclass
class ModelPool:
    """Lazy-loaded MLX clients keyed by operator-defined role."""

    config: ModelsConfig
    _clients: dict[str, LLMClient] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_tools_access: float = 0.0
    _tools_idle_unload_seconds: float = 600.0
    _env_overrides: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        config: ModelsConfig | None = None,
        *,
        configs_dir: Any = None,
    ) -> ModelPool:
        if config is None:
            path = None
            if configs_dir is not None:
                from pathlib import Path

                candidate = Path(configs_dir) / "models.yaml"
                if candidate.is_file():
                    path = candidate
            config = load_models_config(path)
        overrides: dict[str, str] = {}
        fast = os.environ.get("CAPDEP_LLM_MODEL", "").strip()
        if fast:
            overrides["planner.fast"] = fast.removeprefix("mlx/")
        quarantined = os.environ.get("CAPDEP_QUARANTINED_LLM_MODEL", "").strip()
        if quarantined:
            overrides["extractor"] = quarantined.removeprefix("mlx/")
        return cls(config=config, _env_overrides=overrides)

    def preload(self, *roles: str) -> None:
        for role in roles:
            self.client(role)

    def client(self, role: str) -> LLMClient:
        with self._lock:
            cached = self._clients.get(role)
            if cached is not None:
                if role == "planner.tools":
                    self._last_tools_access = time.monotonic()
                return cached
            spec = self.config.role_spec(role)
            model_id = self._env_overrides.get(role, spec.mlx)
            from capabledeputy.llm.mlx_client import MLXLLMClient

            client: LLMClient = MLXLLMClient(
                model=model_id,
                max_tokens=spec.max_tokens,
                enable_thinking=mlx_enable_thinking(),
            )
            self._clients[role] = client
            if role == "planner.tools":
                self._last_tools_access = time.monotonic()
            return client

    def resolve_planner(self, ctx: ModelRoutingContext) -> tuple[LLMClient, ModelRoutingResult]:
        result = resolve_model_role(self.config, ctx)
        client = self.client(result.role)
        self._maybe_unload_tools_model()
        return client, result

    def extractor_client(self) -> LLMClient:
        return self.client("extractor")

    def default_planner_client(self) -> LLMClient:
        return self.client("planner.fast")

    def _maybe_unload_tools_model(self) -> None:
        if "planner.tools" not in self._clients:
            return
        idle = time.monotonic() - self._last_tools_access
        if idle < self._tools_idle_unload_seconds:
            return
        self._clients.pop("planner.tools", None)

    def status(self) -> dict[str, Any]:
        loaded = sorted(self._clients)
        roles = {
            role: {
                "mlx": self._env_overrides.get(role, self.config.role_spec(role).mlx),
                "loaded": role in self._clients,
                "max_tokens": self.config.role_spec(role).max_tokens,
            }
            for role in self.config.roles
        }
        return {
            "backend": self.config.default_backend,
            "loaded_roles": loaded,
            "roles": roles,
            "tool_selection_mode": self.config.tool_selection.mode,
        }


def require_mlx_on_apple_silicon(*, prefer_local_mlx: bool = True) -> None:
    """Refuse remote/Ollama fallbacks on Apple Silicon unless explicitly allowed."""
    import platform

    if os.environ.get("CAPDEP_ALLOW_REMOTE_LLM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    on_apple_silicon = (
        platform.system() == "Darwin"
        and platform.machine().lower() in {"arm64", "aarch64"}
    )
    if not on_apple_silicon or not prefer_local_mlx:
        return
    backend = os.environ.get("CAPDEP_LLM_BACKEND", "").strip().lower()
    if backend in {"litellm", "api", "anthropic", "claude-cli", "claude", "cli"}:
        raise RuntimeError(
            "CAPDEP_LLM_BACKEND selects a remote planner on Apple Silicon. "
            "Use MLX (default) or set CAPDEP_ALLOW_REMOTE_LLM=1 to override.",
        )
    model = os.environ.get("CAPDEP_LLM_MODEL", "").strip()
    if model and not model.startswith("mlx/") and not model.startswith("Qwen/"):
        if model.startswith("ollama/") or "claude" in model.lower():
            raise RuntimeError(
                f"CAPDEP_LLM_MODEL={model!r} is not an MLX model. "
                "Use mlx/<repo> or set CAPDEP_ALLOW_REMOTE_LLM=1.",
            )


def make_legacy_pool_client(model_spec: str | None) -> LLMClient:
    """Construct a single client when no models.yaml pool is used."""
    return make_llm_client(model_spec)