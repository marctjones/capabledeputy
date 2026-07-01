"""Local uncensored SDXL image generation for the bundled images MCP server."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

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


@dataclass(frozen=True)
class ImageGenConfig:
    output_dir: Path
    default_style: str
    device: str
    default_width: int
    default_height: int
    default_steps: int
    safety_enabled: bool
    checkpoints: dict[str, dict[str, str]]


def _truthy(raw: str | None, *, default: bool = False) -> bool:
    token = (raw or "").strip().lower()
    if not token:
        return default
    return token in {"1", "true", "yes", "on"}


def _normalize_style(raw: str | None) -> str:
    token = (raw or "").strip().lower()
    if not token:
        return "photoreal"
    return _STYLE_ALIASES.get(token, token)


def _resolve_device(requested: str | None) -> str:
    token = (requested or os.environ.get("CAPDEP_IMAGE_DEVICE") or "auto").strip().lower()
    if token in {"cpu", "mps"}:
        return token
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def load_image_gen_config() -> ImageGenConfig:
    output_dir = Path(
        os.environ.get("CAPDEP_IMAGE_OUTPUT_DIR")
        or (Path.home() / ".capdep" / "work" / "images"),
    )
    default_style = _normalize_style(os.environ.get("CAPDEP_IMAGE_STYLE"))

    checkpoints = {key: dict(value) for key, value in _CHECKPOINT_PRESETS.items()}
    for style in checkpoints:
        repo = os.environ.get(f"CAPDEP_IMAGE_{style.upper()}_CHECKPOINT")
        filename = os.environ.get(f"CAPDEP_IMAGE_{style.upper()}_CHECKPOINT_FILE")
        if style == "photoreal":
            repo = os.environ.get("CAPDEP_IMAGE_CHECKPOINT") or repo
            filename = os.environ.get("CAPDEP_IMAGE_CHECKPOINT_FILE") or filename
        if repo:
            checkpoints[style]["repo"] = repo.strip()
        if filename:
            checkpoints[style]["filename"] = filename.strip()

    return ImageGenConfig(
        output_dir=output_dir,
        default_style=default_style,
        device=_resolve_device(None),
        default_width=int(os.environ.get("CAPDEP_IMAGE_WIDTH", "768")),
        default_height=int(os.environ.get("CAPDEP_IMAGE_HEIGHT", "768")),
        default_steps=int(os.environ.get("CAPDEP_IMAGE_STEPS", "20")),
        safety_enabled=_truthy(os.environ.get("CAPDEP_IMAGE_SAFETY"), default=False),
        checkpoints=checkpoints,
    )


def validate_prompt(prompt: str) -> str | None:
    """Return an error string when the prompt violates hard legal limits."""
    if not prompt.strip():
        return "prompt must be non-empty"
    if _FORBIDDEN_PROMPT_RE.search(prompt):
        return (
            "prompt references minors or underage subjects — refused "
            "(hard policy limit)"
        )
    return None


def wrap_prompt_for_style(prompt: str, style: str) -> str:
    body = prompt.strip()
    if style == "graphic_novel" and not body.lower().startswith("score_"):
        return f"{_PONY_PREFIX}{body}"
    return body


def _require_image_deps() -> None:
    try:
        import diffusers  # noqa: F401
        import torch  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "image generation requires optional deps — install with "
            "`pip install capabledeputy[images]`",
        ) from exc


def _load_pipeline(style: str, config: ImageGenConfig) -> Any:
    cached = _PIPE_CACHE.get(style)
    if cached is not None:
        return cached

    _require_image_deps()
    import torch
    from diffusers import StableDiffusionXLPipeline
    from huggingface_hub import hf_hub_download

    preset = config.checkpoints.get(style)
    if preset is None:
        raise ValueError(f"unknown image style {style!r}")

    dtype = torch.float16 if config.device == "mps" else torch.float32
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
    if normalized_style not in cfg.checkpoints:
        return {"ok": False, "error": f"unknown style {normalized_style!r}"}

    prompt_error = validate_prompt(prompt)
    if prompt_error:
        return {"ok": False, "error": prompt_error}

    wrapped_prompt = wrap_prompt_for_style(prompt, normalized_style)
    out_dir = cfg.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    out_name = (filename or f"{uuid4().hex}.png").strip()
    if not out_name.lower().endswith(".png"):
        out_name += ".png"
    output_path = out_dir / Path(out_name).name

    try:
        import torch

        pipe = _load_pipeline(normalized_style, cfg)
        generator_device = "cpu" if cfg.device == "mps" else cfg.device
        generator = torch.Generator(device=generator_device)
        if seed is not None:
            generator = generator.manual_seed(int(seed))

        result = pipe(
            prompt=wrapped_prompt,
            negative_prompt=(negative_prompt or "").strip() or None,
            num_inference_steps=int(steps or cfg.default_steps),
            width=int(width or cfg.default_width),
            height=int(height or cfg.default_height),
            generator=generator,
        )
        image = result.images[0]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "style": normalized_style}

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
        "safety_checker": cfg.safety_enabled,
        "device": cfg.device,
        "width": int(width or cfg.default_width),
        "height": int(height or cfg.default_height),
        "steps": int(steps or cfg.default_steps),
    }


def clear_pipeline_cache() -> None:
    """Drop cached pipelines (tests and memory pressure)."""
    _PIPE_CACHE.clear()