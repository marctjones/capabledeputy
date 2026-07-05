"""Model asset inventory and MLX conversion planning.

The inventory is intentionally descriptive and side-effect free. Mutating
downloads/conversions stay in ``capdep-setup`` and require explicit flags.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelAssetProfile:
    profile_id: str
    role: str
    backend: str
    source_repo: str
    source_format: str
    recommended_runtime: str
    conversion_status: str
    gated: bool = False
    quantization: str | None = None
    fallback_runtime: str | None = None
    notes: str = ""
    files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def can_download(self) -> bool:
        return self.download_repo is not None

    @property
    def download_repo(self) -> str | None:
        runtime = self.recommended_runtime.removeprefix("mlx/")
        if "/" in runtime:
            return runtime
        if "/" in self.source_repo:
            return self.source_repo
        return None

    @property
    def can_convert(self) -> bool:
        return self.conversion_status == "convertible"

    @property
    def manifest_name(self) -> str:
        digest = hashlib.sha256(self.profile_id.encode("utf-8")).hexdigest()[:12]
        return f"{self.profile_id.replace('.', '-').replace('/', '-')}-{digest}.json"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.profile_id,
            "role": self.role,
            "backend": self.backend,
            "source_repo": self.source_repo,
            "source_format": self.source_format,
            "recommended_runtime": self.recommended_runtime,
            "conversion_status": self.conversion_status,
            "gated": self.gated,
            "quantization": self.quantization,
            "fallback_runtime": self.fallback_runtime,
            "notes": self.notes,
            "files": list(self.files),
            "can_download": self.can_download,
            "download_repo": self.download_repo,
            "can_convert": self.can_convert,
        }


def model_asset_home(base: Path | None = None) -> Path:
    if base is not None:
        return base
    raw = os.environ.get("CAPDEP_MODEL_ASSET_HOME")
    if raw:
        return Path(raw).expanduser()
    hf_home = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")
    return hf_home / "capdep-model-assets"


def model_asset_inventory(*, apple_silicon: bool | None = None) -> tuple[ModelAssetProfile, ...]:
    if apple_silicon is None:
        apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    text_runtime = "mlx-lm" if apple_silicon else "transformers"
    image_runtime = "mflux" if apple_silicon else "diffusers"
    return (
        ModelAssetProfile(
            profile_id="planner.fast",
            role="text",
            backend=text_runtime,
            source_repo="Qwen/Qwen3-4B",
            source_format="huggingface-transformers",
            recommended_runtime="mlx/Qwen/Qwen3-4B-MLX-4bit" if apple_silicon else "Qwen/Qwen3-4B",
            conversion_status="native_mlx_available" if apple_silicon else "source_runtime",
            quantization="4bit" if apple_silicon else None,
            fallback_runtime="transformers",
            notes="Default local planner candidate; prefer upstream MLX repo on Apple Silicon.",
        ),
        ModelAssetProfile(
            profile_id="planner.tools",
            role="text",
            backend=text_runtime,
            source_repo="Qwen/Qwen3-14B",
            source_format="huggingface-transformers",
            recommended_runtime="mlx-community/Qwen3-14B-4bit"
            if apple_silicon
            else "Qwen/Qwen3-14B",
            conversion_status="native_mlx_available" if apple_silicon else "source_runtime",
            quantization="4bit" if apple_silicon else None,
            fallback_runtime="transformers",
            notes="Larger tool-selection/planning candidate.",
        ),
        ModelAssetProfile(
            profile_id="planner.quality",
            role="text",
            backend=text_runtime,
            source_repo="Qwen/Qwen3-30B-A3B",
            source_format="huggingface-transformers",
            recommended_runtime="mlx-community/Qwen3-30B-A3B-4bit"
            if apple_silicon
            else "Qwen/Qwen3-30B-A3B",
            conversion_status="native_mlx_available" if apple_silicon else "source_runtime",
            quantization="4bit" if apple_silicon else None,
            fallback_runtime="transformers",
            notes="Preferred quality planner candidate; text-only MLX-LM MoE path.",
        ),
        ModelAssetProfile(
            profile_id="planner.coder",
            role="text",
            backend=text_runtime,
            source_repo="Qwen/Qwen3-Coder-30B-A3B-Instruct",
            source_format="huggingface-transformers",
            recommended_runtime="mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit"
            if apple_silicon
            else "Qwen/Qwen3-Coder-30B-A3B-Instruct",
            conversion_status="native_mlx_available" if apple_silicon else "source_runtime",
            quantization="4bit" if apple_silicon else None,
            fallback_runtime="transformers",
            notes="Coding and safe-scripting planner candidate.",
        ),
        ModelAssetProfile(
            profile_id="vlm.experimental",
            role="vision-language",
            backend="mlx-vlm" if apple_silicon else "transformers",
            source_repo="Qwen/Qwen3.6-35B-A3B",
            source_format="huggingface-transformers-vlm",
            recommended_runtime="mlx-community/Qwen3.6-35B-A3B-4bit"
            if apple_silicon
            else "Qwen/Qwen3.6-35B-A3B",
            conversion_status="native_mlx_vlm_available" if apple_silicon else "source_runtime",
            quantization="4bit" if apple_silicon else None,
            fallback_runtime="transformers",
            notes=(
                "Experimental VLM candidate; keep out of text planner routing until "
                "CapDep has explicit mlx-vlm support."
            ),
        ),
        ModelAssetProfile(
            profile_id="extractor",
            role="text",
            backend=text_runtime,
            source_repo="microsoft/Phi-3.5-mini-instruct",
            source_format="huggingface-transformers",
            recommended_runtime="mlx-community/Phi-3.5-mini-instruct-4bit"
            if apple_silicon
            else "microsoft/Phi-3.5-mini-instruct",
            conversion_status="native_mlx_available" if apple_silicon else "source_runtime",
            quantization="4bit" if apple_silicon else None,
            fallback_runtime="transformers",
            notes="Small extraction role; native MLX repo is preferred when available.",
        ),
        ModelAssetProfile(
            profile_id="image.default",
            role="image",
            backend=image_runtime,
            source_repo="filipstrand/Z-Image-Turbo-mflux-4bit",
            source_format="mflux-mlx",
            recommended_runtime="filipstrand/Z-Image-Turbo-mflux-4bit"
            if apple_silicon
            else "stabilityai/sdxl-turbo",
            conversion_status="native_mflux_available" if apple_silicon else "source_runtime",
            quantization="4bit",
            fallback_runtime="diffusers",
            notes="Interactive default image profile; use existing MFLUX asset.",
        ),
        ModelAssetProfile(
            profile_id="image.flux-schnell-lora",
            role="image",
            backend=image_runtime,
            source_repo="black-forest-labs/FLUX.1-schnell",
            source_format="diffusers",
            recommended_runtime="mflux schnell" if apple_silicon else "diffusers",
            conversion_status="runtime_native_no_conversion" if apple_silicon else "source_runtime",
            gated=False,
            fallback_runtime="diffusers",
            notes="MFLUX can run this family directly; LoRA compatibility is adapter-specific.",
        ),
        ModelAssetProfile(
            profile_id="image.sdxl-photoreal",
            role="image",
            backend="diffusers",
            source_repo="RunDiffusion/Juggernaut-XL-v9",
            source_format="safetensors",
            recommended_runtime="diffusers",
            conversion_status="unsupported",
            gated=True,
            fallback_runtime="diffusers",
            files=("Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",),
            notes="Keep as explicit fallback until a measured MLX/MFLUX path exists.",
        ),
        ModelAssetProfile(
            profile_id="image.pony-graphic-novel",
            role="image",
            backend="diffusers",
            source_repo="LyliaEngine/Pony_Diffusion_V6_XL",
            source_format="safetensors",
            recommended_runtime="diffusers",
            conversion_status="unsupported",
            gated=True,
            fallback_runtime="diffusers",
            files=("ponyDiffusionV6XL_v6StartWithThisOne.safetensors",),
            notes="Keep as explicit fallback; do not silently convert or promote.",
        ),
    )


def model_download_commands(
    inventory: tuple[ModelAssetProfile, ...],
    *,
    cache_home: Path,
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        ("hf", "download", profile.download_repo, "--cache-dir", str(cache_home))
        for profile in inventory
        if profile.download_repo is not None
    )


def model_conversion_commands(
    inventory: tuple[ModelAssetProfile, ...],
    *,
    asset_home: Path,
) -> tuple[tuple[str, ...], ...]:
    commands: list[tuple[str, ...]] = []
    for profile in inventory:
        if not profile.can_convert:
            continue
        output = asset_home / "converted" / profile.profile_id
        commands.append(
            (
                sys.executable,
                "-m",
                "mlx_lm.convert",
                "--hf-path",
                profile.source_repo,
                "--mlx-path",
                str(output),
                "--q-bits",
                "4",
            ),
        )
    return tuple(commands)


def write_conversion_manifests(
    inventory: tuple[ModelAssetProfile, ...],
    *,
    asset_home: Path,
    commands: tuple[tuple[str, ...], ...],
    applied: bool,
) -> tuple[Path, ...]:
    manifest_dir = asset_home / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    command_by_source = {
        command[command.index("--hf-path") + 1]: list(command)
        for command in commands
        if "--hf-path" in command
    }
    paths: list[Path] = []
    for profile in inventory:
        manifest = {
            "schema": "capdep.model_asset_manifest.v1",
            "profile": profile.as_dict(),
            "applied": applied,
            "conversion_command": command_by_source.get(profile.source_repo),
            "source_revision": None,
            "source_file_hashes": {},
            "output_files": [],
            "fallback_runtime": profile.fallback_runtime,
        }
        path = manifest_dir / profile.manifest_name
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(path)
    return tuple(paths)


def conversion_readiness(profile_id: str, *, asset_home: Path | None = None) -> dict[str, Any]:
    home = model_asset_home(asset_home)
    inventory = {profile.profile_id: profile for profile in model_asset_inventory()}
    profile = inventory.get(profile_id)
    if profile is None:
        return {
            "profile_id": profile_id,
            "status": "unknown",
            "detail": "No model asset profile is registered for this runtime profile.",
            "manifest": None,
        }
    manifest = home / "manifests" / profile.manifest_name
    if profile.conversion_status in {
        "native_mlx_available",
        "native_mlx_vlm_available",
        "native_mflux_available",
        "runtime_native_no_conversion",
    }:
        status = "native"
    elif profile.conversion_status == "unsupported":
        status = "source_fallback"
    elif manifest.is_file():
        status = "converted"
    else:
        status = "missing_conversion"
    return {
        "profile_id": profile.profile_id,
        "status": status,
        "detail": profile.notes,
        "manifest": str(manifest) if manifest.is_file() else None,
        "source_repo": profile.source_repo,
        "recommended_runtime": profile.recommended_runtime,
        "conversion_status": profile.conversion_status,
        "fallback_runtime": profile.fallback_runtime,
    }
