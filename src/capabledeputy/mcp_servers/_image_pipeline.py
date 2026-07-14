"""Local image generation for the bundled images MCP server."""

from __future__ import annotations

import os
import platform
import re
import shutil
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

from capabledeputy.model_assets import conversion_readiness

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

_FORBIDDEN_PROMPT_RE = re.compile(
    r"\b("
    r"minor|minors|child|children|kid|kids|teen|teens|teenager|underage|"
    r"preteen|loli|shota|pedoph|paedoph"
    r")\b",
    re.IGNORECASE,
)

_STYLE_ALIASES = {
    "photoreal": "photoreal",
    "photo": "photoreal",
    "realistic": "photoreal",
    "real": "photoreal",
    "graphic_novel": "graphic_novel",
    "graphic-novel": "graphic_novel",
    "comic": "graphic_novel",
    "pony": "graphic_novel",
    "anime": "graphic_novel",
    "illustration": "graphic_novel",
}

_CHECKPOINT_PRESETS: dict[str, dict[str, str]] = {
    "photoreal": {
        "repo": "RunDiffusion/Juggernaut-XL-v9",
        "filename": "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
    },
    "graphic_novel": {
        "repo": "LyliaEngine/Pony_Diffusion_V6_XL",
        "filename": "ponyDiffusionV6XL_v6StartWithThisOne.safetensors",
    },
}

_PONY_PREFIX = "score_9, score_8_up, score_7_up, rating_explicit, "

_PIPE_CACHE: dict[str, Any] = {}
_BACKEND_CACHE: dict[
    tuple[str, str, str | None, int | None, tuple[str, ...], tuple[float, ...]],
    Any,
] = {}
_GENERATION_LOCK = threading.Lock()

_MFLUX_DEFAULT_MODEL_PATHS = {
    "z-image-turbo": "filipstrand/Z-Image-Turbo-mflux-4bit",
}

_MFLUX_MODEL_ALIASES = {
    "mlx": "z-image-turbo",
    "mflux": "z-image-turbo",
    "z": "z-image-turbo",
    "z-image": "z-image",
    "z-image-turbo": "z-image-turbo",
    "flux2": "flux2-klein-4b",
    "flux2-klein": "flux2-klein-4b",
    "flux2-klein-4b": "flux2-klein-4b",
    "flux2-klein-9b": "flux2-klein-9b",
    "fibo": "fibo",
    "fibo-lite": "fibo-lite",
    "qwen": "qwen-image",
    "qwen-image": "qwen-image",
    "flux-schnell": "schnell",
    "schnell": "schnell",
    "flux-dev": "dev",
    "dev": "dev",
}

_PROFILE_PRESETS: dict[str, dict[str, str]] = {
    "default": {
        "backend": "mflux",
        "model": "z-image-turbo",
        "model_path": "filipstrand/Z-Image-Turbo-mflux-4bit",
        "quantize": "8",
        "steps": "9",
    },
    "fast": {
        "backend": "mflux",
        "model": "z-image-turbo",
        "model_path": "filipstrand/Z-Image-Turbo-mflux-4bit",
        "quantize": "8",
        "steps": "9",
    },
    "balanced": {
        "backend": "mflux",
        "model": "z-image-turbo",
        "model_path": "filipstrand/Z-Image-Turbo-mflux-4bit",
        "quantize": "8",
        "steps": "12",
    },
    "quality": {
        "backend": "mflux",
        "model": "flux2-klein-4b",
        "quantize": "8",
        "guidance": "1.0",
        "steps": "8",
    },
    "quality-flux2": {
        "backend": "mflux",
        "model": "flux2-klein-4b",
        "quantize": "8",
        "guidance": "1.0",
        "steps": "8",
    },
    "quality-qwen": {
        "backend": "mflux",
        "model": "qwen-image",
        "model_path": "OsaurusAI/Qwen-Image-mflux-4bit",
        "quantize": "4",
        "steps": "12",
    },
    "flux-nsfw": {
        "backend": "mflux",
        "model": "schnell",
        "quantize": "8",
        "guidance": "1.0",
        "style": "photoreal",
    },
    "flux2-nsfw": {
        "backend": "mflux",
        "model": "flux2-klein-9b",
        "quantize": "8",
        "guidance": "1.0",
        "style": "photoreal",
    },
    "sdxl-nsfw": {
        "backend": "diffusers",
        "style": "photoreal",
        "steps": "24",
    },
    "pony-nsfw": {
        "backend": "diffusers",
        "style": "graphic_novel",
        "steps": "24",
    },
}

_PROFILE_METADATA: dict[str, dict[str, Any]] = {
    "default": {
        "title": "Default fast local image generation",
        "tier": "fast",
        "description": "Apple Silicon MFLUX Z-Image-Turbo default for interactive use.",
        "benchmark_note": "Fastest usable local default from v0.42 benchmark work.",
        "recommended": True,
    },
    "fast": {
        "title": "Fast",
        "tier": "fast",
        "description": "MFLUX Z-Image-Turbo at low step count for short interactive turns.",
        "benchmark_note": "Uses the same engine path as default.",
    },
    "balanced": {
        "title": "Balanced",
        "tier": "balanced",
        "description": "MFLUX Z-Image-Turbo with a slightly higher step count.",
        "benchmark_note": "Small quality bump without switching to slower models.",
    },
    "quality": {
        "title": "High quality",
        "tier": "quality",
        "description": "MFLUX Flux2 Klein 4B for slower, higher quality generations.",
        "benchmark_note": "Significantly slower than Z-Image-Turbo; keep explicit.",
        "slow": True,
    },
    "quality-flux2": {
        "title": "Flux2 quality challenger",
        "tier": "benchmark",
        "description": "MFLUX Flux2 Klein 4B quality candidate for benchmark runs.",
        "benchmark_note": "First general-quality challenger; benchmark before promotion.",
        "slow": True,
    },
    "quality-qwen": {
        "title": "Qwen Image quality challenger",
        "tier": "benchmark",
        "description": "Qwen Image MFLUX 4-bit candidate for benchmark runs.",
        "benchmark_note": "Evaluate prompt adherence and latency before promotion.",
        "slow": True,
    },
    "flux-nsfw": {
        "title": "Flux LoRA",
        "tier": "adapter-test",
        "description": "Flux.1 schnell profile intended for local LoRA testing.",
        "benchmark_note": "Laptop-usable for LoRA probes; quality depends on adapter.",
    },
    "flux2-nsfw": {
        "title": "Flux2 Klein LoRA",
        "tier": "adapter-test",
        "description": "Flux2 Klein 9B profile intended for newer Flux2 LoRA testing.",
        "benchmark_note": "More capable but much slower; keep explicit.",
        "slow": True,
    },
    "sdxl-nsfw": {
        "title": "SDXL checkpoint fallback",
        "tier": "fallback",
        "description": "Diffusers SDXL local checkpoint fallback.",
        "benchmark_note": "Use when local SDXL checkpoints are installed.",
        "slow": True,
    },
    "pony-nsfw": {
        "title": "Pony/Illustrious checkpoint fallback",
        "tier": "fallback",
        "description": "Diffusers graphic-novel checkpoint fallback.",
        "benchmark_note": "Use when local Pony/Illustrious checkpoints are installed.",
        "slow": True,
    },
}

_PROFILE_ASSET_IDS = {
    "default": "image.default",
    "fast": "image.default",
    "balanced": "image.default",
    "quality": "image.flux2-klein-quality",
    "quality-flux2": "image.flux2-klein-quality",
    "quality-qwen": "image.qwen-image-quality",
    "flux-nsfw": "image.flux-schnell-lora",
    "flux2-nsfw": "image.flux2-klein-quality",
    "sdxl-nsfw": "image.sdxl-photoreal",
    "pony-nsfw": "image.pony-graphic-novel",
}


@contextmanager
def _serialized_generation(output_dir: Path) -> Any:
    """Serialize GPU-heavy image generation within and across local processes."""
    with _GENERATION_LOCK:
        lock_file = None
        if fcntl is not None:
            output_dir.parent.mkdir(parents=True, exist_ok=True)
            lock_file = (output_dir.parent / ".image-generation.lock").open("a+")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if lock_file is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()


@dataclass(frozen=True)
class ImageGenConfig:
    output_dir: Path
    backend: str
    default_style: str
    model: str
    model_path: str | None
    quantize: int | None
    lora_paths: tuple[str, ...]
    lora_scales: tuple[float, ...]
    guidance: float | None
    device: str
    default_width: int
    default_height: int
    default_steps: int
    safety_enabled: bool
    prompt_filter_enabled: bool
    checkpoints: dict[str, dict[str, str]]


def _truthy(raw: str | None, *, default: bool = False) -> bool:
    token = (raw or "").strip().lower()
    if not token:
        return default
    return token in {"1", "true", "yes", "on"}


def _optional_int(raw: str | None) -> int | None:
    token = (raw or "").strip()
    return int(token) if token else None


def _optional_float(raw: str | None) -> float | None:
    token = (raw or "").strip()
    return float(token) if token else None


def _csv(raw: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (raw or "").split(",") if part.strip())


def _csv_floats(raw: str | None) -> tuple[float, ...]:
    return tuple(float(part.strip()) for part in (raw or "").split(",") if part.strip())


def _normalize_style(raw: str | None) -> str:
    token = (raw or "").strip().lower()
    if not token:
        return "photoreal"
    return _STYLE_ALIASES.get(token, token)


def _normalize_backend(raw: str | None) -> str:
    token = (raw or os.environ.get("CAPDEP_IMAGE_BACKEND") or "auto").strip().lower()
    if token in {"mlx", "mflux"}:
        return "mflux"
    if token in {"torch", "pytorch", "sdxl"}:
        return "diffusers"
    if token in {"auto", "diffusers"}:
        return token
    raise ValueError(f"unknown image backend {token!r}")


def _normalize_mflux_model(raw: str | None) -> str:
    token = (raw or "z-image-turbo").strip().lower()
    return _MFLUX_MODEL_ALIASES.get(token, token)


def _profile_value(profile: dict[str, str], key: str, env_key: str) -> str | None:
    env_value = os.environ.get(env_key)
    if env_value is not None and env_value.strip():
        return env_value.strip()
    value = profile.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _resolve_device(requested: str | None) -> str:
    token = (requested or os.environ.get("CAPDEP_IMAGE_DEVICE") or "auto").strip().lower()
    if token in {"cpu", "mps"}:
        return token
    try:
        import torch  # pyright: ignore[reportMissingImports]

        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _on_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _mlx_metal_status() -> tuple[bool, str]:
    try:
        import mlx.core as mx  # type: ignore[import-not-found]
    except Exception as exc:
        return False, f"mlx.core not importable: {exc}"
    try:
        if not bool(mx.metal.is_available()):
            return False, "mlx.core imported but Metal is not available"
    except Exception as exc:
        return False, f"mlx.core imported but Metal check failed: {exc}"
    return True, "mlx.core Metal backend available"


def _mflux_mlx_available() -> bool:
    if not _mflux_available():
        return False
    if _on_apple_silicon():
        available, _ = _mlx_metal_status()
        return available
    return True


def _resolve_runtime_backend(config: ImageGenConfig) -> str:
    backend = config.backend
    if backend == "auto":
        if _mflux_mlx_available():
            return "mflux"
        if _on_apple_silicon():
            available, detail = _mlx_metal_status()
            reason = detail if not available else "mflux not importable"
            raise RuntimeError(
                "image generation requires MFLUX with MLX/Metal on Apple Silicon; "
                f"{reason}. Install/fix .venv-images with `scripts/setup-images-venv.sh`.",
            )
        return "diffusers"
    if backend == "mflux" and _on_apple_silicon():
        available, detail = _mlx_metal_status()
        if not available:
            raise RuntimeError(
                "image generation requires MLX/Metal for the MFLUX backend on Apple Silicon; "
                f"{detail}.",
            )
    return backend


def available_image_profiles() -> list[dict[str, Any]]:
    """Return daemon/client-safe image profile metadata."""
    profiles: list[dict[str, Any]] = []
    for profile_id, preset in _PROFILE_PRESETS.items():
        metadata = dict(_PROFILE_METADATA.get(profile_id, {}))
        backend = _normalize_backend(preset.get("backend"))
        style = _normalize_style(preset.get("style"))
        model = _normalize_mflux_model(preset.get("model")) if backend == "mflux" else style
        asset_profile = _PROFILE_ASSET_IDS.get(profile_id)
        profiles.append(
            {
                "id": profile_id,
                "title": metadata.get("title") or profile_id,
                "tier": metadata.get("tier") or "custom",
                "description": metadata.get("description") or "",
                "benchmark_note": metadata.get("benchmark_note") or "",
                "recommended": bool(metadata.get("recommended", False)),
                "slow": bool(metadata.get("slow", False)),
                "backend": backend,
                "model": model,
                "style": style,
                "steps": int(preset.get("steps") or 20),
                "quantize": _optional_int(preset.get("quantize")),
                "guidance": _optional_float(preset.get("guidance")),
                "requires": _profile_requirements(preset),
                "asset_profile": asset_profile,
                "asset_readiness": conversion_readiness(asset_profile) if asset_profile else None,
            },
        )
    return profiles


def _profile_requirements(preset: dict[str, str]) -> list[str]:
    backend = _normalize_backend(preset.get("backend"))
    if backend == "mflux":
        requirements = ["mflux", "mlx"]
        if preset.get("model", "").startswith("flux"):
            requirements.append("huggingface-model-access")
        return requirements
    if backend == "diffusers":
        return ["torch", "diffusers", "local-or-huggingface-checkpoint"]
    return ["mflux-or-diffusers"]


def load_image_gen_config(*, profile_name: str | None = None) -> ImageGenConfig:
    profile_name = (
        (profile_name or os.environ.get("CAPDEP_IMAGE_PROFILE") or "default").strip().lower()
    )
    if profile_name not in _PROFILE_PRESETS:
        raise ValueError(f"unknown image profile {profile_name!r}")
    profile = _PROFILE_PRESETS[profile_name]
    output_dir = Path(
        os.environ.get("CAPDEP_IMAGE_OUTPUT_DIR") or (Path.home() / ".capdep" / "work" / "images"),
    )
    default_style = _normalize_style(_profile_value(profile, "style", "CAPDEP_IMAGE_STYLE"))

    checkpoints = {key: dict(value) for key, value in _CHECKPOINT_PRESETS.items()}
    for style in checkpoints:
        repo = os.environ.get(f"CAPDEP_IMAGE_{style.upper()}_CHECKPOINT")
        filename = os.environ.get(f"CAPDEP_IMAGE_{style.upper()}_CHECKPOINT_FILE")
        path = os.environ.get(f"CAPDEP_IMAGE_{style.upper()}_CHECKPOINT_PATH")
        if style == "photoreal":
            repo = os.environ.get("CAPDEP_IMAGE_CHECKPOINT") or repo
            filename = os.environ.get("CAPDEP_IMAGE_CHECKPOINT_FILE") or filename
            path = os.environ.get("CAPDEP_IMAGE_CHECKPOINT_PATH") or path
        if repo:
            checkpoints[style]["repo"] = repo.strip()
        if filename:
            checkpoints[style]["filename"] = filename.strip()
        if path:
            checkpoints[style]["path"] = path.strip()

    return ImageGenConfig(
        output_dir=output_dir,
        backend=_normalize_backend(_profile_value(profile, "backend", "CAPDEP_IMAGE_BACKEND")),
        default_style=default_style,
        model=_normalize_mflux_model(_profile_value(profile, "model", "CAPDEP_IMAGE_MODEL")),
        model_path=_profile_value(profile, "model_path", "CAPDEP_IMAGE_MODEL_PATH"),
        quantize=_optional_int(_profile_value(profile, "quantize", "CAPDEP_IMAGE_QUANTIZE")),
        lora_paths=_csv(os.environ.get("CAPDEP_IMAGE_LORAS")),
        lora_scales=_csv_floats(os.environ.get("CAPDEP_IMAGE_LORA_SCALES")),
        guidance=_optional_float(_profile_value(profile, "guidance", "CAPDEP_IMAGE_GUIDANCE")),
        device=_resolve_device(None),
        default_width=int(os.environ.get("CAPDEP_IMAGE_WIDTH", "768")),
        default_height=int(os.environ.get("CAPDEP_IMAGE_HEIGHT", "768")),
        default_steps=int(_profile_value(profile, "steps", "CAPDEP_IMAGE_STEPS") or "20"),
        safety_enabled=_truthy(os.environ.get("CAPDEP_IMAGE_SAFETY"), default=False),
        prompt_filter_enabled=_truthy(os.environ.get("CAPDEP_IMAGE_PROMPT_FILTER"), default=False),
        checkpoints=checkpoints,
    )


def image_readiness(*, profile_name: str | None = None) -> dict[str, Any]:
    """Return local image-generation readiness without reading secret values."""
    checks: list[dict[str, Any]] = []
    try:
        config = load_image_gen_config(profile_name=profile_name)
    except Exception as exc:
        return {
            "ok": False,
            "profile": profile_name or os.environ.get("CAPDEP_IMAGE_PROFILE") or "default",
            "checks": [
                {
                    "id": "profile",
                    "status": "error",
                    "detail": str(exc),
                    "recovery": "Select one of image.profiles list.",
                },
            ],
        }

    try:
        backend = _resolve_runtime_backend(config)
    except Exception as exc:
        backend = "mflux" if _on_apple_silicon() else config.backend
        checks = [
            {
                "id": "profile",
                "status": "ok",
                "detail": f"{profile_name or os.environ.get('CAPDEP_IMAGE_PROFILE') or 'default'}",
            },
            {
                "id": "mlx-metal",
                "status": "error",
                "detail": str(exc),
                "recovery": "Install/fix .venv-images with `scripts/setup-images-venv.sh`.",
            },
        ]
        return {
            "ok": False,
            "profile": profile_name or os.environ.get("CAPDEP_IMAGE_PROFILE") or "default",
            "backend": backend,
            "model": config.model,
            "model_path": config.model_path or _MFLUX_DEFAULT_MODEL_PATHS.get(config.model),
            "asset_profile": _PROFILE_ASSET_IDS.get(
                (profile_name or os.environ.get("CAPDEP_IMAGE_PROFILE") or "default")
                .strip()
                .lower(),
            ),
            "asset_readiness": None,
            "device": config.device,
            "checks": checks,
        }
    checks.append(
        {
            "id": "profile",
            "status": "ok",
            "detail": f"{profile_name or os.environ.get('CAPDEP_IMAGE_PROFILE') or 'default'}",
        },
    )
    checks.extend(_backend_readiness_checks(backend))
    checks.extend(_path_readiness_checks(config))
    checks.extend(_account_readiness_checks(config, backend))
    asset_profile = _PROFILE_ASSET_IDS.get(
        (profile_name or os.environ.get("CAPDEP_IMAGE_PROFILE") or "default").strip().lower(),
    )
    asset_readiness = conversion_readiness(asset_profile) if asset_profile else None
    if asset_readiness is not None:
        checks.append(
            {
                "id": "model-asset",
                "status": "ok"
                if asset_readiness["status"] in {"native", "converted", "source_fallback"}
                else "warning",
                "detail": f"{asset_readiness['profile_id']}: {asset_readiness['status']}",
                "recovery": "Run `capdep-setup models --apply --convert` for supported assets.",
            },
        )
    ok = not any(check["status"] == "error" for check in checks)
    return {
        "ok": ok,
        "profile": profile_name or os.environ.get("CAPDEP_IMAGE_PROFILE") or "default",
        "backend": backend,
        "model": config.model,
        "model_path": config.model_path or _MFLUX_DEFAULT_MODEL_PATHS.get(config.model),
        "asset_profile": asset_profile,
        "asset_readiness": asset_readiness,
        "device": config.device,
        "checks": checks,
    }


def _backend_readiness_checks(backend: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if backend == "mflux":
        checks.append(_module_check("mflux", "mflux", "Install image extras in .venv-images."))
        checks.append(_module_check("mlx", "mlx.core", "Install MLX/MFLUX on Apple Silicon."))
        if _on_apple_silicon():
            available, detail = _mlx_metal_status()
            checks.append(
                {
                    "id": "mlx-metal",
                    "status": "ok" if available else "error",
                    "detail": detail,
                    "recovery": "Install MLX in .venv-images and run on Apple Silicon with Metal.",
                },
            )
    elif backend == "diffusers":
        for module in ("torch", "diffusers", "huggingface_hub"):
            checks.append(_module_check(module, module, "Install capabledeputy[images]."))
    else:
        checks.append(
            {
                "id": "backend",
                "status": "error",
                "detail": f"unsupported backend {backend}",
                "recovery": "Use mflux, diffusers, or auto.",
            },
        )
    return checks


def _module_check(check_id: str, module_name: str, recovery: str) -> dict[str, Any]:
    try:
        import_module(module_name)
        return {"id": check_id, "status": "ok", "detail": f"{module_name} importable"}
    except Exception as exc:
        return {
            "id": check_id,
            "status": "error",
            "detail": f"{module_name} not importable: {exc}",
            "recovery": recovery,
        }


def _path_readiness_checks(config: ImageGenConfig) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    output_parent = config.output_dir.expanduser().parent
    checks.append(
        {
            "id": "output-dir",
            "status": "ok" if output_parent.exists() else "warning",
            "detail": str(config.output_dir.expanduser()),
            "recovery": "Parent directory will be created on first generation.",
        },
    )
    for path in config.lora_paths:
        resolved = Path(path).expanduser()
        checks.append(
            {
                "id": "lora-path",
                "status": "ok" if resolved.is_file() else "error",
                "detail": str(resolved),
                "recovery": "Download the LoRA file or remove it from CAPDEP_IMAGE_LORAS.",
            },
        )
    for style, checkpoint in config.checkpoints.items():
        path = checkpoint.get("path")
        if not path:
            continue
        resolved = Path(path).expanduser()
        checks.append(
            {
                "id": f"{style}-checkpoint-path",
                "status": "ok" if resolved.is_file() else "error",
                "detail": str(resolved),
                "recovery": "Download the checkpoint or clear the local checkpoint override.",
            },
        )
    return checks


def _account_readiness_checks(config: ImageGenConfig, backend: str) -> list[dict[str, Any]]:
    needs_hf = backend == "diffusers" and any(
        "path" not in cp for cp in config.checkpoints.values()
    )
    needs_hf = needs_hf or bool(config.model_path and "/" in config.model_path)
    if not needs_hf:
        return []
    token_sources = _huggingface_token_sources()
    return [
        {
            "id": "huggingface-token",
            "status": "ok" if token_sources else "warning",
            "detail": ", ".join(token_sources)
            if token_sources
            else "no local token file/env detected",
            "recovery": "Run `hf auth login` or set HF_TOKEN if the selected model is gated.",
        },
    ]


def _huggingface_token_sources() -> list[str]:
    sources: list[str] = []
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN"):
        sources.append("env")
    home = Path.home()
    for path in (
        home / ".cache" / "huggingface" / "token",
        home / ".huggingface" / "token",
        home / ".config" / "huggingface" / "token",
    ):
        try:
            if path.is_file():
                sources.append(str(path))
        except OSError:
            pass
    if shutil.which("hf"):
        sources.append("hf-cli")
    return sources


def validate_prompt(prompt: str, *, enabled: bool = False) -> str | None:
    """Return an error string when the optional operator prompt filter is enabled."""
    if not prompt.strip():
        return "prompt must be non-empty"
    if enabled and _FORBIDDEN_PROMPT_RE.search(prompt):
        return (
            "prompt rejected by CAPDEP_IMAGE_PROMPT_FILTER "
            "(disable the operator filter to pass prompts through)"
        )
    return None


def wrap_prompt_for_style(prompt: str, style: str) -> str:
    body = prompt.strip()
    if style == "graphic_novel" and not body.lower().startswith("score_"):
        return f"{_PONY_PREFIX}{body}"
    return body


def _require_image_deps() -> None:
    try:
        import diffusers  # noqa: F401  # pyright: ignore[reportMissingImports]
        import torch  # noqa: F401  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise RuntimeError(
            "image generation requires optional deps — install with `scripts/setup-images-venv.sh`",
        ) from exc


def _mflux_available() -> bool:
    try:
        import_module("mflux")
        return True
    except Exception:
        return False


def _mflux_cache_key(
    config: ImageGenConfig,
) -> tuple[str, str, str | None, int | None, tuple[str, ...], tuple[float, ...]]:
    return (
        "mflux",
        config.model,
        config.model_path,
        config.quantize,
        config.lora_paths,
        config.lora_scales,
    )


def _load_mflux_model(config: ImageGenConfig) -> Any:
    key = _mflux_cache_key(config)
    cached = _BACKEND_CACHE.get(key)
    if cached is not None:
        return cached

    model_name = _normalize_mflux_model(config.model)
    model_path = config.model_path or _MFLUX_DEFAULT_MODEL_PATHS.get(model_name)
    lora_paths = list(config.lora_paths) or None
    lora_scales = list(config.lora_scales) or None

    if model_name in {"z-image", "z-image-turbo"}:
        from mflux.models.common.config import ModelConfig  # pyright: ignore[reportMissingImports]
        from mflux.models.z_image import ZImage  # pyright: ignore[reportMissingImports]

        model_config = (
            ModelConfig.z_image_turbo() if model_name == "z-image-turbo" else ModelConfig.z_image()
        )
        model = ZImage(
            model_config=model_config,
            quantize=config.quantize,
            model_path=model_path,
            lora_paths=lora_paths,
            lora_scales=lora_scales,
        )
    elif model_name.startswith("flux2-klein"):
        from mflux.models.common.config import ModelConfig  # pyright: ignore[reportMissingImports]
        from mflux.models.flux2.variants import Flux2Klein  # pyright: ignore[reportMissingImports]

        model = Flux2Klein(
            model_config=ModelConfig.from_name(model_name=model_name),
            quantize=config.quantize,
            model_path=model_path,
            lora_paths=lora_paths,
            lora_scales=lora_scales,
        )
    elif model_name in {"fibo", "fibo-lite"}:
        from mflux.models.common.config import ModelConfig  # pyright: ignore[reportMissingImports]
        from mflux.models.fibo.variants.txt2img.fibo import (
            FIBO,  # pyright: ignore[reportMissingImports]
        )

        model = FIBO(
            model_config=ModelConfig.from_name(model_name=model_name),
            quantize=config.quantize,
            model_path=model_path,
        )
    elif model_name == "qwen-image":
        from mflux.models.qwen.variants.txt2img.qwen_image import (
            QwenImage,  # pyright: ignore[reportMissingImports]
        )

        model = QwenImage(
            quantize=config.quantize,
            model_path=model_path,
            lora_paths=lora_paths,
            lora_scales=lora_scales,
        )
    elif model_name in {"schnell", "dev", "krea-dev"}:
        from mflux.models.common.config import ModelConfig  # pyright: ignore[reportMissingImports]
        from mflux.models.flux.variants.txt2img.flux import (
            Flux1,  # pyright: ignore[reportMissingImports]
        )

        model = Flux1(
            model_config=ModelConfig.from_name(model_name=model_name),
            quantize=config.quantize,
            model_path=model_path,
            lora_paths=lora_paths,
            lora_scales=lora_scales,
        )
    else:
        raise ValueError(f"unsupported MFLUX image model {model_name!r}")

    _BACKEND_CACHE[key] = model
    return model


def _generate_mflux_image(
    *,
    prompt: str,
    negative_prompt: str | None,
    output_path: Path,
    width: int,
    height: int,
    steps: int,
    seed: int | None,
    config: ImageGenConfig,
) -> dict[str, Any]:
    model_name = _normalize_mflux_model(config.model)
    model = _load_mflux_model(config)
    guidance = config.guidance
    if guidance is None:
        if model_name.startswith("flux2-klein"):
            guidance = 1.0
        elif model_name == "z-image-turbo":
            guidance = 0.0
        elif model_name in {"schnell", "fibo-lite"}:
            guidance = 1.0
        else:
            guidance = 4.0

    kwargs: dict[str, Any] = {
        "seed": int(seed if seed is not None else 0),
        "prompt": prompt,
        "width": width,
        "height": height,
        "num_inference_steps": steps,
    }
    if not model_name.startswith("flux2-klein"):
        kwargs["negative_prompt"] = (negative_prompt or "").strip()
    if model_name not in {"z-image-turbo"} or guidance:
        kwargs["guidance"] = guidance

    image = model.generate_image(**kwargs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save(path=str(output_path), export_json_metadata=False)
    except TypeError:
        image.save(str(output_path))
    return {
        "backend": "mflux",
        "model": model_name,
        "model_path": config.model_path or _MFLUX_DEFAULT_MODEL_PATHS.get(model_name),
        "quantize": config.quantize,
        "guidance": guidance,
    }


def _load_pipeline(style: str, config: ImageGenConfig) -> Any:
    cached = _PIPE_CACHE.get(style)
    if cached is not None:
        return cached

    _require_image_deps()
    import torch  # pyright: ignore[reportMissingImports]
    from diffusers import StableDiffusionXLPipeline  # pyright: ignore[reportMissingImports]
    from huggingface_hub import hf_hub_download

    preset = config.checkpoints.get(style)
    if preset is None:
        raise ValueError(f"unknown image style {style!r}")

    dtype = torch.float16 if config.device == "mps" else torch.float32
    checkpoint_path = preset.get("path")
    if checkpoint_path:
        checkpoint_path = str(Path(checkpoint_path).expanduser())
    else:
        checkpoint_path = hf_hub_download(
            repo_id=preset["repo"],
            filename=preset["filename"],
        )
    pipe = StableDiffusionXLPipeline.from_single_file(
        checkpoint_path,
        torch_dtype=dtype,
        use_safetensors=True,
    )
    if config.safety_enabled:
        # Operator opt-in only; default is uncensored local generation.
        pass
    else:
        if hasattr(pipe, "safety_checker"):
            pipe.safety_checker = None
        if hasattr(pipe, "feature_extractor"):
            pipe.feature_extractor = None

    pipe = pipe.to(config.device)
    _PIPE_CACHE[style] = pipe
    return pipe


def _generate_diffusers_image(
    *,
    prompt: str,
    style: str,
    negative_prompt: str | None,
    output_path: Path,
    width: int,
    height: int,
    steps: int,
    seed: int | None,
    config: ImageGenConfig,
) -> dict[str, Any]:
    import torch  # pyright: ignore[reportMissingImports]

    pipe = _load_pipeline(style, config)
    generator_device = "cpu" if config.device == "mps" else config.device
    generator = torch.Generator(device=generator_device)
    if seed is not None:
        generator = generator.manual_seed(int(seed))

    result = pipe(
        prompt=prompt,
        negative_prompt=(negative_prompt or "").strip() or None,
        num_inference_steps=steps,
        width=width,
        height=height,
        generator=generator,
    )
    image = result.images[0]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return {
        "backend": "diffusers",
        "model": style,
        "device": config.device,
    }


def generate_image(
    *,
    prompt: str,
    style: str | None = None,
    negative_prompt: str | None = None,
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    seed: int | None = None,
    alt: str | None = None,
    filename: str | None = None,
    config: ImageGenConfig | None = None,
) -> dict[str, Any]:
    """Generate a PNG and return structured output for inline GUI display."""
    cfg = config or load_image_gen_config()
    normalized_style = _normalize_style(style or cfg.default_style)
    try:
        backend = _resolve_runtime_backend(cfg)
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "backend": "mflux" if _on_apple_silicon() else cfg.backend,
            "style": normalized_style,
            "model": cfg.model,
            "device": cfg.device,
        }

    if backend == "diffusers" and normalized_style not in cfg.checkpoints:
        return {"ok": False, "error": f"unknown style {normalized_style!r}"}

    prompt_error = validate_prompt(prompt, enabled=cfg.prompt_filter_enabled)
    if prompt_error:
        return {"ok": False, "error": prompt_error}

    wrapped_prompt = wrap_prompt_for_style(prompt, normalized_style)
    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    out_name = (filename or f"{uuid4().hex}.png").strip()
    if not out_name.lower().endswith(".png"):
        out_name += ".png"
    output_path = out_dir / Path(out_name).name

    width_value = int(width or cfg.default_width)
    height_value = int(height or cfg.default_height)
    steps_value = int(steps or cfg.default_steps)

    try:
        with _serialized_generation(out_dir):
            if backend == "mflux":
                runtime = _generate_mflux_image(
                    prompt=prompt.strip(),
                    negative_prompt=negative_prompt,
                    output_path=output_path,
                    width=width_value,
                    height=height_value,
                    steps=steps_value,
                    seed=seed,
                    config=cfg,
                )
            elif backend == "diffusers":
                runtime = _generate_diffusers_image(
                    prompt=wrapped_prompt,
                    style=normalized_style,
                    negative_prompt=negative_prompt,
                    output_path=output_path,
                    width=width_value,
                    height=height_value,
                    steps=steps_value,
                    seed=seed,
                    config=cfg,
                )
            else:
                return {"ok": False, "error": f"unknown image backend {backend!r}"}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "backend": backend,
            "style": normalized_style,
            "model": cfg.model,
        }

    path_str = str(output_path.resolve())
    alt_text = (alt or prompt.strip()[:120] or "generated image").strip()
    markdown = f"![{alt_text}]({path_str})"
    return {
        "ok": True,
        "style": normalized_style,
        "prompt": prompt,
        "wrapped_prompt": wrapped_prompt,
        "image_path": path_str,
        "plot": path_str,
        "alt": alt_text,
        "markdown": markdown,
        "content": f"Generated {normalized_style} image.\n\n{markdown}\n",
        "backend": runtime["backend"],
        "model": runtime["model"],
        "model_path": runtime.get("model_path"),
        "quantize": runtime.get("quantize"),
        "guidance": runtime.get("guidance"),
        "safety_checker": cfg.safety_enabled,
        "prompt_filter": cfg.prompt_filter_enabled,
        "device": runtime.get("device", "mlx"),
        "width": width_value,
        "height": height_value,
        "steps": steps_value,
    }


def clear_pipeline_cache() -> None:
    """Drop cached pipelines (tests and memory pressure)."""
    _PIPE_CACHE.clear()
    _BACKEND_CACHE.clear()
